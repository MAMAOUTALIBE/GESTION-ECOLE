from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse, Response

from app.modules.auth.models import User
from app.modules.reports.schemas import (
    BatchAcceptedResponse,
    BulletinVerifyResponse,
    GenerateBulletinsRequest,
    ReportCardGenerationStatus,
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

# Module 4 — async single-card generation peut être demandée aussi par les
# enseignants (génération à la demande pendant un cours).
REPORTS_REQUEST_ROLES = (*REPORTS_GENERATE_ROLES, UserRole.TEACHER)


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
    from sqlalchemy import select

    from app.modules.academics.models import ReportCard
    from app.workers.pdf_tasks import render_bulletins_batch

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


# ---------------------------------------------------------------------------
# Module 4 — async single-card generation
# ---------------------------------------------------------------------------
@router.post(
    "/student/{student_id}/period/{period_id}/generate",
    response_model=ReportCardGenerationStatus,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_roles(*REPORTS_REQUEST_ROLES))],
    summary="Demander la génération asynchrone d'un bulletin PDF",
)
async def request_generation(
    student_id: str,
    period_id: str,
    user: CurrentUserDep,
    service: ReportsSvc,
) -> ReportCardGenerationStatus:
    """Enqueue (ou retourne directement si DONE) un bulletin PDF.

    Idempotent : deux requêtes simultanées pour la même paire ne créent qu'un
    seul ReportCard / un seul task (cf. ``_find_or_create_report_card`` qui
    utilise ``SELECT ... FOR UPDATE``).
    """
    return await service.request_generation(student_id, period_id, user)


@router.get(
    "/{report_card_id}/status",
    response_model=ReportCardGenerationStatus,
    summary="Poll de l'état du PDF (PENDING|PROCESSING|DONE|FAILED)",
)
async def get_status(
    report_card_id: str,
    user: CurrentUserDep,
    service: ReportsSvc,
) -> ReportCardGenerationStatus:
    return await service.get_generation_status(report_card_id, user)


@router.get(
    "/{report_card_id}/download",
    status_code=status.HTTP_302_FOUND,
    summary="Redirige (302) vers l'URL S3 presignée du PDF",
    responses={
        302: {"description": "Redirection vers l'URL S3 presignée (validité 1h)"},
        404: {"description": "PDF pas encore prêt ou bulletin introuvable"},
    },
)
async def download(
    report_card_id: str,
    user: CurrentUserDep,
    service: ReportsSvc,
) -> RedirectResponse:
    url = await service.download_url(report_card_id, user)
    if url is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Le PDF n'est pas encore disponible (status != DONE).",
        )
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)
