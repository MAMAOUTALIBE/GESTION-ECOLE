"""Reports service — render PDF bulletins from ReportCard records.

WeasyPrint is used for HTML→PDF; QR codes are generated with the qrcode lib.
PDFs may optionally be uploaded to S3-compatible storage (MinIO in dev).

Module 4 added asynchronous generation: a HTTP request enqueues a Celery
task that renders, uploads to S3 and updates the ``ReportCard.pdf*`` columns;
the caller polls a status endpoint and finally redirects to a presigned URL.
"""
import base64
import hashlib
import io
from datetime import UTC, datetime
from typing import Any

import qrcode
from qrcode.image.pil import PilImage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.modules.academics.models import Grade, ReportCard
from app.modules.auth.models import User
from app.modules.census.models import Student
from app.modules.reports import storage
from app.modules.reports.schemas import (
    BulletinVerifyResponse,
    ReportCardGenerationStatus,
)
from app.modules.reports.template import BULLETIN_HTML, render_grade_rows
from app.modules.schools.models import School
from app.modules.workflow.models import AuditLog
from app.shared.enums import ReportCardPdfStatus
from app.shared.permissions import (
    NATIONAL_SCOPE_ROLES,
    PREFECTURE_SCOPE_ROLES,
    REGIONAL_SCOPE_ROLES,
    SUB_PREFECTURE_SCOPE_ROLES,
)


