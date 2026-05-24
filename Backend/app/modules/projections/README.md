# Module 2A — Taux de transition par cohortes (IIPE-UNESCO)

Module Backend Python — fondation Phase 2 carte scolaire.

## Objectif métier

Le taux de transition d'un niveau N vers N+1 est l'indicateur clef de la
projection IIPE-UNESCO. Il mesure la part d'élèves qui passent
effectivement au niveau supérieur d'une année à l'autre.

```
tt(region, levelN→levelN+1, gender, year_t) =
   enrollment[region, levelN+1, gender, year_t+1]
   /
   enrollment[region, levelN, gender, year_t]
```

Trois usages prioritaires :

1. **Piloter la scolarisation des filles** — rate `FEMALE` vs `MALE` sur
   chaque paire (CP1→CP2, …, CM1→CM2) — détecte les ruptures de
   parcours genrées.
2. **Détecter les abandons massifs** — rate < 0.5 = signal d'une
   cohorte qui décroche entre deux niveaux.
3. **Alimenter le Module 2B (projection cohorte)** — sans rates
   persistés, la projection se ferait dans le vide.

## Architecture

```
app/modules/projections/
├── __init__.py
├── enums.py          # TransitionScope (NATIONAL / REGIONAL)
├── models.py         # TransitionRate (SQLAlchemy)
├── schemas.py        # Pydantic (ComputeTransitionsRequest/Response, …)
├── transitions.py    # Logique pure : compute_rate, LEVEL_PAIRS
├── service.py        # TransitionRateService (compute, list, outliers)
├── router.py         # POST /compute, GET /, GET /outliers
└── README.md         # ce fichier
```

Worker Celery dédié : `app/workers/projection_tasks.py`
(`compute_transitions_task`). À déclencher manuellement post-recensement —
pas en auto, car une année doit être clôturée par décision MEN.

## Modèle de données

Table `TransitionRate` :

| Colonne             | Type            | Notes                                  |
| ------------------- | --------------- | -------------------------------------- |
| `id`                | `String(30) PK` | cuid                                   |
| `schoolYearFromId`  | `FK SchoolYear` | année source                           |
| `schoolYearToId`    | `FK SchoolYear` | année cible (la suivante)              |
| `scope`             | `TransitionScope` | NATIONAL / REGIONAL                    |
| `entityId`          | `String(30)?`   | regionId si REGIONAL, NULL si NATIONAL |
| `classLevelFrom`    | `EnrollmentClassLevel` | niveau source                    |
| `classLevelTo`      | `EnrollmentClassLevel` | niveau cible (successeur)        |
| `gender`            | `Gender`        | FEMALE / MALE / OTHER                  |
| `rate`              | `NUMERIC(6,4)?` | 4 décimales, NULL si count_from = 0    |
| `sampleSize`        | `Integer`       | volume du dénominateur                 |
| `isOutlier`         | `Boolean`       | True si rate > 2 ou rate < 0           |
| `computedAt`        | `DateTime`      | snapshot point-in-time                 |
| `createdById`       | `FK User?`      | utilisateur ayant déclenché le run     |

Index :
* `(scope, entityId, schoolYearFromId)` — dashboard équité.
* `(classLevelFrom, classLevelTo)` — tri par paire de niveaux.

Unique :
* `(scope, entityId, schoolYearFromId, classLevelFrom, gender)` —
  garantit l'upsert idempotent.

## Logique de calcul

* **Source des effectifs** : `Enrollment` filtré sur
  `source = CENSUS_DECLARED` (vérité officielle, jamais
  `COMPUTED_FROM_STUDENTS` qui sert au contrôle qualité).
* **Année cible** : pour chaque `schoolYearFromId`, on trouve la
  `SchoolYear` qui suit immédiatement (`startDate` > celle de
  year_from, la plus proche). Si aucune → skip silencieux.
