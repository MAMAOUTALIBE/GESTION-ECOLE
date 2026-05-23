"""Reports service — render PDF bulletins from ReportCard records.

WeasyPrint is used for HTML→PDF; QR codes are generated with the qrcode lib.
PDFs may optionally be uploaded to S3-compatible storage (MinIO in dev).
"""
import base64
import io
from typing import Any

import qrcode
from qrcode.image.pil import PilImage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.modules.academics.models import Grade, ReportCard
from app.modules.auth.models import User
from app.modules.census.models import Student
from app.modules.reports.schemas import BulletinVerifyResponse
from app.modules.reports.template import BULLETIN_HTML, render_grade_rows
from app.modules.schools.models import School
from app.modules.workflow.models import AuditLog
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
        from weasyprint import HTML  # noqa: PLC0415

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
