from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response

from app.modules.auth.models import User
from app.modules.reports.schemas import (
    BatchAcceptedResponse,
    BulletinVerifyResponse,
    GenerateBulletinsRequest,
)
from app.modules.reports.service import ReportsService
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

REPORTS_GENERATE_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.PREFECTURE_ADMIN,
    UserRole.SCHOOL_DIRECTOR,
)


def _service(session: DbSession) -> ReportsService:
    return ReportsService(session)


ReportsSvc = Annotated[ReportsService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

router = APIRouter(tags=["reports"])


@router.get(
    "/bulletins/verify/{verification_code}",
    response_model=BulletinVerifyResponse,
    summary="Vérification publique d'un bulletin par son code (PAS d'auth)",
)
async def verify_bulletin(
    verification_code: str, service: ReportsSvc
) -> BulletinVerifyResponse:
    """Public endpoint — anyone with the QR-encoded code can verify
    authenticity. Returns sparse fields if the code is unknown.
    """
    return await service.verify(verification_code)


@router.get(
    "/bulletins/{report_card_id}/pdf",
    summary="Télécharger le bulletin PDF (sync, single render)",
    responses={200: {"content": {"application/pdf": {}}}},
)
async def download_bulletin_pdf(
    report_card_id: str,
    user: CurrentUserDep,
    service: ReportsSvc,
) -> Response:
    pdf_bytes = await service.render_pdf(user, report_card_id)
    await service.record_pdf_render(user, report_card_id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="bulletin-{report_card_id}.pdf"'
            )
        },
    )


@router.post(
    "/bulletins/generate-batch",
    response_model=BatchAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_roles(*REPORTS_GENERATE_ROLES))],
    summary="File d'attente : génération de masse via Celery (workers PDF)",
)
async def generate_batch(
    dto: GenerateBulletinsRequest, user: CurrentUserDep, service: ReportsSvc
) -> BatchAcceptedResponse:
    from app.workers.pdf_tasks import render_bulletins_batch  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415

    from app.modules.academics.models import ReportCard  # noqa: PLC0415

    if dto.reportCardIds:
        ids = dto.reportCardIds
    else:
        stmt = select(ReportCard.id).where(
            ReportCard.schoolYearId == dto.schoolYearId,
            ReportCard.periodId == dto.periodId,
        )
        if dto.classRoomId:
            stmt = stmt.where(ReportCard.classRoomId == dto.classRoomId)
        ids = list((await service.session.execute(stmt)).scalars().all())

    task = render_bulletins_batch.delay(ids, requested_by=user.id)
    return BatchAcceptedResponse(taskId=task.id, estimatedItems=len(ids))
