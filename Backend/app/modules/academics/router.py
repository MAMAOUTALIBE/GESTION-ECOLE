from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.academics.schemas import (
    AssessmentRead,
    CreateAssessmentRequest,
    CreateParentRequest,
    CreateSchoolYearRequest,
    CreateSubjectRequest,
    DeletedResponse,
    GenerateReportCardsRequest,
    GradeRead,
    ParentRead,
    ReportCardRead,
    SaveGradesRequest,
    SchoolYearRead,
    SubjectRead,
    UpdateParentRequest,
    UpdateValidationStatusRequest,
)
from app.modules.academics.service import AcademicsService
from app.modules.auth.models import User
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

# Mirror NestJS role-access groups
ACADEMIC_WRITE_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN,
    UserRole.SCHOOL_DIRECTOR,
    UserRole.TEACHER,
    UserRole.CENSUS_AGENT,
)
ACADEMIC_VALIDATION_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.PREFECTURE_ADMIN,
    UserRole.SCHOOL_DIRECTOR,
)
SCHOOL_MANAGEMENT_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN,
)


def _service(session: DbSession) -> AcademicsService:
    return AcademicsService(session)


AcSvc = Annotated[AcademicsService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

router = APIRouter(tags=["academics"])


# --- Parents ---------------------------------------------------------
@router.get("/parents", response_model=list[ParentRead])
async def list_parents(
    user: CurrentUserDep,
    service: AcSvc,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[ParentRead]:
    """Cap par défaut 500 — à 200 000 parents national, sinon réponse multi-Mo."""
    return await service.list_parents(user, limit=limit)


@router.post(
    "/parents",
    response_model=ParentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*ACADEMIC_WRITE_ROLES))],
)
async def create_parent(
    dto: CreateParentRequest, user: CurrentUserDep, service: AcSvc
) -> ParentRead:
    return await service.create_parent(user, dto)


@router.patch(
    "/parents/{parent_id}",
    response_model=ParentRead,
    dependencies=[Depends(require_roles(*ACADEMIC_WRITE_ROLES))],
)
async def update_parent(
    parent_id: str,
    dto: UpdateParentRequest,
    user: CurrentUserDep,
    service: AcSvc,
) -> ParentRead:
    return await service.update_parent(user, parent_id, dto)


@router.delete(
    "/parents/{parent_id}",
    response_model=DeletedResponse,
    dependencies=[Depends(require_roles(*ACADEMIC_VALIDATION_ROLES))],
)
async def delete_parent(
    parent_id: str, user: CurrentUserDep, service: AcSvc
) -> DeletedResponse:
    result = await service.delete_parent(user, parent_id)
    return DeletedResponse(**result)


# --- School years ----------------------------------------------------
@router.get("/school-years", response_model=list[SchoolYearRead])
async def list_school_years(
    user: CurrentUserDep, service: AcSvc
) -> list[SchoolYearRead]:
    _ = user  # auth required, all users can read
    return await service.list_school_years()


@router.post(
    "/school-years",
    response_model=SchoolYearRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*SCHOOL_MANAGEMENT_ROLES))],
)
async def create_school_year(
    dto: CreateSchoolYearRequest, user: CurrentUserDep, service: AcSvc
) -> SchoolYearRead:
    return await service.create_school_year(user, dto)


# --- Subjects --------------------------------------------------------
@router.get("/subjects", response_model=list[SubjectRead])
async def list_subjects(user: CurrentUserDep, service: AcSvc) -> list[SubjectRead]:
    _ = user
    return await service.list_subjects()


@router.post(
    "/subjects",
    response_model=SubjectRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*ACADEMIC_VALIDATION_ROLES))],
)
async def create_subject(
    dto: CreateSubjectRequest, user: CurrentUserDep, service: AcSvc
) -> SubjectRead:
    return await service.create_subject(user, dto)


# --- Assessments -----------------------------------------------------
@router.get("/assessments", response_model=list[AssessmentRead])
async def list_assessments(
    user: CurrentUserDep,
    service: AcSvc,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[AssessmentRead]:
    """Cap par défaut 500 — multi-trimestre × 6 matières × ~240 classes = ~6 600 lignes."""
    return await service.list_assessments(user, limit=limit)


@router.post(
    "/assessments",
    response_model=AssessmentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*ACADEMIC_WRITE_ROLES))],
)
async def create_assessment(
    dto: CreateAssessmentRequest, user: CurrentUserDep, service: AcSvc
) -> AssessmentRead:
    return await service.create_assessment(user, dto)


@router.patch(
    "/assessments/{assessment_id}/status",
    response_model=AssessmentRead,
    dependencies=[Depends(require_roles(*ACADEMIC_VALIDATION_ROLES))],
)
async def update_assessment_status(
    assessment_id: str,
    dto: UpdateValidationStatusRequest,
    user: CurrentUserDep,
    service: AcSvc,
) -> AssessmentRead:
    return await service.update_assessment_status(user, assessment_id, dto.status)


# --- Grades ----------------------------------------------------------
@router.get("/grades", response_model=list[GradeRead])
async def list_grades(
    user: CurrentUserDep,
    service: AcSvc,
    assessmentId: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[GradeRead]:
    """Sans `assessmentId`, retourne au plus `limit` notes (par défaut 500).

    À l'échelle 3M élèves × 6 matières × 3 trimestres × 3 évals = ~160M notes,
    une réponse non bornée fait plusieurs centaines de Mo et plante le client.
    Les écrans qui ont besoin de la liste complète d'une évaluation passent
    `assessmentId` (≤45 lignes garanti) — limite alors ignorée.
    """
    return await service.list_grades(user, assessmentId, limit=limit)


@router.post(
    "/grades/bulk",
    response_model=list[GradeRead],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*ACADEMIC_WRITE_ROLES))],
)
async def save_grades(
    dto: SaveGradesRequest, user: CurrentUserDep, service: AcSvc
) -> list[GradeRead]:
    return await service.save_grades(user, dto)


# --- Report cards ----------------------------------------------------
@router.get("/report-cards", response_model=list[ReportCardRead])
async def list_report_cards(
    user: CurrentUserDep,
    service: AcSvc,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[ReportCardRead]:
    """Cap par défaut 500 — à 3M élèves × 3 trimestres on dépasse 9M bulletins."""
    return await service.list_report_cards(user, limit=limit)


@router.post(
    "/report-cards/generate",
    response_model=list[ReportCardRead],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*ACADEMIC_VALIDATION_ROLES))],
)
async def generate_report_cards(
    dto: GenerateReportCardsRequest, user: CurrentUserDep, service: AcSvc
) -> list[ReportCardRead]:
    return await service.generate_report_cards(user, dto)


@router.patch(
    "/report-cards/{report_card_id}/status",
    response_model=ReportCardRead,
    dependencies=[Depends(require_roles(*ACADEMIC_VALIDATION_ROLES))],
)
async def update_report_card_status(
    report_card_id: str,
    dto: UpdateValidationStatusRequest,
    user: CurrentUserDep,
    service: AcSvc,
) -> ReportCardRead:
    return await service.update_report_card_status(user, report_card_id, dto.status)
