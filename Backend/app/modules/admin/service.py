"""Module 15 — AdminService : settings typés, feature flags, maintenance.

Stratégie cache
---------------
Les ``get_setting`` lisent depuis Redis (clé ``admin:setting:<key>``, TTL
30 s). Les ``set_setting`` invalidant explicitement la clé après commit
DB → cohérence read-after-write garantie pour le même thread, et
convergence < 30 s pour les autres workers.

Le mode maintenance utilise le même mécanisme : la clé canonique reste
``platform.maintenance_mode`` (persisté en DB pour audit) et un flag
miroir ``admin:maintenance`` est posé / supprimé dans Redis pour que le
middleware HTTP n'ait jamais à hit la DB (chemin chaud).

Rollout des feature flags
-------------------------
``is_feature_enabled_for_user(key, user_id)`` :
* si flag absent ou ``enabled=False`` → False
* sinon ``hash(key + ":" + user_id) % 100 < rolloutPercentage``

L'usage de MD5 (déterministe, distribution uniforme) garantit que pour un
même couple (clé, user) la réponse reste stable, indépendamment du worker
qui répond. Si ``user_id`` est ``None`` (anonyme), on évalue uniquement
``rolloutPercentage == 100``.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, cast

from loguru import logger
from redis.asyncio import Redis
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ValidationFailedError
from app.modules.admin.enums import SettingChangeKind, SettingType
from app.modules.admin.models import (
    FeatureFlag,
    PlatformSetting,
    SettingChangeLog,
)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
SETTING_CACHE_PREFIX = "admin:setting:"
SETTING_CACHE_TTL_SECONDS = 30

MAINTENANCE_KEY = "platform.maintenance_mode"
MAINTENANCE_REDIS_KEY = "admin:maintenance"


# ---------------------------------------------------------------------------
# Validation typée
# ---------------------------------------------------------------------------
def _validate_value_against_type(value: Any, type_: str) -> Any:
    """Vérifie + coerce ``value`` selon ``type_`` (string SettingType).

    * boolean : isinstance bool exact (refuse les 0/1 implicites)
    * int     : isinstance int + pas un bool
    * float   : accepte int ou float (cast en float)
    * string  : isinstance str
    * json    : accepte tout JSON-sérialisable (dict/list/scalar)
    """
    if type_ == SettingType.BOOLEAN.value:
        if not isinstance(value, bool):
            raise ValidationFailedError(
                detail=f"Type 'boolean' attendu, reçu {type(value).__name__}",
            )
        return value
    if type_ == SettingType.INT.value:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationFailedError(
                detail=f"Type 'int' attendu, reçu {type(value).__name__}",
            )
        return value
    if type_ == SettingType.FLOAT.value:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValidationFailedError(
                detail=f"Type 'float' attendu, reçu {type(value).__name__}",
            )
        return float(value)
    if type_ == SettingType.STRING.value:
        if not isinstance(value, str):
            raise ValidationFailedError(
                detail=f"Type 'string' attendu, reçu {type(value).__name__}",
            )
        return value
    if type_ == SettingType.JSON.value:
        try:
            json.dumps(value)  # sanity check sérialisable
        except (TypeError, ValueError) as e:
            raise ValidationFailedError(
                detail=f"Valeur non JSON-sérialisable : {e}",
            ) from e
        return value
    raise ValidationFailedError(detail=f"Type '{type_}' inconnu")


# ---------------------------------------------------------------------------
# AdminService
# ---------------------------------------------------------------------------
class AdminService:
    """Façade transactionnelle pour la configuration runtime + flags."""

    def __init__(self, session: AsyncSession, redis: Redis | None = None) -> None:
        self.session = session
        self._redis = redis

    @property
    def redis(self) -> Redis:
        """Lazy redis client (au cas où il serait None à l'init)."""
        if self._redis is None:
            from app.core.redis import get_redis
            self._redis = get_redis()
        return self._redis

    # ----- Settings ----------------------------------------------------------
    async def get_setting(self, key: str, default: Any = None) -> Any:
        """Lit un paramètre (cache Redis 30 s + DB fallback)."""
        cache_key = f"{SETTING_CACHE_PREFIX}{key}"
        try:
            cached = await self.redis.get(cache_key)
        except Exception as exc:  # pragma: no cover - redis blip
            logger.warning("admin: cache get failed for {}: {}", key, exc)
            cached = None
        if cached is not None:
            try:
                return json.loads(cached)
            except (TypeError, ValueError):
                pass  # cache corrompu — relit en DB

        setting = (await self.session.execute(
            select(PlatformSetting).where(PlatformSetting.key == key)
        )).scalar_one_or_none()
        if setting is None:
            return default

        value = setting.valueJson
        try:
            await self.redis.setex(
                cache_key, SETTING_CACHE_TTL_SECONDS, json.dumps(value),
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("admin: cache set failed for {}: {}", key, exc)
        return value

    async def set_setting(
        self,
        key: str,
        value: Any,
        *,
        type_: str | None = None,
        description: str | None = None,
        actor_id: str | None = None,
    ) -> PlatformSetting:
        """Crée ou met à jour un paramètre + audit + invalidation cache."""
        existing = (await self.session.execute(
            select(PlatformSetting).where(PlatformSetting.key == key)
        )).scalar_one_or_none()

        # Résout le type final
        effective_type = type_ or (existing.type if existing else SettingType.STRING.value)
        validated = _validate_value_against_type(value, effective_type)

        old_value: Any | None = None
        if existing is None:
            setting = PlatformSetting(
                key=key,
                type=effective_type,
                valueJson=validated,
                description=description,
                updatedById=actor_id,
            )
            self.session.add(setting)
        else:
            old_value = existing.valueJson
            existing.type = effective_type
            existing.valueJson = validated
            if description is not None:
                existing.description = description
            existing.updatedById = actor_id
            setting = existing

        await self.session.flush()

        # Audit
        self.session.add(SettingChangeLog(
            key=key,
            kind=SettingChangeKind.SETTING.value,
            oldValue=old_value,
            newValue=validated,
            changedById=actor_id,
        ))
        await self.session.commit()
        await self.session.refresh(setting)

        # Invalidation cache (best-effort)
        await self._invalidate_setting_cache(key)

        # Effet de bord : maintenance flag miroir Redis
        if key == MAINTENANCE_KEY:
            await self._mirror_maintenance_flag(bool(validated))

        return setting

    async def list_settings(self) -> list[PlatformSetting]:
        rows = (await self.session.execute(
            select(PlatformSetting).order_by(PlatformSetting.key.asc())
        )).scalars().all()
        return list(rows)

    async def _invalidate_setting_cache(self, key: str) -> None:
        try:
            await self.redis.delete(f"{SETTING_CACHE_PREFIX}{key}")
        except Exception as exc:  # pragma: no cover
            logger.warning("admin: cache invalidation failed for {}: {}", key, exc)

    # ----- Feature flags -----------------------------------------------------
    async def get_feature_flag(self, key: str) -> FeatureFlag | None:
        return (await self.session.execute(
            select(FeatureFlag).where(FeatureFlag.key == key)
        )).scalar_one_or_none()

    async def list_feature_flags(self) -> list[FeatureFlag]:
        rows = (await self.session.execute(
            select(FeatureFlag).order_by(FeatureFlag.key.asc())
        )).scalars().all()
        return list(rows)

    async def set_feature_flag(
        self,
        key: str,
        *,
        enabled: bool,
        rollout_percentage: int,
        description: str | None = None,
        actor_id: str | None = None,
    ) -> FeatureFlag:
        if not (0 <= rollout_percentage <= 100):
            raise ValidationFailedError(
                detail="rolloutPercentage doit être dans [0, 100]",
            )

        existing = await self.get_feature_flag(key)
        old_state: dict[str, Any] | None = None
        if existing is None:
            flag = FeatureFlag(
                key=key,
                enabled=enabled,
                rolloutPercentage=rollout_percentage,
                description=description,
            )
            self.session.add(flag)
        else:
            old_state = {
                "enabled": existing.enabled,
                "rolloutPercentage": existing.rolloutPercentage,
            }
            existing.enabled = enabled
            existing.rolloutPercentage = rollout_percentage
            if description is not None:
                existing.description = description
            flag = existing

        await self.session.flush()

        new_state = {
            "enabled": flag.enabled,
            "rolloutPercentage": flag.rolloutPercentage,
        }
        self.session.add(SettingChangeLog(
            key=key,
            kind=SettingChangeKind.FEATURE_FLAG.value,
            oldValue=old_state,
            newValue=new_state,
            changedById=actor_id,
        ))
        await self.session.commit()
        await self.session.refresh(flag)
        return flag

    async def is_feature_enabled_for_user(
        self, key: str, user_id: str | None,
    ) -> bool:
        """Évalue un flag pour un user donné (stable et déterministe)."""
        flag = await self.get_feature_flag(key)
        if flag is None or not flag.enabled:
            return False
        if flag.rolloutPercentage >= 100:
            return True
        if flag.rolloutPercentage <= 0:
            return False
        if user_id is None:
            # Anonyme : on n'active que si rollout = 100 (déjà couvert).
            return False
        token = f"{key}:{user_id}".encode()
        # MD5 ici n'a pas de rôle cryptographique : c'est juste un hash
        # déterministe pour bucketiser les users — donc S324 ne s'applique
        # pas. On garde MD5 pour la stabilité historique de la distribution.
        bucket = int(hashlib.md5(token, usedforsecurity=False).hexdigest(), 16) % 100
        return bucket < flag.rolloutPercentage

    # ----- Maintenance mode --------------------------------------------------
    async def enable_maintenance_mode(self, *, actor_id: str | None = None) -> bool:
        await self.set_setting(
            MAINTENANCE_KEY,
            True,
            type_=SettingType.BOOLEAN.value,
            description="Mode lecture seule global (Module 15)",
            actor_id=actor_id,
        )
        return True

    async def disable_maintenance_mode(self, *, actor_id: str | None = None) -> bool:
        await self.set_setting(
            MAINTENANCE_KEY,
            False,
            type_=SettingType.BOOLEAN.value,
            description="Mode lecture seule global (Module 15)",
            actor_id=actor_id,
        )
        return False

    async def is_maintenance_mode(self) -> bool:
        """Lecture rapide depuis Redis (chemin chaud middleware)."""
        try:
            val = await self.redis.get(MAINTENANCE_REDIS_KEY)
        except Exception:  # pragma: no cover
            val = None
        if val is not None:
            return val in {"1", "true", "True"}
        # Fallback DB (premier appel après reboot Redis ou cold start)
        result = cast(bool, await self.get_setting(MAINTENANCE_KEY, default=False))
        await self._mirror_maintenance_flag(result)
        return result

    async def _mirror_maintenance_flag(self, enabled: bool) -> None:
        try:
            if enabled:
                await self.redis.set(MAINTENANCE_REDIS_KEY, "1")
            else:
                await self.redis.delete(MAINTENANCE_REDIS_KEY)
        except Exception as exc:  # pragma: no cover
            logger.warning("admin: maintenance flag mirror failed: {}", exc)

    # ----- Audit -------------------------------------------------------------
    async def list_changes(
        self, *, key: str | None = None, limit: int = 100,
    ) -> list[SettingChangeLog]:
        # Tri DESC sur (changedAt, id) — l'id est un cuid lexicographique
        # monotone, donc le tie-breaker garantit un ordre stable et le
        # "plus récent" même si deux writes tombent dans la même
        # microseconde (cas typique en tests).
        stmt = select(SettingChangeLog).order_by(
            desc(SettingChangeLog.changedAt), desc(SettingChangeLog.id),
        )
        if key is not None:
            stmt = stmt.where(SettingChangeLog.key == key)
        stmt = stmt.limit(min(max(limit, 1), 500))
        rows = (await self.session.execute(stmt)).scalars().all()
        return list(rows)


# ---------------------------------------------------------------------------
# Reexports utiles
# ---------------------------------------------------------------------------
__all__ = [
    "MAINTENANCE_KEY",
    "MAINTENANCE_REDIS_KEY",
    "SETTING_CACHE_PREFIX",
    "SETTING_CACHE_TTL_SECONDS",
    "AdminService",
    "ConflictError",
]
