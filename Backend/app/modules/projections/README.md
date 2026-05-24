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
