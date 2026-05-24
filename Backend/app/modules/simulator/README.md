# Module 3B — Simulateur what-if de réorganisation du réseau scolaire

## Vue d'ensemble

Ce module implémente la partie back-end de l'étape 3 du cycle IIPE :
**tester des hypothèses de réorganisation du réseau scolaire sans toucher
aux données réelles**. Trois opérations sont supportées :

- `CREATE_SCHOOL` — créer une école fictive (lat/lon/capacity).
- `CLOSE_SCHOOL` — fermer une école existante.
- `MERGE_SCHOOLS` — fusionner ≥ 2 écoles en une nouvelle école fictive.

## Garantie read-only

**La table `School` n'est jamais modifiée.** Les écoles fictives
(`CREATE_SCHOOL` et `MERGE_SCHOOLS`) restent dans `scenarioJson` (JSONB
de `SimulationScenario`). Le simulateur lit la photo officielle des
écoles APPROVED, applique les opérations en mémoire (`VirtualSchool`
dataclass), puis calcule les métriques d'impact.

## Endpoints

| Méthode | Chemin                                    | RBAC                         |
|---------|-------------------------------------------|------------------------------|
| POST    | /api/simulator/scenarios                  | NATIONAL/MINISTRY/REGIONAL   |
| POST    | /api/simulator/scenarios/{id}/compute     | NATIONAL/MINISTRY/REGIONAL   |
| GET     | /api/simulator/scenarios                  | Auth                         |
| GET     | /api/simulator/scenarios/{id}             | Auth + visibilité            |
| POST    | /api/simulator/scenarios/{id}/archive     | NATIONAL/MINISTRY/REGIONAL   |

## Métriques d'impact

L'`ImpactReport` regroupe quatre indicateurs :

- **coverage** : nb d'écoles avant/après, delta en %.
- **saturation** : saturation moyenne pondérée (% students/capacity) +
  nb d'écoles critiques (saturation > 100 %).
- **distance** : distance moyenne école-élève en km, estimée via le
  centroid lat/lon des écoles d'une sub-prefecture (proxy IIPE simple)
  pondérée par `studentsCount`.
- **redistributedStudents** : somme des élèves rattachés aux écoles
  fermées ou fusionnées (ces élèves doivent être redirigés vers les
  écoles restantes / créées).

## Workflow d'un scénario

1. `POST /scenarios` → statut `DRAFT`, opérations stockées.
2. `POST /scenarios/{id}/compute` → applique les opérations en mémoire,
   calcule `ImpactReport`, persiste dans `impactJson`, statut `COMPUTED`.
3. `GET /scenarios/{id}` → renvoie le scénario + son `impactJson`.
4. `POST /scenarios/{id}/archive` → masque le scénario des listes
   (statut `ARCHIVED`).

Le recalcul (`compute`) est idempotent : il écrase `impactJson` et
`computedAt`.

## Architecture interne

- `enums.py` : `ScenarioStatus` (DRAFT/COMPUTED/ARCHIVED),
  `OperationType` (3 types).
- `models.py` : `SimulationScenario` (SQLAlchemy, JSONB sur PG).
- `schemas.py` : Pydantic discriminated union pour les opérations.
- `simulator.py` : logique pure (aucun accès DB).
- `service.py` : I/O DB + RBAC.
- `router.py` : FastAPI router.

## Tests

Voir `tests/integration/test_simulator_module_3b.py`.

## À venir — Module 3B.1

L'UI (Final/) qui permettra au planificateur de saisir les opérations
via une carte interactive et d'afficher l'impact en temps réel.
