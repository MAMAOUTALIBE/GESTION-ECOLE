# Module 1A — Enrollment désagrégé

Fondation Phase 1 carte scolaire IIPE. Stocke les effectifs déclarés
annuellement par établissement, désagrégés par **niveau scolaire × genre**.

## Contexte métier

Sans désagrégation, impossible de produire les indicateurs qui orientent
les décisions publiques :

- **Indice de parité fille / garçon (GPI)** — Module 1B.
- **Dashboard équité** par région / niveau — Module 1D.
- **Projection cohorte** (flux d'élèves CP1 → CM2 sur 6 ans) — Phase 2.

Le déclaratif officiel (recensement annuel) reste la **source de vérité**
pour le pilotage cabinet : la table `Student` est partielle (la fiche
peut prendre des semaines à arriver depuis la brousse) et destinée à
être anonymisée / purgée pour respecter le RGPD enfants.

## Modèle

### Table `Enrollment`

| Colonne          | Type                       | Notes                                    |
|------------------|----------------------------|------------------------------------------|
| `id`             | cuid (PK)                  |                                          |
| `schoolYearId`   | FK SchoolYear              |                                          |
| `schoolId`       | FK School                  |                                          |
| `classLevel`     | enum EnrollmentClassLevel  | maternelle 1/2/3 + CP1..CM2              |
| `gender`         | enum Gender                | FEMALE / MALE / OTHER                    |
| `count`          | INTEGER ≥ 0                | validé Pydantic + service                |
| `source`         | enum EnrollmentSource      | CENSUS_DECLARED / COMPUTED / IMPORT      |
| `recordedAt`     | timestamptz                | horloge serveur, pas client              |
| `recordedById`   | FK User (nullable)         | nullable = imports historiques            |
| `notes`          | text (≤ 500)               |                                          |
| `createdAt`      | timestamptz                | TimestampMixin                            |
| `updatedAt`      | timestamptz                | TimestampMixin                            |

### Contraintes

- **Unique** `(schoolYearId, schoolId, classLevel, gender, source)` — autorise
  la coexistence d'une `CENSUS_DECLARED` et d'une `COMPUTED_FROM_STUDENTS`
  pour la même cellule (cross-check data quality).
- Indexes : `(schoolYearId, schoolId)` (UI saisie), `(schoolYearId, classLevel, gender)`
  (agrégats nationaux).

## Endpoints

| Méthode | Route                                   | RBAC                                                |
|---------|------------------------------------------|-----------------------------------------------------|
| POST    | `/api/enrollment`                       | `ENROLLMENT_WRITE_ROLES` (admins + CENSUS_AGENT + SCHOOL_DIRECTOR) |
| POST    | `/api/enrollment/bulk`                  | idem                                                 |
| GET     | `/api/enrollment/school/{school_id}`    | authentifié (scope RBAC automatique)                 |
| GET     | `/api/enrollment/aggregate`             | authentifié (scope RBAC automatique)                 |
| POST    | `/api/enrollment/compute-from-students` | `NATIONAL_ADMIN` ou `MINISTRY_ADMIN`                 |

- `TEACHER` et `INSPECTOR` ne peuvent pas écrire (pas leur métier).
- `bulk_record` plafonné à 200 items pour éviter de bloquer la transaction.
- `aggregate` parallélise 3 sous-requêtes (`asyncio.gather`) : agrégat par
  niveau, par genre, breakdown niveau × genre.

## RBAC territorial

Mêmes patterns que le module `census` :

- `NATIONAL_ADMIN` / `MINISTRY_ADMIN` : portée pays.
- `REGIONAL_ADMIN` / `INSPECTOR` : portée région (via `user.regionId`).
- `PREFECTURE_ADMIN` : portée préfecture.
- `SUB_PREFECTURE_ADMIN` : portée sous-préfecture.
- `SCHOOL_DIRECTOR` / `TEACHER` / `CENSUS_AGENT` : portée école (via `user.schoolId`).

Lecture comme écriture appliquent ces filtres : un `SCHOOL_DIRECTOR` ne
voit que les rows de son école même via `/aggregate`.

## Conflit déclaratif vs calculé

| Source                     | Origine                                      | Confiance               |
|----------------------------|----------------------------------------------|-------------------------|
| `CENSUS_DECLARED`          | Saisie agent recensement / SCHOOL_DIRECTOR   | Vérité officielle       |
| `COMPUTED_FROM_STUDENTS`   | Recalcul depuis `Student` (admin central)    | Signal data quality     |
| `IMPORT`                   | Bulk historique avant migration logiciel     | Variable (notes)        |

Le `/aggregate` filtre par `source` (default = `CENSUS_DECLARED`). On peut
demander explicitement `source=COMPUTED_FROM_STUDENTS` pour comparer.

## GPI (Gender Parity Index)

Module 1B — Indice de parité fille/garçon, fondement des indicateurs
d'équité scolaire UNESCO/IIPE. Formule : `gpi = girls / boys`.

### Seuils UNESCO

| Plage         | Sévérité           | Sens métier                                            |
|---------------|--------------------|--------------------------------------------------------|
| `0.97 .. 1.03`| `NORMAL`           | Parité acceptable.                                     |
| `0.85 .. 0.97`| `WARNING_GIRLS`    | Disparité au détriment des filles — à investiguer.      |
| `< 0.85`      | `CRITICAL_GIRLS`   | Point chaud — déclenche une alerte ministérielle.       |
| `> 1.03`      | `WARNING_BOYS`     | Disparité au détriment des garçons.                     |
| `MALE_ABSENT` | `CRITICAL_GIRLS`   | Cohorte 100 % filles (sentinelle `Decimal(999.9999)`).  |

### Division par zéro

| Cas                       | Retour de `compute_gpi`                  | Sévérité          |
|---------------------------|------------------------------------------|-------------------|
| `girls == 0 and boys == 0`| `None`                                    | `NORMAL` (vide)   |
| `boys == 0 and girls > 0` | `Decimal("999.9999")` (`MALE_ABSENT_GPI`) | `CRITICAL_GIRLS`  |

Les calculs utilisent **toujours `Decimal`** (jamais `float`) pour
garantir une précision à 4 décimales — chiffres ré-utilisés dans des
rapports gouvernementaux.

### Stockage

Table `GpiSnapshot` (migration `0024_gender_parity`) :

| Colonne        | Type                | Notes                                       |
|----------------|---------------------|---------------------------------------------|
| `id`           | cuid (PK)           |                                             |
| `schoolYearId` | FK SchoolYear       |                                             |
| `scope`        | enum GpiScope       | NATIONAL / REGIONAL / PREFECTURE / SCHOOL   |
| `entityId`     | nullable            | NULL si scope = NATIONAL                     |
| `girlsCount`   | int                 |                                             |
| `boysCount`    | int                 |                                             |
| `gpi`          | NUMERIC(6,4)        | nullable (cohorte vide)                      |
| `severity`     | enum GpiSeverity    | pré-calculé pour filtre indexé              |
| `computedAt`   | timestamptz         |                                             |

Index :
- `(schoolYearId, scope, severity)` — points chauds nationaux (cockpit).
- `(entityId, computedAt DESC)` — séries temporelles d'une école.

### Endpoints

| Méthode | Route                                          | RBAC                              |
|---------|------------------------------------------------|-----------------------------------|
| POST    | `/api/enrollment/gpi/compute-snapshots`        | NATIONAL_ADMIN / MINISTRY_ADMIN   |
| GET     | `/api/enrollment/gpi`                          | Tous (scope RBAC vérifié)          |
| GET     | `/api/enrollment/gpi/critical-schools`         | Tous (filtre territorial appliqué) |
| GET     | `/api/enrollment/gpi/evolution`                | Tous (scope RBAC vérifié)          |

Le service `EnrollmentService.compute_gpi_snapshots` :

1. Recalcule à 4 échelons (école, préfecture, région, national).
2. Persiste les snapshots (idempotent : `DELETE WHERE schoolYearId=...`
   puis `INSERT`).
3. Invalide le cache Redis (`enrollment:gpi:*`).
4. Crée les `AnomalyDetection(type=CRITICAL_GPI)` Module 9 pour chaque
   école sous le seuil 0.85.

Le cache Redis (5 min) sur `get_gpi` évite de re-frapper la DB pour
chaque hit du cockpit. Le snapshot étant déjà la valeur "pré-calculée",
les hits sont des `SELECT ... LIMIT 1`.

### Hook Module 19 — cockpit

`KpiKey.NATIONAL_GPI` est ajouté à l'enum cockpit. `CockpitService
.get_national_kpis()` lit le dernier snapshot `scope=NATIONAL` et
expose `nationalGpi: Decimal | None` dans la réponse.

### Hook Module 9 — anomalies

`AnomalyType.CRITICAL_GPI` est ajouté à l'enum. Le détecteur
`detect_critical_gpi(session, school_year_id)` lit `GpiSnapshot` et
matérialise une `AnomalyDetection` (severity=HIGH) par école touchée.
Le détecteur est invoqué automatiquement par
`compute_gpi_snapshots` (hook intégré, pas besoin d'orchestration ext.).

### Beat Celery

Tâche : `enrollment.compute_gpi_snapshots` (dans
`app.workers.enrollment_tasks`).

Planification recommandée (à ajouter à `celery_app.conf.beat_schedule`
côté ops, hors code applicatif) :

```python
from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    "compute-gpi-snapshots-weekly": {
        "task": "enrollment.compute_gpi_snapshots",
        "schedule": crontab(hour=3, minute=0, day_of_week=0),  # dim 03:00 UTC
    },
}
```

Déclenchement manuel : `compute_gpi_snapshots_task.delay(year_id)`
(ou `delay()` sans args → utilise la SchoolYear active).

## Migration

- `0023_enrollment` : table `Enrollment` (Module 1A).
- `0024_gender_parity` : table `GpiSnapshot` (Module 1B) + ajoute
  `CRITICAL_GPI` à `AnomalyType` + `NATIONAL_GPI` à `KpiKey`.

## Backlog

| Issue   | Description                                                                |
|---------|----------------------------------------------------------------------------|
| —       | Aucun connu (voir Module 1D pour dashboard équité complet).                |
