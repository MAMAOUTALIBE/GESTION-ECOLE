# Module 3B UI — Simulateur what-if

Écran `/school-census/simulateur` qui permet aux décideurs (NATIONAL / MINISTRY
/ REGIONAL_ADMIN) de simuler une réorganisation du réseau scolaire avant de
la mettre en œuvre.

## Endpoints consommés
Tous via `SimulatorApiService` (`shared/simulator-api.service.ts`) :

| Méthode | URL | Usage |
| ------- | --- | ----- |
| `POST` | `/api/simulator/scenarios` | Création (DRAFT) |
| `POST` | `/api/simulator/scenarios/{id}/compute` | Calcul d'impact |
| `GET`  | `/api/simulator/scenarios` | Liste visible (RBAC) |
| `GET`  | `/api/simulator/scenarios/{id}` | Détail |
| `POST` | `/api/simulator/scenarios/{id}/archive` | Archivage |

Trois types d'opérations sont supportés (discriminated union sur `type`) :
`CREATE_SCHOOL`, `CLOSE_SCHOOL`, `MERGE_SCHOOLS`.

## Architecture

```
simulateur-page.ts          Orchestrateur, signals state
├── simulateur-map.ts       Leaflet, 4 modes (view/create/close/merge)
├── operations-panel.ts     Toggle modes, chips ops, modal sauvegarder
├── impact-report.ts        4 KPI cards (avant→après, delta coloré)
└── scenarios-table.ts      Table simple avec actions selon status
```

État 100 % signals (pas de NgRx). Les erreurs HTTP sont catchées et affichées
sous forme de toast — un échec d'un endpoint ne bloque pas le rendu.

## Modes du simulateur

| Mode    | Cursor      | Effet du clic                                        |
| ------- | ----------- | ---------------------------------------------------- |
| `view`  | grab        | Popup d'info uniquement                              |
| `create`| copy        | Clic carte → opération `CREATE_SCHOOL` (200 places)  |
| `close` | not-allowed | Clic marqueur → opération `CLOSE_SCHOOL`             |
| `merge` | cell        | Toggle sélection ; bouton "Fusionner" quand ≥ 2      |

Les écoles fermées ou sources d'une fusion sont grisées sur la carte. Les
nouvelles écoles (CREATE / MERGE target) apparaissent en vert néon.

## RBAC

- Route protégée par `roleGuard` (rôles : NATIONAL_ADMIN, MINISTRY_ADMIN,
  REGIONAL_ADMIN).
- Côté frontend, le computed `canEdit` désactive les boutons d'écriture.
- Le backend (`SIMULATOR_WRITE_HTTP_ROLES` dans `app/modules/simulator/router.py`)
  filtre déjà côté API — la défense en profondeur est volontaire.

## Tests

Tous les tests sont en vitest dans `src/app/components/school-census/...` :

- `shared/simulator-api.service.spec.ts` — 6 tests (créer / compute / list /
  get / archive / coercion Decimal).
- `simulateur/impact-report.spec.ts` — 2 tests (placeholder + amélioration).
- `simulateur/operations-panel.spec.ts` — 3 tests (chips + canCompute/Save +
  confirmSave).
- `simulateur/simulateur-page.spec.ts` — 2 tests (chargement initial +
  reset scenario).

Exécution :

```sh
cd Final && \
  $HOME/.nvm/versions/node/v20.19.5/bin/node ./node_modules/.bin/ng test --watch=false
```

## Décisions de design

- **Pas d'édition d'op existante** : la modification d'une op se fait en la
  supprimant et en la recréant. C'est cohérent avec le backend (un scénario
  est figé après création — pour le modifier on en crée un nouveau).
- **Compute toujours possible sur un scénario DRAFT** : pas de bouton
  "Update" — le calcul d'impact valide implicitement la liste d'opérations
  fournies au moment du `createScenario`.
- **Carte unique pour les 4 modes** : on évite d'avoir 4 cartes ou 4 panneaux
  séparés ; la seule chose qui change est le cursor + le handler de clic.
- **Locale `fr-FR`** pour les nombres et les dates : tous les utilisateurs
  ciblés (cadres MENA) travaillent en français.
