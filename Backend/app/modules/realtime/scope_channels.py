"""Module 13 — Résolution des channels Redis Pub/Sub pour un utilisateur.

Politique de scope (alignée sur ``app.shared.permissions``) :

* `NATIONAL_ADMIN` / `MINISTRY_ADMIN` : reçoivent TOUT (global + tout sous-arbre).
* `INSPECTOR` : reçoit le global (cockpit national lecture seule).
* `REGIONAL_ADMIN` : reçoit `region:<id>` + `global` (alertes ministérielles).
* `PREFECTURE_ADMIN` / `SUB_PREFECTURE_ADMIN` : reçoit `region:<id>` parent +
  `global`. NB: pas de channel "prefecture:<id>" — la granularité reste régionale
  car les évènements n'ont pas tous un prefectureId fiable au moment d'émettre.
* `SCHOOL_DIRECTOR` / `TEACHER` / `CENSUS_AGENT` : reçoivent `school:<id>` +
  `region:<id>` (annonces) + `global`.

Si l'utilisateur n'a aucun scope rattaché (ex: SCHOOL_DIRECTOR sans schoolId),
on retourne uniquement `global` — le client peut quand même recevoir les
alertes ministérielles.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.realtime.events import CHANNEL_PREFIX, GLOBAL_CHANNEL
from app.shared.enums import UserRole

if TYPE_CHECKING:
    from app.modules.auth.models import User


# Roles qui reçoivent absolument tout, indépendamment du scope.
_NATIONAL_ROLES = frozenset(
    {UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN, UserRole.INSPECTOR}
)
# Roles avec scope régional explicite.
_REGIONAL_ROLES = frozenset(
    {
        UserRole.REGIONAL_ADMIN,
        UserRole.PREFECTURE_ADMIN,
        UserRole.SUB_PREFECTURE_ADMIN,
    }
)
# Roles avec scope école.
_SCHOOL_ROLES = frozenset(
    {UserRole.SCHOOL_DIRECTOR, UserRole.TEACHER, UserRole.CENSUS_AGENT}
)


def channels_for_user(user: User) -> list[str]:
    """Retourne la liste des channels Redis auxquels l'utilisateur s'abonne.

    L'ordre n'est pas significatif (Redis PubSub n'a pas de priorité), mais
    on évite les doublons (un school director sans regionId ne s'abonne pas
    deux fois au global).
    """
    channels: list[str] = [GLOBAL_CHANNEL]

    role = user.role
    if role in _NATIONAL_ROLES:
        # Pour le national, on ne s'abonne PAS à chaque region:* nominativement
        # (trop coûteux). À la place, tous les events à scope régional sont
        # ALSO publiés sur `global` (cf. Event.channels()), donc s'abonner à
        # `global` suffit pour tout voir. Le rôle national peut, optionnellement,
        # s'abonner aux patterns via `psubscribe gestionee:events:*` mais on
        # garde la version simple : tout passe par `global`.
        return channels

    if role in _REGIONAL_ROLES and user.regionId:
        channels.append(f"{CHANNEL_PREFIX}:region:{user.regionId}")
        return channels

    if role in _SCHOOL_ROLES:
        if user.schoolId:
            channels.append(f"{CHANNEL_PREFIX}:school:{user.schoolId}")
        if user.regionId:
            channels.append(f"{CHANNEL_PREFIX}:region:{user.regionId}")
        return channels

    # Fallback (rôle inconnu ou pas de scope rattaché) : on garde au moins
    # le global pour que l'utilisateur reçoive les annonces système.
    return channels


__all__ = ["channels_for_user"]
