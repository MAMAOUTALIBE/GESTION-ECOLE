# Module 3A — Cartographie SIG enrichie (réorganisation du réseau)

Cet écran agrège **6 couches** que les planificateurs IIPE / MEN
empilent à la demande pour décider où :
- créer une nouvelle école,
- fermer ou fusionner deux écoles voisines,
- réaffecter des enseignants,
- prioriser des investissements infrastructure.

## Architecture

```
reorganisation-page (orchestrateur, signals)
├── layer-toggle-panel  (sélection ON/OFF des 6 couches)
├── reorganisation-map  (Leaflet, empile les couches actives)
└── reorganisation-legend (légende dynamique)
```

Le service `CartographyApiService` parle aux endpoints :

| Endpoint | Source backend | Couche |
| --- | --- | --- |
| `/api/cartography/layers/gpi-critical-regions` | `GpiSnapshot` (Module 1B) | Régions GPI < 0.85 / warning filles |
| `/api/cartography/layers/capacity-critical-schools` | `CapacityDemandSnapshot` (2C) | Écoles saturées en année t+1 |
| `/api/cartography/layers/staffing-critical-schools` | `TeacherStaffingSnapshot` (2D) | Écoles sous-dotées en enseignants |
| `/api/cartography/layers/infrastructure-gaps` | `School.waterSource/...` | Écoles à infra incomplète |
| `/api/cartography/layers/zone-type` | `SubPrefecture.defaultZoneType` (1C) | Urbain / rural / péri-urbain |
| `/api/cartography/layers/white-zones-enriched` | Agrégat Haversine in-memory | Sous-préfectures > 5 km de toute école |

Tous les endpoints retournent un **GeoJSON `FeatureCollection`** (RFC 7946),
ce qui permet à `addGeoJsonLayer` (GuineaMapService) de les empiler
sans transformation supplémentaire.

## Pourquoi pas PostGIS pour ces couches ?

Module 5 utilise PostGIS pour les vector tiles MVT, Voronoï et grille
fine de coverage-gaps. **Module 3A reste portable** : les 6 couches
n'utilisent que des agrégats SQL classiques + Haversine in-memory pour
les zones blanches. Le but : un cabinet ministre tournant la solution sur
un Postgres vanille voit l'écran fonctionner immédiatement.

## RBAC

Route protégée pour : `NATIONAL_ADMIN`, `MINISTRY_ADMIN`,
`REGIONAL_ADMIN`, `INSPECTOR`. La décision de réorganisation se prend au
moins au niveau régional — les rôles préfecture / école sont écartés.

Le filtre territorial s'applique côté backend : `REGIONAL_ADMIN` ne voit
que les écoles / régions de sa `regionId`.

## Cache

Cache Redis 5 minutes par couche, clé hashée incluant le scope user
(évite qu'un `MINISTRY_ADMIN` voie le payload mis en cache par un
`INSPECTOR`). Le composant ne re-fetch pas tant que l'utilisateur ne
clique pas sur "Actualiser".
