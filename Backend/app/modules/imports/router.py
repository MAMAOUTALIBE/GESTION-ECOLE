from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Path, UploadFile, status
from fastapi.responses import Response

from app.modules.auth.models import User
from app.modules.imports.parsers import COLUMNS, ImportKind
from app.modules.imports.schemas import (
    ImportCommitRequest,
    ImportCommitResponse,
    ImportPreviewResponse,
)
from app.modules.imports.service import ImportsService
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

IMPORT_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN,
    UserRole.SCHOOL_DIRECTOR,
    UserRole.CENSUS_AGENT,
)


def _service(session: DbSession) -> ImportsService:
    return ImportsService(session)


ImpSvc = Annotated[ImportsService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

router = APIRouter(tags=["imports"])


@router.get(
    "/templates",
    summary="Liste des templates d'import disponibles avec leurs colonnes",
    dependencies=[Depends(require_roles(*IMPORT_ROLES))],
)
async def list_templates(
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[dict]:
    """Retourne les 3 kinds (students/teachers/schools) avec colonnes attendues."""
    _ = current_user
    labels = {
        "students": "Élèves",
        "teachers": "Enseignants",
        "schools": "Écoles",
    }
    return [
        {
            "kind": kind,
            "label": labels.get(kind, kind),
            "columns": cols,
            "downloadUrl": f"/api/imports/templates/{kind}",
        }
        for kind, cols in COLUMNS.items()
    ]


_KIND_PATH = Path(
    ...,
    pattern="^(students|teachers|schools)$",
    description="Type d'entités à importer",
)


@router.get(
    "/templates/{kind}",
    summary="Télécharger un template Excel vide pour ce type d'import",
    responses={200: {"content": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
    }}},
    dependencies=[Depends(require_roles(*IMPORT_ROLES))],
)
async def download_template(
    kind: Annotated[ImportKind, _KIND_PATH], user: CurrentUserDep
) -> Response:
    _ = user
    if kind not in COLUMNS:
        raise HTTPException(status_code=400, detail="Type d'import inconnu")
    content = ImportsService.template(kind)
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="template-{kind}.xlsx"'
            )
        },
    )


@router.post(
    "/{kind}/preview",
    response_model=ImportPreviewResponse,
    summary="Charger un fichier xlsx/csv et obtenir un aperçu validé (sync)",
    dependencies=[Depends(require_roles(*IMPORT_ROLES))],
)
async def preview(
    kind: Annotated[ImportKind, _KIND_PATH],
    user: CurrentUserDep,
    service: ImpSvc,
    file: Annotated[UploadFile, File(description="Fichier xlsx ou csv (max 10 Mo)")],
) -> ImportPreviewResponse:
    if kind not in COLUMNS:
        raise HTTPException(status_code=400, detail="Type d'import inconnu")
    content = await file.read()
    return await service.preview(user, kind, content)


@router.post(
    "/{kind}/commit",
    response_model=ImportCommitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Confirmer l'import des lignes validées (queue Celery)",
    dependencies=[Depends(require_roles(*IMPORT_ROLES))],
)
async def commit(
    kind: Annotated[ImportKind, _KIND_PATH],
    dto: ImportCommitRequest,
    user: CurrentUserDep,
    service: ImpSvc,
) -> ImportCommitResponse:
    if kind not in COLUMNS:
        raise HTTPException(status_code=400, detail="Type d'import inconnu")
    return await service.commit(user, kind, dto)
