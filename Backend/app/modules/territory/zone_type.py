"""Module 1C — Helper "zone effective" pour une école.

Une école hérite de la zone (URBAN / RURAL / PERI_URBAN) déclarée par
l'INS sur sa sous-préfecture, SAUF si elle pose un override explicite
(``School.zoneType``). Ce module centralise le calcul pour qu'aucune
autre couche n'ait à dupliquer la règle.

Convention :
* ``School.zoneType`` non-null   → c'est la valeur effective (override).
* ``School.zoneType`` est null   → on prend ``SubPrefecture.defaultZoneType``.
* Si une école n'a pas de sous-préfecture rattachée (cas legacy : les
  ``School.subPrefectureId`` historiques sont nullables), on retombe sur
  ``RURAL`` comme valeur de précaution — le service de migration des
  données doit poser un subPrefectureId, mais on évite un 500 si quelqu'un
  oublie.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.schools.models import School
from app.modules.territory.models import SubPrefecture
from app.shared.enums import ZoneType


def effective_zone_type(
    school: School,
    sub_prefecture: SubPrefecture | None,
) -> ZoneType:
    """Retourne la zone effective d'une école (override OU défaut sous-préf).

    Args:
        school : instance School chargée (au minimum ``zoneType``).
        sub_prefecture : la sous-préfecture rattachée (peut être None si
            l'école est legacy/sans rattachement, dans ce cas on retombe
            sur RURAL).

    Returns:
        Une valeur de ``ZoneType`` — toujours définie (jamais None).
    """
    if school.zoneType is not None:
        return school.zoneType
    if sub_prefecture is not None:
        return sub_prefecture.defaultZoneType
    return ZoneType.RURAL


async def get_effective_zone_for_school_id(
    session: AsyncSession,
    school_id: str,
) -> ZoneType:
    """Charge l'école + sa sous-préf et calcule la zone effective.

    Convient pour les usages ponctuels (API, détecteurs). Pour les
    agrégations en bulk, préférer la jointure SQL avec
    ``COALESCE(School.zoneType, SubPrefecture.defaultZoneType)`` (cf.
    ``EnrollmentService.aggregate`` byZoneType=True).
    """
    stmt = (
        select(School.zoneType, SubPrefecture.defaultZoneType)
        .outerjoin(SubPrefecture, SubPrefecture.id == School.subPrefectureId)
        .where(School.id == school_id)
    )
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        # School introuvable — on remonte un défaut au lieu d'exploser ;
        # le code appelant remontera lui-même un 404 si besoin.
        return ZoneType.RURAL
    school_override, sub_default = row
    if school_override is not None:
        return school_override
    if sub_default is not None:
        return sub_default
    return ZoneType.RURAL


__all__ = [
    "effective_zone_type",
    "get_effective_zone_for_school_id",
]
