"""Module 18 — Portail parent : service applicatif.

Compose :

* :func:`intent_parser.parse_intent` — détection d'intention.
* :class:`ParentSession` + :class:`WhatsAppMessage` — journal append-only.
* :class:`Student` (Module 2) — résolution parent → enfant via
  ``guardianPhone`` ou ``Parent.phone`` (Module 4).
* :class:`ReportCard` (Module 4) — dernière moyenne.
* :func:`get_provider` (Module 14 SMS) — abstraction conservée pour le
  fallback SMS (utilisée par les agents en cas de WhatsApp indispo).

Notes d'implémentation
----------------------
* On HASH systématiquement le numéro en SHA-256 hex (64 chars) avant de
  le stocker dans ``ParentSession`` et de l'exposer dans une URL. Le
  numéro brut reste dans ``WhatsAppMessage.phoneNumber`` (journal
  technique audit, jamais exposé via une URL publique).
* L'expiration de session est de 30 min — alignée avec USSD/WhatsApp
  standards : on bumpe ``lastActivityAt`` à chaque hit du même numéro.
* La page publique HTML est anonymisée : on n'expose que les INITIALES
  + classe + dernière moyenne. Pas de nom complet, pas de photo, pas
  de date de naissance.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from loguru import logger
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.academics.models import Parent, ReportCard, StudentParent
from app.modules.census.models import Student
from app.modules.census.normalization import normalize_phone_guinea
from app.modules.parent_portal.enums import (
    ParentChannel,
    ParentIntent,
    WhatsAppDirection,
    WhatsAppStatus,
)
from app.modules.parent_portal.intent_parser import parse_intent
from app.modules.parent_portal.models import ParentSession, WhatsAppMessage
from app.modules.parent_portal.schemas import (
    ChildSummary,
    ParentOverview,
    WhatsAppReplyOut,
)
from app.shared.base import generate_cuid

if TYPE_CHECKING:
    pass


SESSION_TTL: Final = timedelta(minutes=30)


def hash_phone(phone: str) -> str:
    """SHA-256 hex (64 chars) — identifiant pseudonyme stable d'un numéro.

    On normalise toujours avant pour que ``+224622112233`` et
    ``622112233`` produisent le même hash.
    """
    try:
        normalized = normalize_phone_guinea(phone)
    except ValueError:
        # fallback: on hash la forme brute pour ne JAMAIS crasher côté
        # webhook (un numéro étranger / format inconnu reste consultable).
        normalized = phone.strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _safe_normalize(phone: str) -> str | None:
    try:
        return normalize_phone_guinea(phone)
    except ValueError:
        return None


def _initials(first_name: str, last_name: str) -> str:
    """ "Aissatou Diallo" → "A.D." (toujours upper, fallback "?")."""
    a = (first_name or "").strip()[:1].upper() or "?"
    b = (last_name or "").strip()[:1].upper() or "?"
    return f"{a}.{b}."


# Texte de l'événement à venir affiché en pied de page de la vue parent.
# Volontairement statique pour le MVP — un futur Module "calendrier scolaire"
# branchera une vraie requête (backlog 18.3).
DEFAULT_UPCOMING_EVENT: Final = (
    "Prochain conseil de classe : consultez l'ecole pour la date."
)


class ParentPortalService:
    """Service applicatif du portail parent."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # =====================================================================
    # Résolution parent → enfants
    # =====================================================================
    async def _find_students_for_phone(self, phone: str) -> list[Student]:
        """Cherche les ``Student`` rattachés à ``phone``.

        Deux chemins :
        1. ``Student.guardianPhone`` (rapide, schéma legacy Module 2).
        2. ``Parent.phone`` → ``StudentParent.studentId`` (Module 4).

        On déduplique sur ``Student.id`` au cas où les deux schémas
        pointent vers le même élève.
        """
        normalized = _safe_normalize(phone)
        if normalized is None:
            return []

        # 1) Via guardianPhone direct
        stmt_guardian = select(Student).where(
            Student.guardianPhone == normalized
        )
        guardian_rows = list(
            (await self.session.execute(stmt_guardian)).scalars().all()
        )

        # 2) Via Parent.phone → StudentParent → Student
        stmt_via_parent = (
            select(Student)
            .join(StudentParent, StudentParent.studentId == Student.id)
            .join(Parent, Parent.id == StudentParent.parentId)
            .where(Parent.phone == normalized)
        )
        parent_rows = list(
            (await self.session.execute(stmt_via_parent)).scalars().all()
        )

        seen: set[str] = set()
        merged: list[Student] = []
        for s in (*guardian_rows, *parent_rows):
            if s.id in seen:
                continue
            seen.add(s.id)
            merged.append(s)
        return merged

    async def _last_average(self, student_id: str) -> float | None:
        """Renvoie la dernière moyenne disponible (ou ``None``)."""
        stmt = (
            select(ReportCard)
            .where(ReportCard.studentId == student_id)
            .where(ReportCard.average.is_not(None))
            .order_by(desc(ReportCard.updatedAt))
            .limit(1)
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return float(row.average) if (row and row.average is not None) else None

    # =====================================================================
    # Vue parent (utilisée par JSON + HTML)
    # =====================================================================
    async def get_parent_overview(self, phone_hash: str) -> ParentOverview:
        """Vue agrégée parent à partir d'un hash de numéro.

        On n'expose que les INITIALES + classe + dernière moyenne. Si
        aucun enfant n'est rattaché, ``childrenCount=0`` et la liste est
        vide. Volontairement on ne raise PAS pour ne pas révéler si un
        numéro est connu (timing/oracle).
        """
        # On ne sait pas dé-hasher : on parcourt les students dont le
        # guardianPhone hash matche. Pour le MVP, on calcule le hash de
        # chaque guardianPhone non-null. C'est O(N) — acceptable pour les
        # volumes parent (<5k par école). Un index dédié sur le hash sera
        # ajouté au backlog 18.4 (colonne précalculée).
        stmt = select(Student).where(Student.guardianPhone.is_not(None))
        all_students = list(
            (await self.session.execute(stmt)).scalars().all()
        )
        matches: list[Student] = []
        for s in all_students:
            normalized = _safe_normalize(s.guardianPhone or "")
            if normalized is None:
                continue
            if hashlib.sha256(normalized.encode("utf-8")).hexdigest() == phone_hash:
                matches.append(s)

        # Côté Parent.phone (Module 4)
        parent_stmt = (
            select(Student)
            .join(StudentParent, StudentParent.studentId == Student.id)
            .join(Parent, Parent.id == StudentParent.parentId)
        )
        parents_rows = list(
            (await self.session.execute(parent_stmt)).scalars().all()
        )
        # Filtrage in-memory pour comparer le hash
        parent_stmt_phones = select(Parent.id, Parent.phone)
        parent_phones_rows = list(
            (await self.session.execute(parent_stmt_phones)).all()
        )
        matching_parent_ids: set[str] = set()
        for parent_id, parent_phone in parent_phones_rows:
            if not parent_phone:
                continue
            normalized = _safe_normalize(parent_phone)
            if normalized is None:
                continue
            if hashlib.sha256(normalized.encode("utf-8")).hexdigest() == phone_hash:
                matching_parent_ids.add(parent_id)

        # Récupère StudentParent → Student pour les parents matchants
        if matching_parent_ids:
            sp_stmt = (
                select(Student)
                .join(StudentParent, StudentParent.studentId == Student.id)
                .where(StudentParent.parentId.in_(matching_parent_ids))
            )
            for s in (await self.session.execute(sp_stmt)).scalars().all():
                if s.id not in {m.id for m in matches}:
                    matches.append(s)
        # parents_rows était juste pour valider la jointure
        _ = parents_rows

        # Build children summaries
        children: list[ChildSummary] = []
        for s in matches:
            last_avg = await self._last_average(s.id)
            class_name = None
            if s.classRoomId is not None:
                from app.modules.schools.models import ClassRoom
                cls = await self.session.get(ClassRoom, s.classRoomId)
                if cls is not None:
                    class_name = cls.name
            children.append(
                ChildSummary(
                    initials=_initials(s.firstName, s.lastName),
                    className=class_name,
                    lastAverage=last_avg,
                )
            )

        return ParentOverview(
            phoneHash=phone_hash,
            childrenCount=len(children),
            children=children,
            upcomingEventNote=DEFAULT_UPCOMING_EVENT,
        )

    # =====================================================================
    # Session management
    # =====================================================================
    async def _touch_session(
        self,
        *,
        phone: str,
        channel: ParentChannel,
    ) -> ParentSession:
        """Crée ou met à jour la ParentSession en cours (TTL 30 min)."""
        phone_hash = hash_phone(phone)
        now = datetime.now(UTC)

        stmt = (
            select(ParentSession)
            .where(ParentSession.phoneNumberHash == phone_hash)
            .where(ParentSession.channel == channel)
            .where(ParentSession.expiresAt > now)
            .order_by(desc(ParentSession.lastActivityAt))
            .limit(1)
        )
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            existing.lastActivityAt = now
            existing.expiresAt = now + SESSION_TTL
            await self.session.flush()
            return existing

        session_row = ParentSession(
            id=generate_cuid(),
            phoneNumberHash=phone_hash,
            channel=channel,
            startedAt=now,
            lastActivityAt=now,
            expiresAt=now + SESSION_TTL,
        )
        self.session.add(session_row)
        await self.session.flush()
        return session_row

    async def find_active_session(
        self,
        *,
        phone: str,
        channel: ParentChannel,
    ) -> ParentSession | None:
        """Retourne la session active (non expirée) pour ce numéro/canal."""
        phone_hash = hash_phone(phone)
        now = datetime.now(UTC)
        stmt = (
            select(ParentSession)
            .where(ParentSession.phoneNumberHash == phone_hash)
            .where(ParentSession.channel == channel)
            .where(ParentSession.expiresAt > now)
            .order_by(desc(ParentSession.lastActivityAt))
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # =====================================================================
    # Handler WhatsApp
    # =====================================================================
    async def handle_whatsapp_message(
        self,
        *,
        phone_number: str,
        body: str,
        message_id: str,
    ) -> WhatsAppReplyOut:
        """Reçoit un message WhatsApp INBOUND et calcule la réponse.

        1. Journalise le message en DB (idempotency via ``messageId`` UNIQUE).
        2. Crée/bump la ``ParentSession`` WHATSAPP.
        3. Parse l'intent et appelle le builder de réponse.
        4. Persiste un second ``WhatsAppMessage`` OUTBOUND avec la réponse.

        Anti-replay : si on reçoit deux fois le même ``messageId``, on
        renvoie la réponse précédemment calculée sans la rejouer côté
        provider (idempotent — important pour les retries Cloud API).
        """
        # ---- 1. Idempotency check ----
        existing_stmt = select(WhatsAppMessage).where(
            WhatsAppMessage.messageId == message_id,
            WhatsAppMessage.direction == WhatsAppDirection.INBOUND,
        )
        existing = (
            await self.session.execute(existing_stmt)
        ).scalar_one_or_none()
        if existing is not None and existing.status != WhatsAppStatus.RECEIVED:
            # Déjà traité → on cherche la réponse OUTBOUND associée
            out_stmt = select(WhatsAppMessage).where(
                WhatsAppMessage.phoneNumber == phone_number,
                WhatsAppMessage.direction == WhatsAppDirection.OUTBOUND,
                WhatsAppMessage.receivedAt >= existing.receivedAt,
            ).order_by(WhatsAppMessage.receivedAt).limit(1)
            out_row = (await self.session.execute(out_stmt)).scalar_one_or_none()
            intent = parse_intent(body)
            return WhatsAppReplyOut(
                messageId=message_id,
                intent=intent.value,
                reply=out_row.body if out_row else "(deja traite)",
                status=existing.status,
            )

        # ---- 2. Persist INBOUND ----
        if existing is None:
            inbound = WhatsAppMessage(
                id=generate_cuid(),
                direction=WhatsAppDirection.INBOUND,
                phoneNumber=phone_number,
                messageId=message_id,
                body=body,
                status=WhatsAppStatus.RECEIVED,
                receivedAt=datetime.now(UTC),
            )
            self.session.add(inbound)
            await self.session.flush()
        else:
            inbound = existing

        # ---- 3. Session ----
        await self._touch_session(
            phone=phone_number, channel=ParentChannel.WHATSAPP,
        )

        # ---- 4. Intent + réponse ----
        intent = parse_intent(body)
        reply_body = await self._build_reply(
            phone=phone_number, intent=intent,
        )

        # ---- 5. Persist OUTBOUND ----
        # On préfixe le messageId outbound pour rester unique.
        outbound_id = f"out-{message_id}"
        existing_out_stmt = select(WhatsAppMessage).where(
            WhatsAppMessage.messageId == outbound_id,
        )
        if (await self.session.execute(existing_out_stmt)).scalar_one_or_none() is None:
            outbound = WhatsAppMessage(
                id=generate_cuid(),
                direction=WhatsAppDirection.OUTBOUND,
                phoneNumber=phone_number,
                messageId=outbound_id,
                body=reply_body,
                status=WhatsAppStatus.SENT,
                receivedAt=datetime.now(UTC),
                processedAt=datetime.now(UTC),
            )
            self.session.add(outbound)
            await self.session.flush()

        # ---- 6. Mark INBOUND processed ----
        inbound.status = WhatsAppStatus.PROCESSED
        inbound.processedAt = datetime.now(UTC)
        await self.session.flush()

        return WhatsAppReplyOut(
            messageId=message_id,
            intent=intent.value,
            reply=reply_body,
            status=WhatsAppStatus.PROCESSED,
        )

    async def _build_reply(
        self, *, phone: str, intent: ParentIntent,
    ) -> str:
        """Construit la réponse textuelle adaptée à l'intent.

        Si le numéro est inconnu (aucun enfant rattaché), on bascule en
        message d'aide avec une invite à contacter l'école. On ne révèle
        JAMAIS la liste des intents disponibles à un numéro inconnu (le
        comportement reste le même que pour AIDE).
        """
        students = await self._find_students_for_phone(phone)
        if not students:
            return (
                "Numero non reconnu. Contactez votre ecole pour rattacher "
                "votre numero a votre enfant."
            )

        if intent == ParentIntent.MOYENNE:
            return await self._reply_moyenne(students)
        if intent == ParentIntent.PRESENCE:
            return await self._reply_presence(students)
        if intent == ParentIntent.BULLETIN:
            return await self._reply_bulletin(students)
        if intent == ParentIntent.EVENEMENT:
            return f"Evenement: {DEFAULT_UPCOMING_EVENT}"
        # AIDE
        return (
            "Bonjour. Vous pouvez ecrire : MOYENNE, PRESENCE, BULLETIN, "
            "EVENEMENT, AIDE."
        )

    async def _reply_moyenne(self, students: list[Student]) -> str:
        lines = []
        for s in students:
            avg = await self._last_average(s.id)
            avg_str = f"{avg:.2f}/20" if avg is not None else "non disponible"
            lines.append(f"{s.firstName} {s.lastName}: {avg_str}")
        return " | ".join(lines)

    async def _reply_presence(self, students: list[Student]) -> str:
        from app.modules.attendance.models import AttendanceRecord
        from app.shared.enums import AttendanceStatus

        since = datetime.now(UTC) - timedelta(days=7)
        lines = []
        for s in students:
            stmt = (
                select(AttendanceRecord)
                .where(AttendanceRecord.studentId == s.id)
                .where(AttendanceRecord.scannedAt >= since)
            )
            rows = list((await self.session.execute(stmt)).scalars().all())
            if not rows:
                lines.append(f"{s.firstName}: aucune observation.")
                continue
            p = sum(1 for r in rows if r.status == AttendanceStatus.PRESENT)
            a = sum(1 for r in rows if r.status == AttendanceStatus.ABSENT)
            r_late = sum(1 for r in rows if r.status == AttendanceStatus.LATE)
            lines.append(
                f"{s.firstName}: {p}P/{a}A/{r_late}R (7j)"
            )
        return " | ".join(lines)

    async def _reply_bulletin(self, students: list[Student]) -> str:
        lines = []
        for s in students:
            stmt = (
                select(ReportCard)
                .where(ReportCard.studentId == s.id)
                .order_by(desc(ReportCard.updatedAt))
                .limit(1)
            )
            last = (await self.session.execute(stmt)).scalar_one_or_none()
            if last is None:
                lines.append(f"{s.firstName}: aucun bulletin disponible.")
                continue
            avg_str = f"{last.average:.2f}/20" if last.average else "n/a"
            rank_str = (
                f"rang {last.rank}/{last.totalStudents}"
                if last.rank and last.totalStudents else ""
            )
            lines.append(
                f"{s.firstName}: bulletin {avg_str} {rank_str}".strip()
            )
        return " | ".join(lines)

    # =====================================================================
    # USSD enrichment (Module 14 — hook léger)
    # =====================================================================
    def enrich_ussd_menu(self, base_menu: str) -> str:
        """Ajoute les options 4 (bulletins) et 5 (événement) au menu USSD.

        Le hook reste pur (pas de side-effect), si bien que le Module 14
        peut l'appeler depuis ``handle_ussd`` ou non, selon un feature
        flag. Pour le MVP on l'expose en utilitaire — l'intégration
        complète au menu Module 14 viendra avec le backlog 18.5.
        """
        addons = (
            "\n4. Bulletins recents"
            "\n5. Prochain evenement"
        )
        if "0. Quitter" in base_menu:
            return base_menu.replace("0. Quitter", addons.strip() + "\n0. Quitter")
        return base_menu + addons

    async def whatsapp_journal_count(self) -> int:
        """Helper de debug : compte les messages WhatsApp persistés."""
        from sqlalchemy import func
        stmt = select(func.count()).select_from(WhatsAppMessage)
        return int((await self.session.execute(stmt)).scalar_one())

    @staticmethod
    def expire_inactive_sessions_query() -> None:
        """Bookmark : la purge des sessions expirées sera un cron job
        (backlog 18.6). En attendant, on filtre ``expiresAt > now`` à la
        lecture, ce qui garantit l'absence d'effet utilisateur visible.
        """
        logger.debug("expire_inactive_sessions_query — TODO cron job")
        return None
