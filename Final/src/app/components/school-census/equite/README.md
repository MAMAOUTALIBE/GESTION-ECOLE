# Module 1D — Dashboard Équité (UI Angular)

Écran de pilotage de l'équité éducative (parité filles/garçons, écart urbain/rural,
points chauds régionaux) destiné au cabinet ministériel, à la DPGE, aux DRE et
aux inspecteurs.

## Composants standalone (Angular 21, signals)

| Fichier | Rôle |
| --- | --- |
| `equite-page.ts` | Page principale lazy-loaded (`/school-census/equite`). |
| `equite-kpi-card.ts` | Card KPI réutilisable (titre, valeur, badge sévérité, Δ N-1). |
| `equite-region-chart.ts` | Bar chart horizontal ApexCharts — GPI par région avec lignes seuils 0.85 et 0.97. |
| `equite-region-map.ts` | Carte Leaflet de la Guinée colorée selon le GPI régional. |
| `equite-zone-donut.ts` | Donut ApexCharts — effectifs par zone (urbain / rural / péri-urbain). |
| `equite-critical-schools-table.ts` | Table HTML accessible des écoles avec GPI critique. |

## Endpoints consommés

| Méthode service | Endpoint Backend | Module |
| --- | --- | --- |
| `getNationalGpi()` | `GET /api/enrollment/gpi?scope=NATIONAL` | 1B |
| `getRegionalGpi(regionId)` | `GET /api/enrollment/gpi?scope=REGIONAL&entityId=…` | 1B |
| `getCriticalSchools()` | `GET /api/enrollment/gpi/critical-schools` | 1B |
| `getAggregateByZone()` | `GET /api/enrollment/aggregate?byZoneType=true` | 1A + 1C |
| `getUrbanRuralGap()` | `GET /api/cockpit/kpis/urban-rural-gap` | 19 |
| `getEvolution()` | `GET /api/enrollment/gpi/evolution` | 1B |

Toutes les requêtes sont en lecture seule. Aucune donnée nominative
n'est demandée (uniquement des agrégats : `girlsCount`, `boysCount`, GPI).

## Maquette ASCII

```
┌─────────────────────────────────────────────────────────────────┐
│ Pilotage équité — Carte scolaire                  [Actualiser]  │
│ Dashboard Équité (GPI & zone)                                   │
├──────────────┬──────────────┬──────────────┬──────────────┐    │
│ GPI national │ Filles ✔     │ Garçons      │ Δ urbain/    │    │
│   0.9412     │   312 408    │   330 891    │   rural 0.18 │    │
│ [parité]     │              │              │ [à surveill.]│    │
├──────────────┴──────────────┴──────────────┼──────────────┤    │
│ Carte GPI régional (Leaflet)               │ Effectifs    │    │
│  ┌──────────────────────────────────────┐  │ par zone     │    │
│  │   carte Guinée colorée par GPI       │  │  ╭──────╮    │    │
│  │   légende UNESCO (0.85 / 0.97 / 1.03)│  │  │donut │    │    │
│  └──────────────────────────────────────┘  │  ╰──────╯    │    │
├────────────────────────────────────────────┴──────────────┤    │
│ GPI par région (bars + seuils 0.85 / 0.97)                │    │
├───────────────────────────────────────────────────────────┤    │
│ Écoles à GPI critique  (table accessible clavier)         │    │
│  ┌──────────┬────────┬──────────┬───────┬───────┬──────┐  │    │
│  │ Nom      │  GPI   │ Sévérité │ Filles│Garçons│Fiche │  │    │
│  └──────────┴────────┴──────────┴───────┴───────┴──────┘  │    │
└───────────────────────────────────────────────────────────┘
```

## Sécurité & rôles

Route protégée par `roleGuard` avec `EQUITE_DASHBOARD_ROLES =
[NATIONAL_ADMIN, MINISTRY_ADMIN, REGIONAL_ADMIN, INSPECTOR]`. Les rôles
plus locaux (préfecture, sous-préfecture, école) peuvent consulter le
détail d'une école via la table → fiche école standard.

## Accessibilité

- Table sémantique (`<thead>`, `scope="col"`, `aria-label`).
- Carte Leaflet avec `role="img"` + label descriptif.
- Boutons avec `aria-label` quand l'icône seule est affichée.
- Badges de sévérité doublés d'un libellé texte (pas que de la couleur).
- Région live (`aria-live="polite"`) pour annoncer la fin du chargement.

## Tests

- `enrollment-api.service.spec.ts` : 7 tests (un par endpoint + helper `toNumber`).
- `equite-kpi-card.spec.ts` : 4 tests (rendu, sévérité, delta, sans delta).
- `equite-page.spec.ts` : 2 tests (montage + signal loading).

Lancement :

```bash
cd Final
$HOME/.nvm/versions/node/v20.19.5/bin/node ./node_modules/.bin/ng test \
    --watch=false --browsers=ChromeHeadless
```
