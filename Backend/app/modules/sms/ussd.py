"""Module 14 — Handler USSD : machine à états pour les sessions ``*999*CODE#``.

Conventions du webhook USSD (alignées sur Africa's Talking / Orange) :

* Le réseau envoie ``{sessionId, phoneNumber, serviceCode, text}``.
* ``text`` est l'historique CONCATÉNÉ des saisies, séparées par ``*``.
  Exemple : ``"1*STU-00012345"`` = option 1, puis code élève.
* La réponse est une string ; on préfixe ``CON `` (continuer) ou ``END ``
  (terminer la session).

Menu MVP (3 options + 0 quitter)
--------------------------------
::

    Bienvenue GESTION-EE
    1. Moyenne de mon enfant
    2. Presence cette semaine
    3. Verifier diplome (CEPE/BEPC)
    0. Quitter

Identification du parent
------------------------
Le numéro USSD (``phoneNumber``) est normalisé via
``normalize_phone_guinea`` (Module 2). On cherche ensuite un ``Student``
dont ``guardianPhone`` correspond exactement (E.164) ; si plusieurs
enfants partagent ce numéro, le parent doit saisir un code élève
(``uniqueCode``) en deuxième étape. Pour rester compatible avec
ESC USSD (limite ~182 chars de réponse), on évite les listes longues.

Tous les écrans répondent en français ASCII (sans accents) pour rester
compatible GSM-7 partout — un caractère hors GSM-7 fait basculer en
UCS-2 et divise la longueur max par 2.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.census.models import Student
from app.modules.census.normalization import normalize_phone_guinea
from app.modules.diplomas.enums import DiplomaStatus
from app.modules.diplomas.models import Diploma
from app.modules.sms.models import UssdSession
from app.shared.base import generate_cuid

if TYPE_CHECKING:
    pass


WELCOME_MENU: str = (
    "Bienvenue GESTION-EE\n"
    "1. Moyenne de mon enfant\n"
    "2. Presence cette semaine\n"
    "3. Verifier diplome (CEPE/BEPC)\n"
    "0. Quitter"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_normalize(phone: str) -> str | None:
    """Comme ``normalize_phone_guinea`` mais swallow les ValueError."""
    try:
        return normalize_phone_guinea(phone)
    except ValueError:
        return None


async def _find_student_by_code(
    session: AsyncSession, unique_code: str,
) -> Student | None:
    """Lookup direct sur ``Student.uniqueCode`` (unique)."""
    code = unique_code.strip().upper()
    stmt = select(Student).where(Student.uniqueCode == code)
    return (await session.execute(stmt)).scalar_one_or_none()


async def _find_students_by_phone(
    session: AsyncSession, phone: str,
) -> list[Student]:
    """Liste des Students dont le ``guardianPhone`` (normalisé) matche."""
    normalized = _safe_normalize(phone)
    if normalized is None:
        return []
    stmt = select(Student).where(Student.guardianPhone == normalized)
    return list((await session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# Renderers — chaque option a son propre handler pour la lisibilité.
# ---------------------------------------------------------------------------
async def _render_average(
    session: AsyncSession, student: Student,
) -> str:
    """Renvoie la moyenne du dernier ReportCard de l'élève."""
    # Import local pour éviter cycles import / dépendance hard.
    from app.modules.academics.models import ReportCard

    stmt = (
        select(ReportCard)
        .where(ReportCard.studentId == student.id)
        .where(ReportCard.average.is_not(None))
        .order_by(desc(ReportCard.updatedAt))
        .limit(1)
    )
    last = (await session.execute(stmt)).scalar_one_or_none()
    if last is None or last.average is None:
        return (
            f"END {student.firstName} {student.lastName}: aucune moyenne "
            "disponible pour le moment."
        )
    return (
        f"END {student.firstName} {student.lastName}: "
        f"moyenne = {last.average:.2f}/20"
    )


