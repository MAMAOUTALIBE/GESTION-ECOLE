from typing import Annotated

from fastapi import APIRouter, Depends

from app.modules.auth.models import User
from app.modules.library.schemas import (
    InventoryPage,
    LibraryInventoryQuery,
    LibraryLoansQuery,
    LoansPage,
)
from app.modules.library.service import LibraryService
from app.shared.deps import DbSession, get_current_user


def _service(session: DbSession) -> LibraryService:
    return LibraryService(session)


LibSvc = Annotated[LibraryService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

router = APIRouter(tags=["library"])


@router.get(
    "/inventory",
    response_model=InventoryPage,
    summary="Inventaire bibliothèque (paginé + recherche + filtres)",
)
async def list_inventory(
    user: CurrentUserDep,
    service: LibSvc,
    query: Annotated[LibraryInventoryQuery, Depends()],
) -> InventoryPage:
    return await service.list_inventory(user, query)


@router.get(
    "/loans",
    response_model=LoansPage,
    summary="Prêts bibliothèque (paginé + recherche + filtres)",
)
async def list_loans(
    user: CurrentUserDep,
    service: LibSvc,
    query: Annotated[LibraryLoansQuery, Depends()],
) -> LoansPage:
    return await service.list_loans(user, query)
