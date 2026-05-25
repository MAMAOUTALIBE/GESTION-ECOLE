# Module 2D UI — Dashboard transferts enseignants

Frontend Angular pour le pilotage des transferts d'enseignants. Visualise les
écoles sur-dotées / sous-dotées et valide les recommandations automatiques
générées par le backend (Module 2D backend).

## Architecture

```
transferts-page (orchestrateur, signals)
├── staffing-kpi-card  × 4   (critique, sur-doté, en attente, exécutées)
├── staffing-map              (Leaflet, marqueurs colorés par sévérité)
├── staffing-table            (top 20 écoles sévérité ↓ ratio ↓)
└── recommendations-table     (workflow review + modal)
```

Service `StaffingApiService` (carbon copy des schémas Pydantic Backend) :

| Endpoint                                          | Rôles                  | Méthode service              |
| -------------------------------------------------- | ---------------------- | ----------------------------- |
| `POST /projections/staffing/compute`               | NATIONAL/MINISTRY     | `computeStaffing(syId)`      |
| `POST /projections/recommendations/generate`       | NATIONAL/MINISTRY     | `generateRecommendations(syId)` |
| `GET  /projections/staffing`                       | NATIONAL/MINISTRY/REGIONAL | `listStaffing(filters)`  |
| `GET  /projections/recommendations`                | NATIONAL/MINISTRY/REGIONAL | `listRecommendations(...)` |
| `PATCH /projections/recommendations/{id}/review`   | REGIONAL_ADMIN+        | `reviewRecommendation(...)`  |

## RBAC

Route protégée pour : `NATIONAL_ADMIN`, `MINISTRY_ADMIN`, `REGIONAL_ADMIN`.

Les `SCHOOL_DIRECTOR`, `TEACHER`, `CENSUS_AGENT`, `PREFECTURE_ADMIN`,
`SUB_PREFECTURE_ADMIN` et `INSPECTOR` n'accèdent **pas** à cette page :
la décision de transfert relève au minimum du niveau régional.

Côté UI :
- Les boutons "Calculer staffing" / "Générer recommandations" ne sont visibles
  que pour NATIONAL/MINISTRY.
- Les actions du workflow (REVIEWED / ACCEPTED / REJECTED / EXECUTED) ne sont
  visibles que pour les rôles `canReview` (NATIONAL/MINISTRY/REGIONAL).

## Workflow des recommandations

```
PENDING ──▶ REVIEWED ──▶ ACCEPTED ──▶ EXECUTED
                   │
                   └─▶ REJECTED
```

Une modale demande une confirmation explicite et propose une note de revue
(`reviewNote`, ≤ 500 caractères) qui sera tracée dans l'audit côté backend.

## State management

100 % via `signal()` / `computed()` — pas de NgRx. Les erreurs réseau sont
gérées par `catchError` au cas par cas : si l'endpoint staffing tombe, la
page continue de fonctionner partiellement (les KPIs comptent à 0).

## Réutilisations

- `GuineaMapService` pour la config Leaflet (centre, bornes, tuiles).
- `AcademicsApiService.listSchoolYears()` pour la liste des années.
- `CensusApiService.metadata()` pour la liste des écoles (nom + region).
- `AuthService.hasAnyRole()` pour les flags `canTriggerJobs` / `canReview`.
