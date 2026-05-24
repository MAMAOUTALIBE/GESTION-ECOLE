# Module Attendance — GESTION-EE Backend

Module en charge de l'enregistrement des présences (élèves & enseignants)
via scans QR, et de l'agrégation statistique pour les dashboards.

## Surface API

| Méthode | URL | Rôles | Description |
|---|---|---|---|
| `GET`  | `/api/attendance/today`                | tout authentifié (scoped)               | Scans du jour, filtrés par scope territorial |
| `POST` | `/api/attendance/scan`                 | TEACHER → NATIONAL_ADMIN                | Enregistre 1 scan QR (dédup. journée) |
| `POST` | `/api/attendance/bulk`                 | SCHOOL_DIRECTOR → NATIONAL_ADMIN        | Ingestion en lot (≤ 200 scans) idempotente |
| `GET`  | `/api/attendance/stats`                | TEACHER → NATIONAL_ADMIN                | Stats agrégées par bucket (day/week/month) |
| `GET`  | `/api/attendance/partitions`           | NATIONAL_ADMIN, MINISTRY_ADMIN          | Liste les partitions (debug / monitoring) |
| `POST` | `/api/attendance/partitions/ensure`    | NATIONAL_ADMIN, MINISTRY_ADMIN          | Pré-crée les partitions futures |

### POST /bulk
- Body : `{ items: BulkScanItem[] }` (1 à 200 items).
- Idempotent par couple `(student|teacher, jour UTC)`.
- Refuse les `scannedAt` dans le futur (item en erreur, le batch continue).
- Retourne `{ inserted, skipped, errors: [{index, reason}], by_status }`.
- Audit : un `AuditLog` `BULK_ATTENDANCE_SCAN` agrégé par appel.

### GET /stats
- Query params : `schoolId | classRoomId | studentId` (au moins un requis),
  `dateFrom`, `dateTo` (max 366j), `groupBy=day|week|month`.
- Scope appliqué : un directeur ne peut pas demander une autre école.
- Cache Redis 60s, clé `attendance:stats:<sha1(filters+role+scope)>`.

## Partitionnement déclaratif (Module 3)

### Pourquoi ?

À l'échelle nationale visée (3M élèves × ~200 jours = 600M lignes/an), une
table non partitionnée :
- ralentit les dashboards (full scan sur des mois) ;
- bloque les VACUUM / ANALYZE sur des verrous longs ;
- rend les sauvegardes monolithiques.

### Choix techniques

- **Postgres 16 natif** (pas pg_partman) — moins de deps OS, contrôle
  total via Alembic, partition pruning automatique sur `scannedAt`.
- **Par mois** (pas semaine ni jour) : ~50M lignes/partition à l'échelle
  cible, sweet spot pour planner / VACUUM. Le jour serait trop verbeux
  (3 600+ partitions sur 10 ans), l'année trop grossière pour scan.
- **PK composite `(id, scannedAt)`** : PostgreSQL impose que la partition
  key soit incluse dans toute contrainte d'unicité. L'unicité fonctionnelle
  reste portée par `id` (cuid généré).
- **Migration zero-downtime simplifiée** : rename legacy → create
  partitioned → `INSERT INTO ... SELECT` → drop legacy CASCADE. Aucun
  FK ne pointe vers `AttendanceRecord` (vérifié dans `census/`/`schools/`).

### Layout

```
AttendanceRecord                   (parent — PARTITION BY RANGE)
├── AttendanceRecord_2026_05
├── AttendanceRecord_2026_06
├── ...
├── AttendanceRecord_2027_04
└── AttendanceRecord_default       (catch-all hors range)
```

Tous les indexes sont définis sur la **table parente** et propagés
automatiquement à chaque partition (existante et future).

### Rolling 3 mois en avance

- `app.workers.attendance_tasks.ensure_attendance_partitions_task` ;
- pré-crée les partitions du mois courant + N futurs (default 3) ;
- idempotent (re-run safe via `IF NOT EXISTS`).

Pour activer le cron (à faire côté ops, hors scope Module 3) :

```python
# dans app/core/celery_app.py
from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    "attendance-ensure-partitions": {
        "task": "attendance.ensure_partitions",
        "schedule": crontab(hour=2, minute=0),
        "kwargs": {"months_ahead": 3},
    },
}
```

En attendant, l'endpoint `POST /api/attendance/partitions/ensure` permet
de déclencher manuellement la même opération.

## Tests

Les fixtures pytest (cf. `tests/integration/conftest.py`) utilisent
`Base.metadata.create_all()` qui ne sait pas générer la syntaxe
`PARTITION BY` (non supportée par SQLAlchemy). Le test module 3
(`tests/integration/test_attendance_module3.py`) fournit une fixture
`attendance_partitioned_table` qui :

1. drope la table créée par `create_all` ;
2. recrée la table partitionnée via SQL brut (équivalent à la migration
   0010) ;
3. pré-crée les partitions du mois courant et des 3 mois suivants.

Cette fixture est requise dès qu'un test interroge `pg_inherits` ou
attend qu'une insertion atterrisse dans une partition spécifique. Les
tests non liés au partitionnement utilisent la table standard
(non-partitionnée) générée par `create_all` — c'est suffisant pour la
sémantique ORM testée.