async def _render_attendance(
    session: AsyncSession, student: Student,
) -> str:
    """Renvoie un résumé de présence des 7 derniers jours."""
    from datetime import timedelta

    from app.modules.attendance.models import AttendanceRecord
    from app.shared.enums import AttendanceStatus

    since = datetime.now(UTC) - timedelta(days=7)
    stmt = (
        select(AttendanceRecord)
        .where(AttendanceRecord.studentId == student.id)
        .where(AttendanceRecord.scannedAt >= since)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    if not rows:
        return (
            f"END {student.firstName}: aucune observation cette semaine."
        )
    present = sum(1 for r in rows if r.status == AttendanceStatus.PRESENT)
    absent = sum(1 for r in rows if r.status == AttendanceStatus.ABSENT)
    late = sum(1 for r in rows if r.status == AttendanceStatus.LATE)
    return (
        f"END {student.firstName}: {present}P / {absent}A / {late}R "
        "(7 derniers jours)"
    )


async def _render_diploma(
    session: AsyncSession, serial_or_code: str,
) -> str:
    """Vérifie un diplôme par serial OU par code élève (renvoie le plus récent ISSUED)."""
    serial = serial_or_code.strip().upper()
    # 1) Recherche directe par serial
    stmt = select(Diploma).where(Diploma.serial == serial)
    diploma = (await session.execute(stmt)).scalar_one_or_none()

    # 2) Sinon fallback : c'est peut-être un uniqueCode d'élève → dernier
    # diplôme ISSUED.
    if diploma is None:
        student = await _find_student_by_code(session, serial_or_code)
        if student is None:
            return "END Serial inconnu et code eleve inconnu."
        stmt = (
            select(Diploma)
            .where(Diploma.studentId == student.id)
            .where(Diploma.status == DiplomaStatus.ISSUED)
            .order_by(desc(Diploma.issuedAt))
            .limit(1)
        )
        diploma = (await session.execute(stmt)).scalar_one_or_none()
        if diploma is None:
            return (
                f"END Aucun diplome trouve pour {student.firstName} "
                f"{student.lastName}."
            )

    if diploma.status == DiplomaStatus.REVOKED:
        return f"END Diplome {diploma.serial} REVOQUE."
    if diploma.status == DiplomaStatus.ISSUED:
        return (
            f"END Diplome {diploma.serial} VALIDE "
            f"({diploma.diplomaType.value})."
        )
    return "END Serial inconnu."


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
async def handle_ussd(
    *,
    session_id: str,
    phone: str,
    text: str,
    service_code: str | None,
    db: AsyncSession,
) -> str:
    """Pilote une session USSD complète.

    Persiste la ligne :class:`UssdSession` (upsert sur ``sessionId``),
    parse ``text``, route vers le bon renderer, renvoie une string
    ``CON ...`` ou ``END ...``.
    """
    # ----- Upsert session -----
    stmt = select(UssdSession).where(UssdSession.sessionId == session_id)
    session_row = (await db.execute(stmt)).scalar_one_or_none()
    if session_row is None:
        session_row = UssdSession(
            id=generate_cuid(),
            sessionId=session_id,
            phoneNumber=phone,
            serviceCode=service_code,
            lastInput=text or None,
            currentStep="MENU",
        )
        db.add(session_row)
    else:
        session_row.lastInput = text or None

    parts = [p for p in (text or "").split("*") if p != ""]

    # Vide → menu d'accueil
    if not parts:
        session_row.currentStep = "MENU"
        await db.flush()
        return f"CON {WELCOME_MENU}"

    option = parts[0]
    rest = parts[1:]

    if option == "0":
        session_row.currentStep = "DONE"
        session_row.completedAt = datetime.now(UTC)
        await db.flush()
        return "END Merci, au revoir."

    # ----- Option 1 / 2 — Moyenne / Présence -----
    if option in {"1", "2"}:
        return await _handle_student_lookup_option(
            option=option, rest=rest, phone=phone,
            session_row=session_row, db=db,
        )

    # ----- Option 3 — Vérification diplôme -----
    if option == "3":
        if not rest:
            session_row.currentStep = "DIPLOMA_INPUT"
            await db.flush()
            return "CON Saisissez le numero de serie OU le code eleve:"
        serial_input = rest[0]
        session_row.currentStep = "DONE"
        session_row.completedAt = datetime.now(UTC)
        await db.flush()
        return await _render_diploma(db, serial_input)

    # Option inconnue → re-affiche le menu
    session_row.currentStep = "MENU"
    await db.flush()
    return f"CON Option invalide.\n{WELCOME_MENU}"


async def _handle_student_lookup_option(
    *,
    option: str,
    rest: list[str],
    phone: str,
    session_row: UssdSession,
    db: AsyncSession,
) -> str:
    """Branche commune pour les options 1 et 2 (besoin d'identifier l'élève)."""
    students = await _find_students_by_phone(db, phone)

    # Pas de match → message d'aide
    if not students:
        session_row.currentStep = "DONE"
        session_row.completedAt = datetime.now(UTC)
        await db.flush()
        return (
            "END Numero non reconnu comme tuteur. Contactez votre ecole "
            "pour mise a jour."
        )

    # Un seul match ET pas de code saisi → on utilise directement cet élève
    if len(students) == 1 and not rest:
        student = students[0]
    elif rest:
        # Code saisi → on cherche cet élève spécifique
        student = await _find_student_by_code(db, rest[0])
        if student is None:
            session_row.currentStep = "DONE"
            session_row.completedAt = datetime.now(UTC)
            await db.flush()
            return "END Code eleve inconnu."
    else:
        # Plusieurs enfants → on demande le code
        session_row.currentStep = "AWAITING_STUDENT_CODE"
        await db.flush()
        return "CON Plusieurs enfants. Saisissez le code eleve:"

    session_row.currentStep = "DONE"
    session_row.completedAt = datetime.now(UTC)
    await db.flush()

    if option == "1":
        return await _render_average(db, student)
    return await _render_attendance(db, student)


# ---------------------------------------------------------------------------
# HMAC signature verification (optionnel — activé si USSD_HMAC_SECRET set)
# ---------------------------------------------------------------------------
import hashlib
import hmac
import os


def verify_signature(raw_body: bytes, provided_signature: str | None) -> bool:
    """Vérifie une signature HMAC-SHA256 hex sur le corps de la requête.

    Activée uniquement si la variable d'environnement ``USSD_HMAC_SECRET``
    est définie ET non-vide. Sinon, on accepte la requête sans contrôle
    (mode dev / test par défaut, compatible avec les opérateurs qui ne
    signent pas leurs callbacks).

    Renvoie ``True`` si :
    * pas de secret configuré, OU
    * la signature fournie correspond bit-à-bit à HMAC(secret, body).

    Renvoie ``False`` si secret configuré et signature absente / fausse.
    """
    secret = (os.getenv("USSD_HMAC_SECRET") or "").strip()
    if not secret:
        return True
    if not provided_signature:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, provided_signature.strip())