* **Rate REGIONAL** : par région, par paire de niveaux, par genre.
* **Rate NATIONAL** : `Σ(count_to par région) / Σ(count_from par
  région)` — **somme pondérée**, pas moyenne simple. Une moyenne simple
  biaiserait les régions à faibles effectifs.

### Garde-fous

| Condition           | Comportement                                       |
| ------------------- | -------------------------------------------------- |
| `count_from = 0`    | `rate = NULL`, `isOutlier = False` (pas de data)   |
| `rate > 2.0`        | `rate` enregistré, `isOutlier = True`              |
| `rate < 0.0`        | impossible mais blindé : `isOutlier = True`        |

## Endpoints API

### `POST /api/projections/transitions/compute`

RBAC : NATIONAL_ADMIN / MINISTRY_ADMIN.

Body :
```json
{ "schoolYearFromIds": ["clxxx...year2024", "clxxx...year2023"] }
```

Réponse :
```json
{
  "computed": 144,
  "outliers": 3,
  "anomaliesCreated": 3,
  "skipped": ["clxxx...year_courante"],
  "computedAt": "2026-05-24T12:00:00+00:00"
}
```

### `GET /api/projections/transitions`

Query params optionnels : `scope`, `entityId`, `schoolYearFromId`,
`classLevelFrom`, `gender`.

Scope territorial appliqué automatiquement :
* NATIONAL_SCOPE_ROLES → tout.
* REGIONAL_SCOPE_ROLES → NATIONAL + REGIONAL de leur région.
* Autres rôles → NATIONAL uniquement.

### `GET /api/projections/transitions/outliers`

Query param optionnel : `schoolYearFromId`.

Retourne uniquement les rows `isOutlier = True`. Utile pour le cabinet
ministre : "Quelles régions/paires de niveaux ont un signal aberrant ?".

## Hook Module 9 — Anomalies

Tous les outliers (`rate > 2` ou `rate < 0.5`) génèrent une
`AnomalyDetection` de type `TRANSITION_RATE_OUTLIER`, severity
`MEDIUM`, `entityType="Region"`. Le seuil `< 0.5` (en plus de `> 2`) est
spécifique à l'anomalie : il capture les abandons massifs, signal
d'alerte gouvernementale.

## Exemple

Année 2024 (source) → 2025 (cible), région KIN-001 :

| Niveau | Filles 2024 | Filles 2025 | Rate F |
| ------ | ----------- | ----------- | ------ |
| CP1    | 100         |             |        |
| CP2    | 75          | 80          | 0.8000 |
| CE1    | 60          | 70          | 0.9333 |
| ...    | ...         | ...         | ...    |

Le rate CP1→CP2 sur 2024-2025 = `80 (CP2 en 2025) / 100 (CP1 en 2024)
= 0.8000`. 20% de la cohorte CP1 a abandonné, redoublé, ou changé de
région.

## Pourquoi pas un calcul live ?

* **Coût** : agréger Enrollment sur 2 années × N régions × M niveaux ×
  2 genres à chaque hit dashboard est trop cher.
* **Reproductibilité** : les sources Enrollment peuvent être amendées
  rétroactivement (correction recensement). Un snapshot point-in-time
  préserve la traçabilité des rapports IIPE.
* **Module 2B** : la projection cohorte multi-années lit directement
  les rates persistés ; pas de re-calcul à chaque projection.

---

# Module 2B — Projection effectifs horizon 5 ans

## Objectif métier

