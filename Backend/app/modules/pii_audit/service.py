"""Module 5C — Service d'audit PII.

Responsabilités :

* ``log_access`` — insère UNE ligne d'audit. Best-effort : capture
  toute exception, ne casse JAMAIS le flux principal.
* ``log_bulk_list`` — insère N lignes (1 par entityId) si ``len <
  BULK_LIST_AGGREGATION_THRESHOLD``, sinon UNE seule ligne agrégée
  (``entityId="*"`` + ``metadataJson={"count": N}``).
* ``list_accesses`` — listing filtrable, RBAC scope appliqué.
* ``get_history_for_entity`` — historique des accès sur UNE entité
  (NATIONAL / MINISTRY only).
* ``purge_old_logs`` — DELETE batch (NATIONAL_ADMIN only).

Conventions techniques :

* L'IP est résolue via ``app.core.proxy.client_ip`` (compat XFF
  Module 1.1 C-4).
* Le user-agent est tronqué + assaini comme dans Module 1.1 H-3.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from fastapi import Request
from loguru import logger
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError
from app.core.proxy import client_ip
from app.modules.auth.models import User
from app.modules.pii_audit.enums import (
    BULK_LIST_AGGREGATION_THRESHOLD,
    PiiAccessType,
    PiiEntityType,
)
from app.modules.pii_audit.models import PiiAccessLog
from app.modules.pii_audit.schemas import (
    PiiAccessLogEntry,
    PiiAccessLogFilters,
)
from app.shared.enums import UserRole

# Caps copiés du Module 1.1 H-3 — défense en profondeur (le schéma
# Postgres a déjà un VARCHAR(512) sur userAgent, mais on coupe + on
# enlève les caractères de contrôle pour éviter l'injection dans les
# log shippers).
_USER_AGENT_MAX = 512
_ENDPOINT_MAX = 200
_REQUEST_ID_MAX = 60
_CONTROL_CHARS = "".join(
    chr(c) for c in range(0x00, 0x20) if c != 0x09
) + "\x7f"


def _sanitize(value: str | None, max_length: int) -> str | None:
    """Tronque + retire les caractères de contrôle (defense in depth)."""
    if value is None:
        return None
    cleaned = value.translate({ord(c): None for c in _CONTROL_CHARS})
    return cleaned[:max_length]


def _admins() -> frozenset[UserRole]:
    """Renvoie le set des rôles "admin national" — NATIONAL + MINISTRY."""
    return frozenset({UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN})


class PiiAuditService:
    """Service stateless ; une instance par requête via ``Depends``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ----------------------------------------------------------------
    # WRITE — toujours best-effort, jamais bloquant
    # ----------------------------------------------------------------
    async def log_access(
        self,
        *,
        actor: User | None,
        entity_type: PiiEntityType,
        entity_id: str,
        access_type: PiiAccessType,
        endpoint: str,
        request: Request | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insère un row d'audit. Capture toute exception en interne.

        Si ``actor`` est ``None`` (rare, mais possible si le décorateur
        est posé sur un endpoint PUBLIC qui révèle quand même de la PII
        — ex. ``GET /api/diplomas/verify/{serial}``), la ligne est tout
        de même persistée avec ``userId=NULL`` + ``userRole=NULL``.
        """
        try:
            ip_addr: str | None = None
            ua: str | None = None
            req_id: str | None = None
            if request is not None:
                ip_addr = client_ip(request)
                ua = request.headers.get("user-agent")
                req_id = getattr(request.state, "request_id", None) or (
                    request.headers.get("x-request-id")
                )

            row = PiiAccessLog(
                userId=actor.id if actor is not None else None,
                userRole=(
                    actor.role.value if (actor is not None) else None
                ),
                entityType=entity_type,
                entityId=entity_id[:30] if entity_id else "*",
                accessType=access_type,
                endpoint=_sanitize(endpoint, _ENDPOINT_MAX) or "",
                ip=ip_addr,
                userAgent=_sanitize(ua, _USER_AGENT_MAX),
                requestId=_sanitize(req_id, _REQUEST_ID_MAX),
                metadataJson=metadata,
            )
            self.session.add(row)
            await self.session.flush()
        except Exception as exc:
            # Auditing must NEVER break the user-facing flow. We log it
            # via loguru — ops will see it in Loki — but we swallow.
            logger.warning(
                "pii_audit: log_access failed (entity={}/{} accessType={}): {}",
                entity_type,
                entity_id,
                access_type,
                exc,
            )

    async def log_bulk_list(
        self,
        *,
        actor: User | None,
        entity_type: PiiEntityType,
        entity_ids: Iterable[str],
        endpoint: str,
        request: Request | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Audite une opération de listing.

        * <= ``BULK_LIST_AGGREGATION_THRESHOLD`` (50) entités :
          insère **N rows** (une par entité visible).
        * > 50 entités : insère **UN seul row** agrégé
          (``entityId="*"``, ``metadataJson={"count": N}``) pour éviter
          de noyer la table en cas de listing massif.
        """
        ids = [eid for eid in entity_ids if eid]
        count = len(ids)
        if count == 0:
            # Rien à logger — mais on enregistre quand même une trace
            # "LIST vide" pour répondre à "qui a regardé sans rien
            # voir ?". Ce row est borné à 1 par appel donc négligeable.
            await self.log_access(
                actor=actor,
                entity_type=entity_type,
                entity_id="*",
                access_type=PiiAccessType.LIST,
                endpoint=endpoint,
                request=request,
                metadata={"count": 0, **(extra_metadata or {})},
            )
            return

        if count > BULK_LIST_AGGREGATION_THRESHOLD:
            meta = {"count": count, **(extra_metadata or {})}
            await self.log_access(
                actor=actor,
                entity_type=entity_type,
                entity_id="*",
                access_type=PiiAccessType.LIST,
                endpoint=endpoint,
                request=request,
                metadata=meta,
            )
            return

        # <= 50 : on log une ligne par entité.
        for eid in ids:
            await self.log_access(
                actor=actor,
                entity_type=entity_type,
                entity_id=eid,
                access_type=PiiAccessType.LIST,
                endpoint=endpoint,
                request=request,
                metadata=extra_metadata,
            )

    # ----------------------------------------------------------------
    # READ
    # ----------------------------------------------------------------
    async def list_accesses(
        self,
        filters: PiiAccessLogFilters,
        actor: User,
    ) -> list[PiiAccessLogEntry]:
        """Lecture filtrée, scopée RBAC.

        Matrice :

        * NATIONAL_ADMIN / MINISTRY_ADMIN : voit toutes les lignes.
        * REGIONAL_ADMIN / INSPECTOR / PREFECTURE_ADMIN /
          SUB_PREFECTURE_ADMIN : voit toutes les lignes *de leur scope*
          — on n'a pas la donnée géographique sur ``PiiAccessLog`` (la
          fiche élève peut bouger), donc dans ce MVP on les borne aux
          accès qu'ils ont eux-mêmes effectués. Doc → backlog 5C.1.
        * Autres rôles : seulement leurs propres accès.
        """
        stmt = select(PiiAccessLog).order_by(
            PiiAccessLog.accessedAt.desc()
        )

        if actor.role in _admins():
            # Admins nationaux peuvent passer ``userId`` pour filtrer
            # un autre user. Sinon ils voient tout.
            if filters.userId:
                stmt = stmt.where(PiiAccessLog.userId == filters.userId)
        else:
            # Non-admin : on force le filtre user.id, on ignore le
            # paramètre userId s'il a été passé (jamais faire confiance
            # au client).
            stmt = stmt.where(PiiAccessLog.userId == actor.id)

        if filters.entityType:
            stmt = stmt.where(PiiAccessLog.entityType == filters.entityType)
        if filters.entityId:
            stmt = stmt.where(PiiAccessLog.entityId == filters.entityId)
        if filters.accessType:
            stmt = stmt.where(PiiAccessLog.accessType == filters.accessType)
        if filters.fromDate:
            stmt = stmt.where(PiiAccessLog.accessedAt >= filters.fromDate)
        if filters.toDate:
            stmt = stmt.where(PiiAccessLog.accessedAt <= filters.toDate)

        stmt = stmt.limit(filters.limit).offset(filters.offset)

        rows = (await self.session.execute(stmt)).scalars().all()
        return [PiiAccessLogEntry.model_validate(r) for r in rows]

    async def get_history_for_entity(
        self,
        entity_type: PiiEntityType,
        entity_id: str,
        actor: User,
        *,
        limit: int = 200,
    ) -> list[PiiAccessLogEntry]:
        """Historique des accès à UNE entité.

        Réservé aux admins nationaux : c'est la réponse à une demande
        formelle "qui a consulté ?" et ça révèle qui dans
        l'administration a touché à la donnée.
        """
        if actor.role not in _admins():
            raise ForbiddenError(
                detail=(
                    "Seuls NATIONAL_ADMIN / MINISTRY_ADMIN peuvent "
                    "consulter l'historique d'audit PII d'une entité."
                )
            )

        stmt = (
            select(PiiAccessLog)
            .where(
                and_(
                    PiiAccessLog.entityType == entity_type,
                    PiiAccessLog.entityId == entity_id,
                )
            )
            .order_by(PiiAccessLog.accessedAt.desc())
            .limit(max(1, min(int(limit), 500)))
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [PiiAccessLogEntry.model_validate(r) for r in rows]

    async def purge_old_logs(
        self,
        cutoff_date: datetime,
        actor: User,
    ) -> int:
        """Supprime les lignes dont ``accessedAt < cutoff_date``.

        Réservé NATIONAL_ADMIN. Renvoie le nombre de lignes supprimées.
        """
        if actor.role != UserRole.NATIONAL_ADMIN:
            raise ForbiddenError(
                detail="Seul NATIONAL_ADMIN peut purger l'audit PII.",
            )

        stmt = delete(PiiAccessLog).where(
            PiiAccessLog.accessedAt < cutoff_date
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return int(result.rowcount or 0)


__all__ = ["PiiAuditService"]
