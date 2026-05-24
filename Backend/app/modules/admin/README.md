# Module 15 — Admin / Settings plateforme

Panneau de contrôle "régalien" du ministère : configurer la plateforme
**sans redéploiement**, basculer des fonctionnalités, et couper les
écritures en lecture seule globale.

## Surface fonctionnelle

| Brique                | Quoi                                                   |
|-----------------------|--------------------------------------------------------|
| **Settings typés**    | Clé / valeur stockés en JSONB, validés à l'écriture (boolean / int / float / string / json). |
| **Feature flags**     | `enabled` + `rolloutPercentage` ∈ [0, 100]. Évaluation stable par utilisateur (hash MD5). |
| **Maintenance mode**  | Bascule lecture seule globale. Le middleware HTTP renvoie `503` sur tous les `POST/PUT/PATCH/DELETE`. |
| **Audit**             | `SettingChangeLog` append-only : qui, quand, avant, après — pour les deux entités précédentes. |

## API HTTP

Tous les endpoints exigent `NATIONAL_ADMIN` ou `MINISTRY_ADMIN`.

```
GET    /api/admin/settings
PUT    /api/admin/settings/{key}            { value, type?, description? }
GET    /api/admin/feature-flags
PUT    /api/admin/feature-flags/{key}       { enabled, rolloutPercentage, description? }
POST   /api/admin/maintenance/enable
POST   /api/admin/maintenance/disable
GET    /api/admin/maintenance
GET    /api/admin/changes?key=&limit=
```

## Cache + invalidation

- `get_setting(key)` hit Redis `admin:setting:<key>` (TTL 30 s) avant la DB.
- `set_setting` invalide explicitement la clé après commit DB.
- Le mode maintenance utilise un flag miroir `admin:maintenance` côté
  Redis pour que le middleware n'aille jamais en DB sur le chemin chaud.

## Rollout déterministe

```python
bucket = int(hashlib.md5(f"{key}:{user_id}".encode()).hexdigest(), 16) % 100
enabled = bucket < rolloutPercentage
```

Le même couple `(flag_key, user_id)` renvoie toujours le même résultat,
quel que soit le worker — c'est la propriété attendue pour un canary
utilisable (un utilisateur ne voit pas la feature apparaître / disparaître
aléatoirement entre deux refresh).

## Middleware maintenance

`app/core/maintenance.py` est wired dans `app/main.py` juste après
`RequestIdMiddleware`. En cas de Redis indisponible, on **fail-open**
(loggé) — c'est volontaire : la panne du panneau admin ne doit pas
casser le service.

Routes exemptées (toujours autorisées en écriture, même en maintenance) :
`/health`, `/ready`, `/metrics`, `/api/admin/maintenance/*`, `/api/auth/login`.

## Migration

`alembic/versions/0020_admin_settings.py` :
- DROP de la table legacy `PlatformSetting` (stub Phase 13bis non wired).
- Crée `PlatformSetting`, `FeatureFlag`, `SettingChangeLog` + enum
  `SettingChangeKind`.
- Contrainte SQL : `rolloutPercentage BETWEEN 0 AND 100`.

## Tests

`tests/integration/test_admin_module15.py` — 13 tests couvrant settings
typés, audit, cache invalidation, feature flags (stable per user, rollout
0/100), maintenance (block writes / allow reads / disable), RBAC,
validation de type.