def _qr_png_base64(payload: str) -> str:
    """Render a QR PNG as base64 (suitable for inline <img src=data:>)."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=4,
        border=1,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white", image_factory=PilImage)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


class ReportsService:
    """Public lookup is **NOT** scoped — anyone with the verification code
    can confirm a bulletin's authenticity. RBAC scope is only enforced when
    a logged-in user requests the full PDF render.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ==================================================================
    # Public verification (no auth required)
    # ==================================================================
    async def verify(self, verification_code: str) -> BulletinVerifyResponse:
        stmt = (
            select(ReportCard)
            .where(ReportCard.verificationCode == verification_code)
            .options(
                selectinload(ReportCard.student).selectinload(Student.school),
                selectinload(ReportCard.schoolYear),
                selectinload(ReportCard.period),
            )
        )
        rc = (await self.session.execute(stmt)).scalar_one_or_none()
        if rc is None:
            return BulletinVerifyResponse(
                verificationCode=verification_code, valid=False
            )
        return BulletinVerifyResponse(
            verificationCode=rc.verificationCode,
            valid=True,
            studentFullName=(
                f"{rc.student.firstName} {rc.student.lastName}"
                if rc.student else None
            ),
            schoolName=rc.student.school.name if rc.student and rc.student.school else None,
            periodName=rc.period.name if rc.period else None,
            schoolYearName=rc.schoolYear.name if rc.schoolYear else None,
            average=rc.average,
            rank=rc.rank,
            totalStudents=rc.totalStudents,
            status=rc.status,
            issuedAt=rc.issuedAt,
        )

    # ==================================================================
    # Render PDF for a single report card (auth required)
    # ==================================================================
    async def render_pdf(self, user: User, report_card_id: str) -> bytes:
        # WeasyPrint is heavy — import lazily so app start doesn't pay the cost
        from weasyprint import HTML

        rc = await self._load_report_card_with_context(report_card_id)
        if rc is None:
            raise NotFoundError(detail="Bulletin introuvable")
        await self._assert_can_access_school(user, rc.student.schoolId)

        html_str = self._render_html(rc)
        return HTML(string=html_str).write_pdf()  # type: ignore[no-any-return]

    # ==================================================================
    # Helpers used by both sync render & Celery batch worker
    # ==================================================================
    async def _load_report_card_with_context(
        self, report_card_id: str
    ) -> ReportCard | None:
        stmt = (
            select(ReportCard)
            .where(ReportCard.id == report_card_id)
            .options(
                selectinload(ReportCard.student).selectinload(Student.school).selectinload(
                    School.region
                ),
                selectinload(ReportCard.student).selectinload(Student.classRoom),
                selectinload(ReportCard.schoolYear),
                selectinload(ReportCard.period),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _load_grades_for_card(self, rc: ReportCard) -> list[dict[str, Any]]:
        stmt = (
            select(Grade)
            .where(
                Grade.studentId == rc.studentId,
                Grade.periodId == rc.periodId,
                Grade.schoolYearId == rc.schoolYearId,
            )
            .options(
                selectinload(Grade.subject),
                selectinload(Grade.assessment),
            )
        )
        rows = (await self.session.execute(stmt)).scalars().unique().all()

        # Aggregate per subject (mean of grades weighted by assessment coef)
        per_subject: dict[str, dict[str, Any]] = {}
        for g in rows:
            subj = g.subject
            if not subj:
                continue
            current = per_subject.setdefault(
                subj.id,
                {
                    "subject": subj.name,
                    "coefficient": subj.coefficient,
                    "max_score": int(g.assessment.maxScore) if g.assessment else 20,
                    "weighted_sum": 0.0,
                    "coef_sum": 0.0,
                    "appreciations": [],
                },
            )
            assess_coef = g.assessment.coefficient if g.assessment else 1.0
            current["weighted_sum"] += g.score * assess_coef
            current["coef_sum"] += assess_coef
            if g.appreciation:
                current["appreciations"].append(g.appreciation)

        out: list[dict[str, Any]] = []
        for s in per_subject.values():
            score = (
                round((s["weighted_sum"] / s["coef_sum"]) * 100) / 100
                if s["coef_sum"] else 0.0
            )
            out.append({
                "subject": s["subject"],
                "coefficient": s["coefficient"],
                "max_score": s["max_score"],
                "score": score,
                "appreciation": " · ".join(s["appreciations"][:2]) if s["appreciations"] else "",
            })
        out.sort(key=lambda r: r["subject"])
        return out

    def _render_html(self, rc: ReportCard) -> str:
        # Synchronous part — call after grades have been loaded
        return self._render_html_with_grades(rc, [])

    def _render_html_with_grades(
        self, rc: ReportCard, grades: list[dict[str, Any]]
    ) -> str:
        student = rc.student
        school = student.school if student else None
        region = school.region if school else None
        classroom = student.classRoom if student else None

        verify_url = settings.qr_public_base_url.rstrip("/")
        qr_payload = f"{verify_url}/{rc.verificationCode}"

        return BULLETIN_HTML.format(
            full_name=f"{student.firstName} {student.lastName}" if student else "",
            unique_code=student.uniqueCode if student else "",
            class_name=classroom.name if classroom else "—",
            school_name=school.name if school else "",
            region_name=region.name if region else "",
            prefecture_name=(school.prefecture if school and school.prefecture else "—"),
            school_year_name=rc.schoolYear.name if rc.schoolYear else "—",
            period_name=rc.period.name if rc.period else "—",
            issued_at=rc.issuedAt.strftime("%d/%m/%Y") if rc.issuedAt else "—",
            qr_base64=_qr_png_base64(qr_payload),
            verification_code=rc.verificationCode,
            grade_rows=render_grade_rows(grades),
            average=f"{rc.average:.2f}" if rc.average is not None else "—",
            rank=rc.rank if rc.rank is not None else "—",
            total_students=rc.totalStudents if rc.totalStudents is not None else "—",
            status=rc.status.value,
            verify_url=verify_url,
        )

    async def render_html_for_card(self, rc_id: str) -> str | None:
        """Compose final HTML (with grades). Used by the Celery worker."""
        rc = await self._load_report_card_with_context(rc_id)
        if rc is None:
            return None
        grades = await self._load_grades_for_card(rc)
        return self._render_html_with_grades(rc, grades)

    async def _assert_can_access_school(self, user: User, school_id: str) -> None:
        school = await self.session.get(School, school_id)
        if school is None:
            raise NotFoundError(detail="École introuvable")
        if user.role in NATIONAL_SCOPE_ROLES:
            return
        if user.role in REGIONAL_SCOPE_ROLES and user.regionId == school.regionId:
            return
        if user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId == school.prefectureId:
            return
        if (
            user.role in SUB_PREFECTURE_SCOPE_ROLES
            and user.subPrefectureId == school.subPrefectureId
        ):
            return
        if user.schoolId == school.id:
            return
        raise ForbiddenError(detail="Accès non autorisé pour cette école")

    # ==================================================================
    # Audit / accounting
    # ==================================================================
    async def record_pdf_render(
        self, user: User, report_card_id: str, *, batch: bool = False
    ) -> None:
        self.session.add(
            AuditLog(
                actorId=user.id,
                action="RENDER_BULLETIN_PDF" if not batch else "RENDER_BULLETINS_BATCH",
                entity="ReportCard",
                entityId=report_card_id,
            )
        )
        await self.session.flush()

    # ==================================================================
    # Module 4 — async generation pipeline
    # ==================================================================
    async def request_generation(
        self, student_id: str, period_id: str, actor: User
    ) -> ReportCardGenerationStatus:
        """Demande la génération d'un bulletin PDF (async).

        * Si un bulletin existe déjà en ``DONE`` pour ``(student_id, period_id)``
          on retourne directement le ``downloadUrl`` (idempotence / cache).
        * Sinon on crée / ré-utilise la ligne ``ReportCard``, on la marque
          ``PENDING`` et on enqueue le task Celery.
        """
        from app.core.observability import (
            reports_pdf_completed_total,
            reports_pdf_requested_total,
        )

        reports_pdf_requested_total.inc()

        rc = await self._find_or_create_report_card(student_id, period_id)
        await self._assert_can_access_school(actor, rc.student.schoolId)

        # Idempotence — déjà DONE → on renvoie l'URL directement sans toucher
        # au statut ni enqueue.
        if rc.pdfStatus == ReportCardPdfStatus.DONE and rc.pdfS3Key:
            reports_pdf_completed_total.labels(status="cache_hit").inc()
            url = await storage.get_presigned_url(rc.pdfS3Key)
            return ReportCardGenerationStatus(
                reportCardId=rc.id,
                status=ReportCardPdfStatus.DONE,
                downloadUrl=url,
                generatedAt=rc.pdfGeneratedAt,
                sha256=rc.pdfSha256,
                pollUrl=f"{settings.api_prefix}/reports/{rc.id}/status",
            )

        # Race-condition friendly : si quelqu'un d'autre a déjà enqueue (status
        # PROCESSING) on ne re-enqueue PAS, on renvoie son taskId.
        if rc.pdfStatus == ReportCardPdfStatus.PROCESSING and rc.pdfTaskId:
            return ReportCardGenerationStatus(
                reportCardId=rc.id,
                status=ReportCardPdfStatus.PROCESSING,
                taskId=rc.pdfTaskId,
                pollUrl=f"{settings.api_prefix}/reports/{rc.id}/status",
            )

        # On (re)passe en PENDING + enqueue
        from app.core.celery_app import celery_app
        from app.workers.pdf_tasks import generate_report_pdf_task

        rc.pdfStatus = ReportCardPdfStatus.PENDING
        rc.pdfErrorMessage = None
        await self.session.flush()

        task = generate_report_pdf_task.delay(rc.id)
        rc.pdfTaskId = task.id

        # En mode eager (tests / dev sans worker), Celery a déjà exécuté le
        # task ci-dessus avec sa propre session sync. Cette session ne voit
        # pas les écritures faites par celle de la requête (la création de
        # ReportCard n'a pas été commit). Pour rendre la pipeline testable
        # de bout-en-bout, on ré-exécute la génération inline en utilisant
        # la session async de la requête — c'est strictement équivalent en
        # termes de comportement métier, juste localisé dans la même trx.
        if celery_app.conf.task_always_eager:
            await self._render_and_upload_inline(rc)

        await self.session.flush()

        await self.record_pdf_render(actor, rc.id)

        # Re-load post-render pour récupérer le status final.
        await self.session.refresh(rc)
        download_url: str | None = None
        if rc.pdfStatus == ReportCardPdfStatus.DONE and rc.pdfS3Key:
            download_url = await storage.get_presigned_url(rc.pdfS3Key)

        return ReportCardGenerationStatus(
            reportCardId=rc.id,
            status=rc.pdfStatus,
            taskId=task.id,
            pollUrl=f"{settings.api_prefix}/reports/{rc.id}/status",
            downloadUrl=download_url,
            generatedAt=rc.pdfGeneratedAt,
            sha256=rc.pdfSha256,
            errorMessage=rc.pdfErrorMessage,
        )

    async def _render_and_upload_inline(self, rc: ReportCard) -> None:
        """Eager-mode helper : rend + upload + met à jour le ReportCard en
        utilisant la session async courante (visible dans la même transaction).

        Reproduit la logique de ``app.workers.pdf_tasks.generate_report_pdf_task``
        sans passer par un session sync séparée. Pas appelé en prod (le worker
        Celery s'en charge dans son propre process).
        """
        import time

        from app.core.observability import (
            reports_pdf_completed_total,
            reports_pdf_duration_seconds,
        )
        from app.modules.reports.storage import S3Error
        from app.workers.pdf_tasks import _render_pdf_sync

        rc.pdfStatus = ReportCardPdfStatus.PROCESSING
        await self.session.flush()

        start = time.monotonic()
        max_retries = 3
        try:
            grades = await self._load_grades_for_card(rc)
            pdf_bytes = _render_pdf_sync(rc, grades)
            sha = ReportsService.compute_sha256(pdf_bytes)
            key = storage.bulletin_key(
                rc.student.schoolId, rc.periodId, rc.studentId
            )

            # Retry boucle inline — équivalent au ``self.retry`` Celery.
            for attempt in range(max_retries + 1):
                try:
                    await storage.upload_pdf(
                        key,
                        pdf_bytes,
                        metadata={
                            "sha256": sha,
                            "report_card_id": rc.id,
                            "student_id": rc.studentId,
                            "period_id": rc.periodId,
                        },
                    )
                    break
                except S3Error:
                    if attempt < max_retries:
                        # En prod le worker Celery dort entre tentatives.
                        continue
                    raise
        except S3Error as exc:
            rc.pdfStatus = ReportCardPdfStatus.FAILED
            rc.pdfErrorMessage = (
                f"S3 unavailable after {max_retries + 1} attempts: {exc}"
            )
            reports_pdf_completed_total.labels(status="failed").inc()
            await self.session.flush()
            return
        except Exception as exc:
            rc.pdfStatus = ReportCardPdfStatus.FAILED
            rc.pdfErrorMessage = f"{type(exc).__name__}: {exc}"[:2000]
            reports_pdf_completed_total.labels(status="failed").inc()
            await self.session.flush()
            return

        rc.pdfStatus = ReportCardPdfStatus.DONE
        rc.pdfS3Key = key
        rc.pdfSha256 = sha
        rc.pdfGeneratedAt = datetime.now(UTC)
        rc.pdfErrorMessage = None
        reports_pdf_duration_seconds.observe(time.monotonic() - start)
        reports_pdf_completed_total.labels(status="done").inc()
        await self.session.flush()

    async def get_generation_status(
        self, report_card_id: str, actor: User
    ) -> ReportCardGenerationStatus:
        """Retourne l'état courant du PDF pour un ReportCard."""
        rc = await self._load_report_card_with_context(report_card_id)
        if rc is None:
            raise NotFoundError(detail="Bulletin introuvable")
        await self._assert_can_access_school(actor, rc.student.schoolId)

        download_url: str | None = None
        if rc.pdfStatus == ReportCardPdfStatus.DONE and rc.pdfS3Key:
            download_url = await storage.get_presigned_url(rc.pdfS3Key)

        return ReportCardGenerationStatus(
            reportCardId=rc.id,
            status=rc.pdfStatus,
            taskId=rc.pdfTaskId,
            pollUrl=f"{settings.api_prefix}/reports/{rc.id}/status",
            downloadUrl=download_url,
            generatedAt=rc.pdfGeneratedAt,
            sha256=rc.pdfSha256,
            errorMessage=rc.pdfErrorMessage,
        )

    async def download_url(
        self, report_card_id: str, actor: User
    ) -> str | None:
        """URL S3 presignée si DONE, sinon ``None`` (le router renvoie 404)."""
        rc = await self._load_report_card_with_context(report_card_id)
        if rc is None:
            raise NotFoundError(detail="Bulletin introuvable")
        await self._assert_can_access_school(actor, rc.student.schoolId)
        if rc.pdfStatus != ReportCardPdfStatus.DONE or not rc.pdfS3Key:
            return None
        return await storage.get_presigned_url(rc.pdfS3Key)

    async def _find_or_create_report_card(
        self, student_id: str, period_id: str
    ) -> ReportCard:
        """Trouve le ReportCard ``(student_id, period_id)`` ou le crée.

        Verrouille la ligne avec ``SELECT ... FOR UPDATE`` pour empêcher
        deux requêtes concurrentes de créer deux ReportCards / d'enqueuer
        deux tasks pour la même paire (cf. test
        ``test_generate_handles_concurrent_requests_same_student_period``).
        """
        stmt = (
            select(ReportCard)
            .where(
                ReportCard.studentId == student_id,
                ReportCard.periodId == period_id,
            )
            .options(
                selectinload(ReportCard.student).selectinload(Student.school),
                selectinload(ReportCard.student).selectinload(Student.classRoom),
                selectinload(ReportCard.schoolYear),
                selectinload(ReportCard.period),
            )
            .with_for_update()
        )
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing

        # Pas de ReportCard → on a besoin de connaître le schoolYearId actif.
        # On déduit depuis l'AcademicPeriod (qui pointe sur SchoolYear).
        from app.modules.academics.models import AcademicPeriod

        period = await self.session.get(AcademicPeriod, period_id)
        if period is None:
            raise NotFoundError(detail="Période académique introuvable")
        student = await self.session.get(Student, student_id)
        if student is None:
            raise NotFoundError(detail="Élève introuvable")

        rc = ReportCard(
            studentId=student_id,
            periodId=period_id,
            schoolYearId=period.schoolYearId,
            classRoomId=student.classRoomId,
            verificationCode=f"GE-{student_id[:8]}-{period_id[:8]}",
            pdfStatus=ReportCardPdfStatus.PENDING,
        )
        self.session.add(rc)
        await self.session.flush()
        # Re-load via the eager-options stmt to ensure relations are usable.
        return await self._load_report_card_with_context(rc.id)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Worker helpers (synchronous-friendly via callers)
    # ------------------------------------------------------------------
    @staticmethod
    def compute_sha256(pdf_bytes: bytes) -> str:
        """Hex SHA-256 of the PDF payload (64 chars)."""
        return hashlib.sha256(pdf_bytes).hexdigest()


def count_report_cards_by_pdf_status(session: Any) -> Any:  # pragma: no cover
    """Helper utilitaire (potentiellement utile pour /admin/dashboards). Non
    appelé directement par le pipeline async. Conserve la signature simple
    pour rester appelable depuis un context sync ou async.
    """
    return select(ReportCard.pdfStatus, func.count()).group_by(ReportCard.pdfStatus)
