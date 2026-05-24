"""Module 11 — DiplomaService : émission, vérification publique, révocation.

Le service est volontairement compact : il assemble crypto + persistance
+ audit log. La génération PDF est optionnelle et désactivée par défaut
(MVP) — on stocke ``pdfS3Key=None`` et un module 11.x branchera WeasyPrint
+ upload S3.

Vérification publique
---------------------
La méthode ``verify_diploma`` est appelée par un endpoint SANS AUTH. Elle
DOIT :

* Ne lever aucune exception qui leak un identifiant interne.
* Renvoyer ``status: NOT_FOUND`` (404) pour un serial inconnu — pas de
  message qui distingue "format invalide" vs "n'existe pas" (anti-énum).
* Pour un diplôme RÉVOQUÉ, retourner ``status: REVOKED`` avec la raison
  publique : la transparence est un service au recruteur.
* N'inclure AUCUN champ qui n'apparaisse pas dans :class:`DiplomaVerification`.

Audit
-----
Chaque émission ET chaque révocation génère une entrée ``AuditLog``
(actorId, action, entity=Diploma, entityId, metadata) pour traçabilité
totale — un diplôme frauduleusement émis DOIT laisser une trace
inaltérable côté audit.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationFailedError,
)
from app.modules.census.models import Student
from app.modules.diplomas.crypto import (
    compute_payload_sha256,
    sign_payload,
)
from app.modules.diplomas.enums import DiplomaStatus, DiplomaType
from app.modules.diplomas.models import Diploma
from app.modules.diplomas.schemas import (
    DiplomaVerification,
    PublicStudentInfo,
)
from app.modules.diplomas.serial import generate_serial
from app.modules.schools.models import School
from app.modules.workflow.models import AuditLog
from app.shared.base import generate_cuid
from app.shared.enums import UserRole
from app.shared.permissions import (
    NATIONAL_SCOPE_ROLES,
    REGIONAL_SCOPE_ROLES,
    SCHOOL_SCOPE_ROLES,
)

if TYPE_CHECKING:
    from app.modules.auth.models import User


_MAX_SERIAL_RETRIES = 5


class DiplomaService:
    """Service centralisé pour le cycle de vie d'un diplôme."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # =====================================================================
    # Issue
    # =====================================================================
    async def issue_diploma(
        self,
        *,
        student_id: str,
        diploma_type: DiplomaType,
        school_id: str,
        actor: "User",
        academic_year_id: str | None = None,
        exam_center: str | None = None,
        score: float | None = None,
        mention: str | None = None,
    ) -> Diploma:
        """Émet un diplôme : canonicalise, signe, persiste, audite.

        Étapes :

        1. Vérifier que l'élève et l'école existent.
        2. Générer un serial unique (retry sur collision via UNIQUE).
        3. Composer le payload signé (informations publiquement vérifiables).
        4. Signer avec Ed25519, calculer SHA-256.
        5. Persister Diploma (status=ISSUED) + AuditLog.
        """
        # 1. Pré-conditions métier
        student = await self.session.get(Student, student_id)
        if student is None:
            raise NotFoundError(detail="Élève introuvable")
        school = await self.session.get(School, school_id)
        if school is None:
            raise NotFoundError(detail="École introuvable")

        if score is not None and (score < 0 or score > 20):
            raise ValidationFailedError(detail="Score hors plage [0, 20].")

        issued_at = datetime.now(UTC)
        year = issued_at.year

        # 2. Génération du serial (retry si collision DB)
        serial = await self._generate_unique_serial(
            diploma_type.value, year,
        )

        # 3. Payload signé : on conserve UNIQUEMENT les informations
        # publiquement vérifiables. Pas de studentId interne dans le
        # payload signé — la vérification publique ne doit pas leak l'ID.
        payload = self._build_signed_payload(
            serial=serial,
            student=student,
            school=school,
            diploma_type=diploma_type,
            issued_at=issued_at,
            exam_center=exam_center,
            score=score,
            mention=mention,
        )

        # 4. Signature
        signature_b64, fingerprint = sign_payload(payload)
        payload_sha = compute_payload_sha256(payload)

        # 5. Persistance + audit
        diploma = Diploma(
            id=generate_cuid(),
            serial=serial,
            studentId=student_id,
            diplomaType=diploma_type,
            academicYearId=academic_year_id,
            schoolId=school_id,
            examCenter=exam_center,
            score=score,
            mention=mention,
            issuedAt=issued_at,
            signedAt=issued_at,
            payloadSha256=payload_sha,
            signature=signature_b64,
            publicKeyFingerprint=fingerprint,
            status=DiplomaStatus.ISSUED,
        )
        self.session.add(diploma)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            # Collision serial (extrêmement rare) : on rollback la session
            # pour permettre un retry côté caller.
            await self.session.rollback()
            raise ConflictError(
                detail="Collision serial — réessayez l'émission.",
            ) from exc

        self.session.add(AuditLog(
            id=generate_cuid(),
            actorId=actor.id,
            action="ISSUE_DIPLOMA",
            entity="Diploma",
            entityId=diploma.id,
            metadata_={
                "serial": serial,
                "studentId": student_id,
                "schoolId": school_id,
                "diplomaType": diploma_type.value,
                "publicKeyFingerprint": fingerprint,
            },
        ))
        await self.session.flush()

        logger.info(
            "diplomas: émis serial={} type={} student={} actor={}",
            serial, diploma_type.value, student_id, actor.id,
        )
        return diploma

    async def _generate_unique_serial(
        self, diploma_type: str, year: int,
    ) -> str:
        """Pré-check du serial avant l'INSERT, pour éviter de polluer la
        transaction principale par une collision. La contrainte UNIQUE
        reste le garde-fou final côté DB.
        """
        for _ in range(_MAX_SERIAL_RETRIES):
            candidate = generate_serial(diploma_type, year)
            existing = await self.session.execute(
                select(Diploma.id).where(Diploma.serial == candidate),
            )
            if existing.scalar_one_or_none() is None:
                return candidate
        # Très improbable mais on lève explicitement plutôt que de boucler.
        raise ConflictError(
            detail="Impossible de générer un serial unique après "
            f"{_MAX_SERIAL_RETRIES} essais.",
        )

    def _build_signed_payload(
        self,
        *,
        serial: str,
        student: Student,
        school: School,
        diploma_type: DiplomaType,
        issued_at: datetime,
        exam_center: str | None,
        score: float | None,
        mention: str | None,
    ) -> dict[str, Any]:
        """Compose le dict publiquement vérifiable qui sera signé.

        Convention : tous les noms de clés sont snake_case ; ``serial``
        sert d'identifiant pivot ; ``schoolName`` est dénormalisé pour
        que la vérification n'ait pas besoin de re-rejoindre School
        (utile pour les vérificateurs offline).
        """
        return {
            "serial": serial,
            "diploma_type": diploma_type.value,
            "issued_at": issued_at.isoformat(),
            "student": {
                "first_name": student.firstName,
                "last_name": student.lastName,
            },
            "school": {
                "name": school.name,
                "code": school.code,
            },
            "exam_center": exam_center,
            "score": round(score, 2) if score is not None else None,
            "mention": mention,
        }

    # =====================================================================
    # Verify (public, sans auth)
    # =====================================================================
    async def verify_diploma(self, serial: str) -> DiplomaVerification:
        """Vérification publique. RAISE ``NotFoundError`` si serial inconnu —
        le router convertit en HTTP 404 avec un body structuré.

        Pour un diplôme révoqué : status=REVOKED + raison + dates.
        Pour un diplôme valide : status=VALID + payload public + signature.
        """
        # Charge le diplôme + relations nécessaires (student, school).
        stmt = (
            select(Diploma)
            .where(Diploma.serial == serial)
            .options(
                selectinload(Diploma.student),
                selectinload(Diploma.school),
            )
        )
        diploma = (await self.session.execute(stmt)).scalar_one_or_none()
        if diploma is None:
            raise NotFoundError(
                detail="Serial inconnu.",
                extra={"serial": serial, "status": "NOT_FOUND"},
            )

        if diploma.status == DiplomaStatus.REVOKED:
            return DiplomaVerification(
                status="REVOKED",
                serial=diploma.serial,
                diplomaType=diploma.diplomaType,
                issuedAt=diploma.issuedAt,
                revokedAt=diploma.revokedAt,
                revokedReason=diploma.revokedReason,
                student=self._public_student_info(diploma),
                examCenter=diploma.examCenter,
            )

        # Statut DRAFT : on traite comme "non émis" → NOT_FOUND public.
        # Les drafts sont des brouillons internes ; ils ne doivent JAMAIS
        # apparaître via la vérification publique.
        if diploma.status != DiplomaStatus.ISSUED:
            raise NotFoundError(
                detail="Serial inconnu.",
                extra={"serial": serial, "status": "NOT_FOUND"},
            )

        # ISSUED : on recompose le payload pour le retourner aux
        # vérificateurs externes (qui peuvent re-canonicaliser + re-hasher
        # + verify avec la clé publique).
        payload = self._build_signed_payload(
            serial=diploma.serial,
            student=diploma.student,
            school=diploma.school,
            diploma_type=diploma.diplomaType,
            issued_at=diploma.issuedAt or diploma.signedAt or datetime.now(UTC),
            exam_center=diploma.examCenter,
            score=diploma.score,
            mention=diploma.mention,
        )

        return DiplomaVerification(
            status="VALID",
            serial=diploma.serial,
            diplomaType=diploma.diplomaType,
            issuedAt=diploma.issuedAt,
            student=self._public_student_info(diploma),
            score=diploma.score,
            mention=diploma.mention,
            examCenter=diploma.examCenter,
            payloadSha256=diploma.payloadSha256,
            signature=diploma.signature,
            publicKeyFingerprint=diploma.publicKeyFingerprint,
            payload=payload,
        )

    def _public_student_info(self, diploma: Diploma) -> PublicStudentInfo:
        """Compose les infos publiques du titulaire — minimisation."""
        last_initial = (
            f"{diploma.student.lastName[:1]}." if diploma.student.lastName
            else ""
        )
        return PublicStudentInfo(
            firstName=diploma.student.firstName,
            lastNameInitial=last_initial,
            schoolName=diploma.school.name if diploma.school else None,
        )

    # =====================================================================
    # QR code helpers
    # =====================================================================
    def build_verification_url(self, serial: str) -> str:
        """URL publique encodée dans le QR.

        Format : ``{QR_PUBLIC_BASE_URL}/diplomas/{serial}``. Le scanner
        ouvre la page de vérification sans avoir à entrer le serial à
        la main.
        """
        base = settings.qr_public_base_url.rstrip("/")
        return f"{base}/diplomas/{serial}"

    def generate_qr_svg(self, serial: str) -> str:
        """QR SVG (chaîne) pour un serial. Réutilise la lib ``qrcode``."""
        import qrcode
        import qrcode.image.svg as svg

        url = self.build_verification_url(serial)
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(image_factory=svg.SvgImage)
        # img.to_string() renvoie des bytes ; on décode pour stocker en str.
        return img.to_string(encoding="unicode")

    # =====================================================================
    # Revoke (national admin only)
    # =====================================================================
    async def revoke_diploma(
        self, serial: str, reason: str, actor: "User",
    ) -> Diploma:
        if actor.role != UserRole.NATIONAL_ADMIN:
            raise ForbiddenError(
                detail="Seul un administrateur national peut révoquer un "
                "diplôme.",
            )

        stmt = select(Diploma).where(Diploma.serial == serial)
        diploma = (await self.session.execute(stmt)).scalar_one_or_none()
        if diploma is None:
            raise NotFoundError(detail="Diplôme introuvable.")

        if diploma.status == DiplomaStatus.REVOKED:
            raise ConflictError(detail="Diplôme déjà révoqué.")

        diploma.status = DiplomaStatus.REVOKED
        diploma.revokedAt = datetime.now(UTC)
        diploma.revokedReason = reason

        self.session.add(AuditLog(
            id=generate_cuid(),
            actorId=actor.id,
            action="REVOKE_DIPLOMA",
            entity="Diploma",
            entityId=diploma.id,
            metadata_={"serial": serial, "reason": reason},
        ))
        await self.session.flush()
        return diploma

    # =====================================================================
    # List (with territorial scope)
    # =====================================================================
    async def list_diplomas(
        self,
        *,
        actor: "User",
        status_filter: DiplomaStatus | None = None,
        school_id: str | None = None,
        diploma_type: DiplomaType | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Diploma], int]:
        """Listing avec scope territorial automatique.

        * NATIONAL/MINISTRY  → tout voir (filtres optionnels).
        * REGIONAL/INSPECTOR → écoles de leur région (via Join School.regionId).
        * SCHOOL_DIRECTOR/TEACHER → uniquement leur école.
        * Autres rôles      → 403.
        """
        if actor.role not in (
            NATIONAL_SCOPE_ROLES
            | REGIONAL_SCOPE_ROLES
            | SCHOOL_SCOPE_ROLES
        ):
            raise ForbiddenError(detail="Rôle non autorisé.")

        stmt = select(Diploma)
        count_stmt = select(func.count(Diploma.id))

        # Scope
        if actor.role in SCHOOL_SCOPE_ROLES:
            if not actor.schoolId:
                raise ForbiddenError(detail="Aucune école associée.")
            stmt = stmt.where(Diploma.schoolId == actor.schoolId)
            count_stmt = count_stmt.where(Diploma.schoolId == actor.schoolId)
        elif actor.role in REGIONAL_SCOPE_ROLES:
            if not actor.regionId:
                raise ForbiddenError(detail="Aucune région associée.")
            # Sous-requête sur les écoles de la région.
            school_ids_subq = (
                select(School.id).where(School.regionId == actor.regionId)
            ).scalar_subquery()
            stmt = stmt.where(Diploma.schoolId.in_(school_ids_subq))
            count_stmt = count_stmt.where(
                Diploma.schoolId.in_(school_ids_subq),
            )

        # Filtres explicites
        if school_id is not None:
            stmt = stmt.where(Diploma.schoolId == school_id)
            count_stmt = count_stmt.where(Diploma.schoolId == school_id)
        if status_filter is not None:
            stmt = stmt.where(Diploma.status == status_filter)
            count_stmt = count_stmt.where(Diploma.status == status_filter)
        if diploma_type is not None:
            stmt = stmt.where(Diploma.diplomaType == diploma_type)
            count_stmt = count_stmt.where(Diploma.diplomaType == diploma_type)

        stmt = stmt.order_by(Diploma.createdAt.desc()).limit(limit).offset(offset)
        rows = list((await self.session.execute(stmt)).scalars())
        total = (await self.session.execute(count_stmt)).scalar_one()
        return rows, total

    # =====================================================================
    # Get PDF (placeholder for MVP)
    # =====================================================================
    async def get_diploma_pdf(self, serial: str) -> bytes | None:
        """Renvoie les bytes PDF du diplôme.

        MVP : le PDF n'est pas généré ni stocké (``pdfS3Key=None``). On
        renvoie ``None`` ; le router convertit en HTTP 404 avec un message
        clair indiquant que le PDF n'est pas encore disponible. La
        signature reste vérifiable via la page web publique.
        """
        stmt = select(Diploma).where(Diploma.serial == serial)
        diploma = (await self.session.execute(stmt)).scalar_one_or_none()
        if diploma is None:
            raise NotFoundError(detail="Diplôme introuvable.")
        if diploma.pdfS3Key is None:
            return None
        # Module 11.x : récupérer les bytes depuis S3 (boto3) ici.
        return None


__all__ = ["DiplomaService"]
