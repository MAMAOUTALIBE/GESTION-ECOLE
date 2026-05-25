"""Module 5C — Décorateur ``@audit_pii_access`` pour endpoints FastAPI.

Usage type sur un endpoint qui retourne UNE entité :

.. code-block:: python

    from app.modules.pii_audit.decorators import audit_pii_access
    from app.modules.pii_audit.enums import PiiAccessType, PiiEntityType

    @router.get("/students/{student_id}")
    @audit_pii_access(
        entity_type=PiiEntityType.STUDENT,
        access_type=PiiAccessType.VIEW,
        get_entity_id=lambda kwargs: kwargs["student_id"],
    )
    async def get_student(
        student_id: str, user: CurrentUserDep, service: CensusSvc, request: Request,
    ) -> StudentRead:
        ...

L'endpoint DOIT déclarer ``request: Request`` et ``user`` (annoté
``Depends(get_current_user)``) dans sa signature — c'est ainsi qu'on
récupère l'acteur + l'IP + l'UA.

Implémentation
--------------
On exécute la fonction d'abord, puis on tente l'audit via
``asyncio.create_task`` pour ne PAS bloquer la réponse HTTP. Si l'audit
échoue (Redis / DB indispo), le service interne logue via loguru et
swallow ; le client ne voit aucune erreur.

Cas particulier : tests intégration utilisent une session
transactionnelle dont le ``rollback()`` final invalide les rows
détachées d'une autre tâche asyncio. Pour rester déterministe en
test, le décorateur attend la fin de l'audit avant de retourner si
``await_audit=True`` (utilisé par les tests).
"""
from __future__ import annotations

import asyncio
import functools
import os
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from fastapi import Request
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.pii_audit.enums import PiiAccessType, PiiEntityType
from app.modules.pii_audit.service import PiiAuditService

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])

# Set qui garde une référence forte aux audit-tasks de fond pour
# empêcher leur ramassage prématuré (RUF006 — sinon ``asyncio`` peut
# annuler la task tant que le résultat n'est pas collecté).
_BG_TASKS: set[asyncio.Task[Any]] = set()


def _resolve_actor(kwargs: dict[str, Any]) -> User | None:
    """Récupère le ``User`` parmi les kwargs (clé conventionnelle ``user``)."""
    actor = kwargs.get("user") or kwargs.get("current_user")
    return actor if isinstance(actor, User) else None


def _resolve_request(kwargs: dict[str, Any]) -> Request | None:
    """Récupère le ``Request`` parmi les kwargs (clé ``request``)."""
    req = kwargs.get("request")
    return req if isinstance(req, Request) else None


def _resolve_session(kwargs: dict[str, Any]) -> AsyncSession | None:
    """Récupère la session SQLAlchemy parmi les kwargs (``session`` / ``svc.session``)."""
    sess = kwargs.get("session")
    if isinstance(sess, AsyncSession):
        return sess
    # Beaucoup d'endpoints exposent un service ``svc`` au lieu de la
    # session brute. On accepte tout objet qui porte ``.session``.
    for candidate_key in ("service", "svc", "census_service"):
        svc = kwargs.get(candidate_key)
        if svc is not None and hasattr(svc, "session"):
            inner = svc.session
            if isinstance(inner, AsyncSession):
                return inner
    return None


def audit_pii_access(
    *,
    entity_type: PiiEntityType,
    access_type: PiiAccessType,
    get_entity_id: Callable[[dict[str, Any]], str],
    endpoint_label: str | None = None,
    await_audit: bool | None = None,
) -> Callable[[F], F]:
    """Décorateur d'endpoint qui consigne l'accès en best-effort.

    Args:
        entity_type: type de l'entité PII consultée.
        access_type: VIEW / LIST / EXPORT.
        get_entity_id: fonction prenant les kwargs de l'endpoint et
            retournant la string ``entityId`` à consigner. Pour les
            LIST où on n'a pas l'id à l'avance (dépend du retour), on
            peut passer ``lambda _: "*"`` et consigner un row agrégé.
        endpoint_label: override du libellé d'endpoint (par défaut on
            prend ``request.url.path`` si disponible, sinon le nom
            qualifié de la fonction).
        await_audit: forcer l'attente de l'audit avant retour (utile
            en test). Par défaut on suit la variable
            ``PII_AUDIT_AWAIT`` (1 = attendre, sinon ``create_task``).
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = await func(*args, **kwargs)

            try:
                actor = _resolve_actor(kwargs)
                request = _resolve_request(kwargs)
                session = _resolve_session(kwargs)
                if session is None:
                    # Pas de session disponible — on log + on sort.
                    # Aucune ligne d'audit n'est insérable.
                    logger.warning(
                        "pii_audit: no AsyncSession in kwargs of {} — skipping",
                        getattr(func, "__qualname__", "<anon>"),
                    )
                    return result

                try:
                    entity_id = str(get_entity_id(kwargs))
                except Exception as exc:
                    logger.warning(
                        "pii_audit: get_entity_id failed for {}: {}",
                        getattr(func, "__qualname__", "<anon>"),
                        exc,
                    )
                    return result

                endpoint_used = endpoint_label or (
                    request.url.path
                    if request is not None
                    else getattr(func, "__qualname__", "endpoint")
                )

                service = PiiAuditService(session)

                async def _run_audit() -> None:
                    await service.log_access(
                        actor=actor,
                        entity_type=entity_type,
                        entity_id=entity_id,
                        access_type=access_type,
                        endpoint=endpoint_used,
                        request=request,
                    )

                should_await = (
                    await_audit
                    if await_audit is not None
                    else os.getenv("PII_AUDIT_AWAIT", "0") == "1"
                )
                if should_await:
                    await _run_audit()
                else:
                    # Best-effort en arrière-plan — on ne bloque pas la
                    # réponse HTTP. Si l'event loop est en cours de
                    # shutdown, on retombe sur l'attente synchrone.
                    try:
                        # On stocke la référence pour empêcher le GC de
                        # tuer la task avant son exécution (RUF006).
                        _bg_task = asyncio.create_task(_run_audit())
                        _BG_TASKS.add(_bg_task)
                        _bg_task.add_done_callback(_BG_TASKS.discard)
                    except RuntimeError:  # pragma: no cover - safety net
                        await _run_audit()
            except Exception as exc:
                # Filet de sécurité ultime : l'audit ne casse jamais le flux.
                logger.warning("pii_audit: decorator failed: {}", exc)

            return result

        return wrapper  # type: ignore[return-value]

    return decorator


__all__ = ["audit_pii_access"]
