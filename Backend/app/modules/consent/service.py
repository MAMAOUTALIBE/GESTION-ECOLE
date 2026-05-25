"""Module 5B — Service du consentement utilisateur.

Responsabilités :

* ``get_status`` — calcule l'état du consentement pour l'utilisateur
  courant (a-t-il consenti, à quelle version, doit-il reconsentir).
* ``accept``    — upsert ``UserConsent`` + maj ``User.consentVersion``
  + capture IP/UA pour la preuve + audit best-effort dans
  ``PiiAccessLog``.

Aucune vérification de rôle : tout utilisateur authentifié peut
consulter son propre statut et consentir. Le RBAC du router est volontairement
neutre (``get_current_user`` suffit).
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Request
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.core.proxy import client_ip
from app.modules.auth.models import User
from app.modules.consent.enums import CURRENT_CONSENT_VERSION
from app.modules.consent.models import UserConsent
from app.modules.consent.schemas import AcceptConsentRequest, ConsentStatus
from app.shared.base import generate_cuid

# Caps copiés de pii_audit (defense in depth — la colonne DB borne déjà
# à VARCHAR(512), on tronque + on retire les caractères de contrôle
# pour éviter l'injection dans les log shippers).
_USER_AGENT_MAX = 512
_IP_MAX = 45
_CONTROL_CHARS = "".join(
    chr(c) for c in range(0x00, 0x20) if c != 0x09
) + "\x7f"


def _sanitize(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    cleaned = value.translate({ord(c): None for c in _CONTROL_CHARS})
    return cleaned[:max_length] or None


class ConsentService:
    """Service stateless ; une instance par requête via ``Depends``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ==================================================================
    # READ
    # ==================================================================
    async def get_status(self, user: User) -> ConsentStatus:
        """Calcule l'état du consentement pour ``user``.

        ``needsAcceptance`` est vrai si :
        * aucun ``UserConsent`` n'existe pour cet utilisateur, OU
        * la version acceptée est strictement antérieure à
          ``CURRENT_CONSENT_VERSION`` (compare lexicographique sur
          des dates ISO ``YYYY-MM-DD`` — robuste sans parsing).
        """
        row = (
            await self.session.execute(
                select(UserConsent).where(UserConsent.userId == user.id)
            )
        ).scalars().one_or_none()

        if row is None:
            return ConsentStatus(
                version=None,
                acceptedAt=None,
                needsAcceptance=True,
                currentRequiredVersion=CURRENT_CONSENT_VERSION,
            )

        needs = row.consentVersion < CURRENT_CONSENT_VERSION
        return ConsentStatus(
            version=row.consentVersion,
            acceptedAt=row.acceptedAt,
            needsAcceptance=needs,
            currentRequiredVersion=CURRENT_CONSENT_VERSION,
        )

    # ==================================================================
    # WRITE
    # ==================================================================
    async def accept(
        self,
        user: User,
        dto: AcceptConsentRequest,
        request: Request | None = None,
    ) -> ConsentStatus:
        """Persiste l'acceptation (upsert sur userId).

        Refus si ``dto.consentVersion`` ne correspond pas à la version
        actuellement requise (cas client legacy qui acquiesce à un
        document obsolète — on veut un acte de consentement sur la
        version COURANTE).
        """
        if dto.consentVersion != CURRENT_CONSENT_VERSION:
            raise ConflictError(
                detail=(
                    "La version du consentement envoyée ne correspond pas "
                    "à la version actuellement requise par la plateforme."
                ),
                extra={
                    "submittedVersion": dto.consentVersion,
                    "currentRequiredVersion": CURRENT_CONSENT_VERSION,
                },
            )

        # Capture preuve (IP + user-agent). Tolère request=None pour
        # les tests unitaires service-only.
        ip_addr: str | None = None
        ua: str | None = None
        if request is not None:
            try:
                ip_addr = _sanitize(client_ip(request), _IP_MAX)
            except Exception:  # pragma: no cover - defensive
                ip_addr = None
            ua = _sanitize(request.headers.get("user-agent"), _USER_AGENT_MAX)

        now = datetime.now(UTC)

        # Upsert manuel (UNIQUE userId — SQLite + Postgres OK).
        existing = (
            await self.session.execute(
                select(UserConsent).where(UserConsent.userId == user.id)
            )
        ).scalars().one_or_none()

        if existing is None:
            row = UserConsent(
                id=generate_cuid(),
                userId=user.id,
                consentVersion=dto.consentVersion,
                acceptedAt=now,
                ip=ip_addr,
                userAgent=ua,
            )
            self.session.add(row)
        else:
            existing.consentVersion = dto.consentVersion
            existing.acceptedAt = now
            existing.ip = ip_addr
            existing.userAgent = ua
            row = existing

        # MAJ cache dénormalisé sur User
        user.consentVersion = dto.consentVersion
        await self.session.flush()

        # Audit applicatif via loguru — l'événement reste trouvable dans
        # les pipelines Loki/Sentry. On évite d'écrire dans PiiAccessLog
        # car l'enum DB ``PiiEntityType`` n'expose pas ``USER`` (et étendre
        # un enum natif Postgres demanderait une migration dédiée). Le
        # cache ``User.consentVersion`` + ``UserConsent.acceptedAt``/
        # ``ip``/``userAgent`` suffisent à prouver l'acte.
        logger.info(
            "consent.accepted userId={} version={} ip={}",
            user.id,
            dto.consentVersion,
            ip_addr or "-",
        )

        return ConsentStatus(
            version=row.consentVersion,
            acceptedAt=row.acceptedAt,
            needsAcceptance=False,
            currentRequiredVersion=CURRENT_CONSENT_VERSION,
        )


__all__ = ["ConsentService"]
