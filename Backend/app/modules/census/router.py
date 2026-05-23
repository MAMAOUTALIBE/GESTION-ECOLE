from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.auth.models import User
from app.modules.census.schemas import (
    AssignStudentClassRequest,
    AssignTeacherClassesRequest,
    CreateStudentRequest,
    CreateTeacherRequest,
    DashboardQuery,
    DashboardResponse,
    IdentifyResponse,
    MergeStudentsRequest,
    MetadataResponse,
    QrSvgResponse,
    StudentDuplicateCheckRequest,
    StudentDuplicateCheckResponse,
    StudentRead,
    TeacherDuplicateCheckRequest,
    TeacherDuplicateCheckResponse,
    TeacherRead,
    TransferStudentRequest,
)
from app.modules.census.service import MERGE_STUDENTS_ROLES, CensusService
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

CENSUS_WRITE_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN,
    UserRole.SCHOOL_DIRECTOR,
    UserRole.CENSUS_AGENT,
)


def _service(session: DbSession) -> CensusService:
    return CensusService(session)


CensusSvc = Annotated[CensusService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

router = APIRouter(tags=["census"])


# --- Dashboard / metadata --------------------------------------------
@router.get("/dashboard", response_model=DashboardResponse)
async def dashboard(
    user: CurrentUserDep,
    service: CensusSvc,
    query: Annotated[DashboardQuery, Depends()],
) -> DashboardResponse:
    return await service.dashboard(user, query)


@router.get("/metadata", response_model=MetadataResponse)
async def metadata(user: CurrentUserDep, service: CensusSvc) -> MetadataResponse:
    return await service.metadata(user)


# --- Students ---------------------------------------------------------
@router.get("/students", response_model=list[StudentRead])
async def list_students(
    user: CurrentUserDep,
    service: CensusSvc,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[StudentRead]:
    """Cap par défaut 500 — à 3M élèves national, jamais retourner toute la liste."""
    return await service.list_students(user, limit=limit)


@router.get("/students/cards", response_model=list[StudentRead])
async def list_student_cards(
    user: CurrentUserDep, service: CensusSvc
) -> list[StudentRead]:
    return await service.list_student_cards(user)


@router.get("/students/{student_id}", response_model=StudentRead)
async def get_student(
    student_id: str, user: CurrentUserDep, service: CensusSvc
) -> StudentRead:
    return await service.get_student(user, student_id)


@router.post(
    "/students",
    response_model=StudentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*CENSUS_WRITE_ROLES))],
)
async def create_student(
    dto: CreateStudentRequest,
    user: CurrentUserDep,
    service: CensusSvc,
    force: Annotated[bool, Query(description="Force la création même si un doublon HIGH existe")] = False,
) -> StudentRead:
    return await service.create_student(user, dto, force=force)


@router.post(
    "/students/check-duplicates",
    response_model=StudentDuplicateCheckResponse,
    summary="Recherche fuzzy de doublons élèves (Module 2)",
    # C-3 review Module 2 : endpoint réservé aux rôles habilités à créer des
    # élèves. Sans ce filtre, n'importe quel utilisateur authentifié (un
    # TEACHER, un INSPECTOR) pouvait énumérer les fiches.
    dependencies=[Depends(require_roles(*CENSUS_WRITE_ROLES))],
)
async def check_student_duplicates(
    dto: StudentDuplicateCheckRequest,
    user: CurrentUserDep,
    service: CensusSvc,
) -> StudentDuplicateCheckResponse:
    return await service.check_student_duplicates(user, dto)


@router.post(
    "/students/{student_id}/merge",
    response_model=StudentRead,
    summary="Fusionne deux fiches élèves (source → target)",
    dependencies=[Depends(require_roles(*MERGE_STUDENTS_ROLES))],
)
async def merge_students(
    student_id: str,
    dto: MergeStudentsRequest,
    user: CurrentUserDep,
    service: CensusSvc,
) -> StudentRead:
    return await service.merge_students(
        user,
        source_id=student_id,
        target_id=dto.targetId,
        reason=dto.reason,
    )


@router.patch(
    "/students/{student_id}/class",
    response_model=StudentRead,
    dependencies=[Depends(require_roles(*CENSUS_WRITE_ROLES))],
)
async def assign_student_class(
    student_id: str,
    dto: AssignStudentClassRequest,
    user: CurrentUserDep,
    service: CensusSvc,
) -> StudentRead:
    return await service.assign_student_class(user, student_id, dto)


@router.post(
    "/students/{student_id}/transfer",
    response_model=StudentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*CENSUS_WRITE_ROLES))],
)
async def transfer_student(
    student_id: str,
    dto: TransferStudentRequest,
    user: CurrentUserDep,
    service: CensusSvc,
) -> StudentRead:
    return await service.transfer_student(user, student_id, dto)


# --- Teachers ---------------------------------------------------------
@router.get("/teachers", response_model=list[TeacherRead])
async def list_teachers(user: CurrentUserDep, service: CensusSvc) -> list[TeacherRead]:
    return await service.list_teachers(user)


@router.get("/teachers/cards", response_model=list[TeacherRead])
async def list_teacher_cards(
    user: CurrentUserDep, service: CensusSvc
) -> list[TeacherRead]:
    return await service.list_teacher_cards(user)


@router.get("/teachers/{teacher_id}", response_model=TeacherRead)
async def get_teacher(
    teacher_id: str, user: CurrentUserDep, service: CensusSvc
) -> TeacherRead:
    return await service.get_teacher(user, teacher_id)


@router.post(
    "/teachers",
    response_model=TeacherRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*CENSUS_WRITE_ROLES))],
)
async def create_teacher(
    dto: CreateTeacherRequest,
    user: CurrentUserDep,
    service: CensusSvc,
    force: Annotated[bool, Query(description="Force la création même si un doublon HIGH existe")] = False,
) -> TeacherRead:
    return await service.create_teacher(user, dto, force=force)


@router.post(
    "/teachers/check-duplicates",
    response_model=TeacherDuplicateCheckResponse,
    summary="Recherche fuzzy de doublons enseignants (Module 2)",
    dependencies=[Depends(require_roles(*CENSUS_WRITE_ROLES))],
)
async def check_teacher_duplicates(
    dto: TeacherDuplicateCheckRequest,
    user: CurrentUserDep,
    service: CensusSvc,
) -> TeacherDuplicateCheckResponse:
    return await service.check_teacher_duplicates(user, dto)


@router.patch(
    "/teachers/{teacher_id}/classes",
    response_model=TeacherRead,
    dependencies=[Depends(require_roles(*CENSUS_WRITE_ROLES))],
)
async def assign_teacher_classes(
    teacher_id: str,
    dto: AssignTeacherClassesRequest,
    user: CurrentUserDep,
    service: CensusSvc,
) -> TeacherRead:
    return await service.assign_teacher_classes(user, teacher_id, dto)


# --- QR / identification --------------------------------------------
@router.get(
    "/identify/{token}",
    response_model=IdentifyResponse,
    summary="Identifier une personne à partir d'un token / payload / uniqueCode QR",
)
async def identify(
    token: str, user: CurrentUserDep, service: CensusSvc
) -> IdentifyResponse:
    return await service.identify(user, token)


@router.get(
    "/qr/{token}",
    response_model=QrSvgResponse,
    summary="Identifier + rendre le QR SVG (220x220, niveau M)",
)
async def qr_svg(
    token: str, user: CurrentUserDep, service: CensusSvc
) -> QrSvgResponse:
    return await service.qr_svg(user, token)
