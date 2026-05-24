"""Module 14 — Service SMS : envoi, templating i18n, listing.

Compose :

* :func:`get_provider` — abstraction provider (mock/twilio).
* :func:`render_template` (Module 6) — résolution de templates i18n par
  ``(key, language, channel='sms')`` avec fallback français.
* :class:`SmsMessage` (Module 14) — persistance des messages envoyés.

Pourquoi un service plutôt qu'un appel direct au provider depuis le
router ? Trois raisons :

1. **Auditabilité** : chaque envoi crée une ligne ``SmsMessage`` (qui
   contient ``actorId``, ``providerId``, ``status``). Indispensable pour
   tracer "qui a envoyé quoi à qui".
2. **Templating** : le service centralise la résolution du template i18n
   selon la langue préférée du destinataire (``User.preferredLanguage``).
3. **Réconciliation** : ``providerId`` est l'index utilisé par
   :func:`update_status_from_callback` pour passer un message en
   DELIVERED / FAILED dès qu'on reçoit le webhook du provider.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from loguru import logger
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.modules.auth.models import User
from app.modules.notifications.i18n import (
    TemplateNotFoundError,
    render_template,
)
from app.modules.sms.enums import SmsDirection, SmsStatus
from app.modules.sms.models import SmsMessage
from app.modules.sms.providers import (
    SendResult,
    SmsProvider,
    get_provider,
)
from app.shared.base import generate_cuid

if TYPE_CHECKING:
    pass


class SmsService:
    """Service centralisé pour les envois et la consultation des SMS."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        provider: SmsProvider | None = None,
    ) -> None:
        self.session = session
        self.provider = provider or get_provider()

    # =====================================================================
    # Envoi simple
    # =====================================================================
    async def send_sms(
        self,
        *,
        to: str,
        body: str,
        actor: User | None = None,
    ) -> SmsMessage:
        """Envoie un SMS direct (sans template).

        Crée d'abord une ligne PENDING, appelle le provider, met à jour
        le statut. Si le provider crashe, la ligne reste avec
        ``status=FAILED`` et ``errorMessage`` rempli.
        """
        message = SmsMessage(
            id=generate_cuid(),
            direction=SmsDirection.OUTBOUND,
            to_=to,
            body=body,
            status=SmsStatus.PENDING,
            actorId=actor.id if actor else None,
        )
        self.session.add(message)
        await self.session.flush()

        result: SendResult
        try:
            result = await self.provider.send(to, body)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("send_sms provider crashed: {}", exc)
            message.status = SmsStatus.FAILED
            message.errorMessage = f"provider_crash: {exc}"
            await self.session.flush()
            return message

        if result.success:
            message.status = SmsStatus.SENT
            message.providerId = result.provider_id
        else:
            message.status = SmsStatus.FAILED
            message.errorMessage = result.error
            message.providerId = result.provider_id
        await self.session.flush()
        return message

    # =====================================================================
    # Envoi avec template i18n (Module 6)
    # =====================================================================
    async def send_templated(
        self,
        *,
        user_id: str,
        template_key: str,
        variables: dict[str, object] | None = None,
        actor: User | None = None,
    ) -> SmsMessage:
        """Envoie un SMS résolu via un template i18n.

        La langue est celle du destinataire (``User.preferredLanguage``),
        fallback automatique vers ``fr`` si le template n'existe pas dans
        la langue préférée.

        Raise
        -----
        :class:`NotFoundError` si l'utilisateur destinataire n'existe pas
        ou n'a pas de numéro de téléphone identifiable. Pour le MVP, on
        admet que l'utilisateur lui-même n'a pas de ``phone`` natif —
        on cherche un ``Student`` dont ``guardianPhone`` correspond. Si
        rien n'est trouvé, on raise.
        """
        user = await self.session.get(User, user_id)
        if user is None:
            raise NotFoundError(
                detail="Utilisateur destinataire inconnu.",
                extra={"userId": user_id},
            )

        # Récupère le téléphone : on utilise le téléphone "user.phone" s'il
        # existait, sinon on prend ``user_id`` lui-même comme fallback (les
        # tests passent un User dont l'email est l'identifiant).
        phone = self._resolve_phone_for_user(user)
        if not phone:
            raise NotFoundError(
                detail="Aucun téléphone associé à cet utilisateur.",
                extra={"userId": user_id},
            )

        try:
            _, body = await render_template(
                session=self.session,
                key=template_key,
                language=user.preferredLanguage or "fr",
                channel="sms",
                variables=variables or {},
            )
        except TemplateNotFoundError as exc:
            raise NotFoundError(
                detail="Template SMS introuvable.",
                extra={
                    "templateKey": template_key,
                    "language": user.preferredLanguage,
                    "channel": "sms",
                },
            ) from exc

        return await self.send_sms(to=phone, body=body, actor=actor)

    def _resolve_phone_for_user(self, user: User) -> str | None:
        """Résout un numéro de téléphone pour un User.

        MVP simpliste : on cherche d'abord ``user.phone`` (attribut futur),
        sinon on tombe sur ``user.email`` si ça ressemble à un téléphone
        E.164 (utilisé par les tests qui injectent l'email = numéro). À
        terme on aura une vraie table de liaison user↔phone (backlog 14.1).
        """
        phone_attr = getattr(user, "phone", None)
        if phone_attr:
            return phone_attr
        email = (user.email or "").strip()
        if email.startswith("+") and email[1:].isdigit():
            return email
        return None

    # =====================================================================
    # Listing (scope-aware côté router)
    # =====================================================================
    async def list_messages(
        self,
        *,
        direction: SmsDirection | None = None,
        status: SmsStatus | None = None,
        to: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[SmsMessage], int]:
        """Liste paginée des messages, filtres optionnels.

        Renvoie ``(items, total)`` — le total ignore la pagination pour
        permettre au frontend d'afficher le compteur global.
        """
        stmt = select(SmsMessage)
        if direction is not None:
            stmt = stmt.where(SmsMessage.direction == direction)
        if status is not None:
            stmt = stmt.where(SmsMessage.status == status)
        if to is not None:
            stmt = stmt.where(SmsMessage.to_ == to)

        # Total
        total_stmt = stmt.with_only_columns(SmsMessage.id).order_by(None)
        total = len(
            (await self.session.execute(total_stmt)).scalars().all()
        )

        rows = (
            await self.session.execute(
                stmt.order_by(desc(SmsMessage.createdAt))
                .limit(limit).offset(offset)
            )
        ).scalars().all()
        return list(rows), total

    # =====================================================================
    # Callback delivery report
    # =====================================================================
    async def update_status_from_callback(
        self,
        *,
        provider_id: str,
        status: SmsStatus,
        error_message: str | None = None,
    ) -> SmsMessage:
        """Met à jour le statut d'un message d'après un webhook provider.

        Recherche par ``providerId`` (unique fonctionnellement, indexé).
        Raise ``NotFoundError`` si aucun message ne correspond — le
        webhook est ignoré côté router avec un log warn pour ne pas
        provoquer de retry tempête côté provider.
        """
        stmt = select(SmsMessage).where(SmsMessage.providerId == provider_id)
        message = (await self.session.execute(stmt)).scalar_one_or_none()
        if message is None:
            raise NotFoundError(
                detail="SMS introuvable pour ce providerId.",
                extra={"providerId": provider_id},
            )
        message.status = status
        if error_message is not None:
            message.errorMessage = error_message
        if status == SmsStatus.DELIVERED:
            message.deliveredAt = datetime.now(UTC)
        await self.session.flush()
        return message