À partir des effectifs déclarés au recensement (année de base) et des
taux de transition Module 2A, projeter les effectifs des années t+1 à
t+5 (et jusqu'à t+10 max). Utilisé pour :

1. **Planifier les recrutements d'enseignants** par région à 5 ans.
2. **Anticiper les besoins en infrastructure** (classes manquantes).
3. **Comparer des scénarios** ("baseline" vs "+10 % filles scolarisées
   à horizon 2030").

## Algorithme cohortes IIPE-UNESCO

```
projection[region, levelN, gender, t+k] =
    enrollment[region, levelN-1, gender, t+k-1]
    × transition_rate[region, levelN-1 → levelN, gender]
```

Pour **MATERNELLE_1** (premier niveau, pas de niveau précédent) :

```
projection[region, MATERNELLE_1, gender, t+k] =
    enrollment[region, MATERNELLE_1, gender, t+k-1]
    × (1 + demographic_growth)
```

`demographic_growth` est porté par le scénario (par défaut 2.4 % —
taux INS Guinée 2024).

### Stratégie de fallback (rate manquant)

| Étape | Rate utilisé                                |
| ----- | ------------------------------------------- |
| 1     | REGIONAL (region, level_from, gender)       |
| 2     | NATIONAL (level_from, gender) — fallback    |
| 3     | Aucun → on garde le count précédent (signal data quality) |

Les effectifs projetés sont **toujours arrondis à l'entier**
(half-even) : on parle d'élèves, pas de moyennes.

## Modèle de données

### `ProjectionScenario`

| Colonne                   | Type             | Notes                              |
| ------------------------- | ---------------- | ---------------------------------- |
| `id`                      | `String(30) PK`  | cuid ou `'BASELINE'`               |
| `name`                    | `String(80) UQ`  | nom court                          |
| `description`             | `String(500)?`   |                                    |
| `demographicGrowthRate`   | `NUMERIC(5,4)`   | défaut 0.0240                      |
| `customTransitionRates`   | `JSONB?`         | surcharges optionnelles            |
| `createdById`             | `FK User?`       |                                    |
| `createdAt`               | `DateTime`       |                                    |

Le scénario `BASELINE` est **seedé par la migration 0027** — sert de
défaut pour ``RunProjectionRequest``.

### `ProjectedEnrollment`

| Colonne              | Type             | Notes                                  |
| -------------------- | ---------------- | -------------------------------------- |
| `id`                 | `String(30) PK`  | cuid                                   |
| `baseSchoolYearId`   | `FK SchoolYear`  | année source des effectifs initiaux    |
| `projectedYear`      | `Integer`        | année calendrier projetée (ex. 2028)   |
| `scope`              | `TransitionScope`| NATIONAL / REGIONAL                    |
| `entityId`           | `String(30)?`    | regionId si REGIONAL, NULL sinon       |
| `classLevel`         | `EnrollmentClassLevel` | niveau projeté                   |
| `gender`             | `Gender`         | FEMALE / MALE / OTHER                  |
| `projectedCount`     | `Integer`        | effectifs projetés (entier)            |
| `scenarioId`         | `FK ProjectionScenario` | défaut `'BASELINE'`             |
| `computedAt`         | `DateTime`       |                                        |
| `createdAt`          | `DateTime`       |                                        |

Index :
* `(baseSchoolYearId, projectedYear, scope, entityId)` — dashboard.
* `(scenarioId)` — comparaison entre scénarios.

Unique :
* `(baseSchoolYearId, projectedYear, scope, entityId, classLevel,
   gender, scenarioId)` — upsert idempotent.

## Endpoints API

### `POST /api/projections/run`

RBAC : NATIONAL_ADMIN / MINISTRY_ADMIN.

Body :
```json
{
  "baseSchoolYearId": "clxxx...year2024",
  "horizonYears": 5,
  "scenarioId": "BASELINE"
}
```

Réponse :
```json
{
  "scenarioId": "BASELINE",
  "projectedRows": 240,
  "regionsCovered": 8,
  "horizonYears": 5,
  "computedAt": "2026-05-24T12:00:00+00:00"
}
```

### `GET /api/projections`

Query params : `baseSchoolYearId`, `projectedYear`, `scope`,
`entityId`, `classLevel`, `gender`, `scenarioId`, `limit` (≤ 1000),
`offset`.

Scope territorial appliqué :
* NATIONAL_SCOPE_ROLES → tout.
* REGIONAL_SCOPE_ROLES → NATIONAL + REGIONAL de leur région.
* Autres rôles → NATIONAL uniquement.

### `POST /api/projections/scenarios`

RBAC : NATIONAL_ADMIN / MINISTRY_ADMIN.

Body :
```json
{
  "name": "OPTIMISTE_FILLES_2030",
  "description": "+10% rétention filles primaire",
  "demographicGrowthRate": 0.025,
  "customTransitionRates": { "CP1->CP2:FEMALE": 0.95 }
}
```

### `GET /api/projections/scenarios`

Liste tous les scénarios visibles. Aucun scope territorial — un
scénario est national par construction.

## Tâche Celery

`run_projection_task(base_school_year_id, horizon_years=5,
scenario_id="BASELINE")` — lancé manuellement, jamais en beat
(comme `compute_transitions_task`).


---

# Module 2C — Capacité vs demande projetée (planification infrastructure)

## Objectif métier

Comparer pour chaque école (et agrégat préfecture/région/national) :

* **Capacité** = `classroomsUsable × STUDENTS_PER_CLASSROOM_NORM`
  (norme MEN Guinée = 50 élèves par salle, paramétrable).
* **Demande projetée** (horizon 1..5 ans) = somme des effectifs projetés
  Module 2B, redistribuée à l'école au prorata de sa part dans la
  région (méthode IIPE simple).
* **Gap** = demand − capacity (entier ; négatif = marge).
* **Saturation** = demand / capacity × 100 (Decimal ; NULL si capacity = 0).

### Niveaux d'alerte

* `OK`       — saturation ≤ 80 %.
* `WARNING`  — 80 % < saturation ≤ 100 %.
* `CRITICAL` — saturation > 100 % (sur-capacité, salles requises) OU
  capacity = 0 avec demande > 0 (école sans capacité utilisable).

### Trois usages métier

1. **Investissement infrastructure** — `GET /critical-schools`
   alimente directement le Module 3C (où construire / réhabiliter).
2. **Pilotage cabinet** — KPI `PROJECTED_CRITICAL_SCHOOLS_COUNT`
   exposé dans `GET /api/cockpit/kpis/national`.
3. **Anomalies HIGH** — chaque école CRITICAL sur l'horizon t+1 est
   matérialisée en `AnomalyDetection(type=CAPACITY_CRITICAL_PROJECTED,
   severity=HIGH)` pour suivi via Module 9.

## Modèle de données

Table `CapacityDemandSnapshot` (migration 0028) :

| Colonne          | Type                | Notes                                  |
|------------------|---------------------|----------------------------------------|
| baseSchoolYearId | String(30) FK       | Année source de la projection 2B       |
| projectedYear    | Integer             | Année cible (ex. 2027)                 |
| scope            | CapacityScope ENUM  | SCHOOL / PREFECTURE / REGIONAL / NATIONAL |
| entityId         | String(30) NULL     | NULL pour NATIONAL                     |
| capacity         | Integer             | Somme des places                       |
| demand           | Integer             | Effectifs projetés (tous niveaux)      |
| gap              | Integer             | demand − capacity                      |
| saturationPct    | Numeric(6,2) NULL   | NULL si capacity = 0                   |
| severity         | CapacitySeverity    | OK / WARNING / CRITICAL                |
| scenarioId       | String(30) FK       | Défaut BASELINE                        |
| computedAt       | TIMESTAMP TZ        |                                        |

Unique `(baseSchoolYearId, projectedYear, scope, entityId, scenarioId)` —
upsert idempotent au recalcul.

## Endpoints

* `POST /api/projections/capacity-demand/compute` — NATIONAL/MINISTRY.
* `GET  /api/projections/capacity-demand` — filtres + scope RBAC.
* `GET  /api/projections/capacity-demand/critical-schools` — top N écoles
  CRITICAL pour Module 3C (tri par gap décroissant).

## Logique pure (`capacity.py`)

* `compute_school_capacity(classrooms_usable, norm=50) -> int`.
* `compute_saturation_pct(demand, capacity) -> Decimal | None`.
* `compute_severity(saturation_pct) -> CapacitySeverity`.
* `compute_gap(demand, capacity) -> int`.

