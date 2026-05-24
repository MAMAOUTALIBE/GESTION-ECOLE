# Module schoollife — Vie scolaire

Module 7 du projet GESTION-EE. Couvre **4 sous-domaines** opérationnels du
quotidien d'une école.

## Sous-domaines

### 1. Discipline (`/api/schoollife/discipline/*`)

Gestion des incidents (retards, bagarres, brimades, …) et de leur cycle de
vie. Les incidents peuvent être rattachés à un élève précis ou rester
"d'établissement".

- `POST /incidents` — créer un incident
- `GET /incidents` — lister (filtres `schoolId`, `severity`, `status`)
- `PATCH /incidents/{id}` — mettre à jour sanction / statut / sévérité
- `GET /incidents/by-student/{studentId}` — historique disciplinaire
- `GET /incidents/stats` — agrégats par sévérité / sanction / statut

**RBAC** : écriture réservée à `SCHOOL_DIRECTOR` + admins territoriaux ;
lecture ouverte aux `INSPECTOR` et au-dessus.

### 2. Santé (`/api/schoollife/health/*`)

Trois entités complémentaires :

- **`HealthVisit`** — passage infirmerie / visite médicale.
- **`Vaccination`** — vaccin administré (BCG, Pentavalent, …) avec lot et
  date.
- **`StudentAllergy`** — allergie déclarée (food / drug / environmental).
  La sévérité va de `MILD` à `ANAPHYLACTIC`.

- `POST /visits` + `GET /visits`
- `POST /vaccinations` + `GET /vaccinations?studentId=X&vaccine=BCG`
- `POST /allergies` + `GET /allergies/by-student/{id}`

**RBAC** : écriture pour `SCHOOL_DIRECTOR` + admins ; lecture pour
`INSPECTOR` + au-dessus.

### 3. Cantines (`/api/schoollife/meals/*`)

- **`MealService`** — un service de cantine (date + type breakfast/lunch/snack).
- **`MealMenu`** — JSON satellite à un `MealService` (items, allergens).
- **`MealAttendance`** — présence d'un élève à un service (bulk insert
  optimisé pour la saisie classe entière).

- `GET /menu/{date}?schoolId=X`
- `POST /menu` — crée le menu (auto-crée le `MealService` si absent)
- `POST /attendance` — bulk presence (idempotent, re-soumission OK)
- `GET /attendance/stats?mealServiceId=X` — compteurs present/absent/excused

**RBAC** :
- Menu : écriture `SCHOOL_DIRECTOR` + admins.
- Présence : `TEACHER` autorisé à déclarer la présence d'une classe.

### 4. Transport (`/api/schoollife/transport/*`)

- **`BusRoute`** — tournée de bus (nom, capacité, horaires, chauffeur).
- **`BusStop`** — point d'arrêt (lat/lon, pickup/dropoff, ordre).
- **`StudentBusSubscription`** — abonnement élève → route → arrêt.

- `POST /routes`, `GET /routes`
- `POST /stops`, `GET /stops?routeId=X`
- `POST /subscriptions`, `GET /subscriptions?routeId=X&studentId=Y`
- `GET /routes/{routeId}/students` — élèves abonnés actifs (avec brief)

**RBAC** : `SCHOOL_DIRECTOR` + `REGIONAL_ADMIN` + admins. La cohérence
école-élève est vérifiée à la souscription (refus si élève d'une autre
école que la route).

## Architecture

```
schoollife/
├── enums.py            # 6 enums Module 7 + ré-exports phase 13
├── models.py           # SQLAlchemy : Incident + 5 nouveaux modèles
├── schemas.py          # Pydantic : Create/Read/Update + Stats DTOs
├── service.py          # SchoolLifeService (legacy) + 4 services Module 7
├── router.py           # Router legacy phase 13 (non touché)
└── routers/
    ├── discipline.py
    ├── health.py
    ├── meals.py
    └── transport.py
```

## Tables PostgreSQL

| Table                     | Phase | Module |
|---------------------------|-------|--------|
| `Incident`                | 13    | 7 (status ajouté) |
| `HealthVisit`             | 13    | — |
| `BusRoute`                | 13    | — |
| `MealService`             | 13    | — |
| `TimetableSlot`           | 13    | — |
| `Vaccination`             | —     | **7** |
| `StudentAllergy`          | —     | **7** |
| `MealAttendance`          | —     | **7** |
| `MealMenu`                | —     | **7** |
| `BusStop`                 | —     | **7** |
| `StudentBusSubscription`  | —     | **7** |

## Migration

- `alembic/versions/0013_schoollife.py` — ajoute la colonne `Incident.status`
  et crée les 6 nouvelles tables + 6 enums Postgres natifs.
- Reverse propre (drop des tables + colonne + enums).

## Tests

`tests/integration/test_schoollife_module7.py` couvre 26 cas répartis sur
les 4 sous-domaines + un test cross-cut (NATIONAL_ADMIN voit toutes les
écoles).

## Backlog 7.1

Reporté au prochain sous-module (cf. `feedback_module_dod`) :

- Calendrier vaccinal PEV Guinée complet avec rappels automatiques.
- Optimisation tournées de bus (clustering géographique des points d'arrêt).
- Allocation nutritive cantines (calorimétrie + filtre allergies auto).
- Alertes médicales temps réel (notification parent si sévérité ≥ SEVERE).
- Photo / pièce jointe pour les incidents disciplinaires.
- Export CSV / PDF des fiches santé d'un élève.
- Géocodage inverse à la création d'un arrêt de bus (Nominatim).
- Calendrier d'emploi du temps revu (le `TimetableSlot` historique reste
  brut).
