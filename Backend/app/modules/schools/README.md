# Module Schools

CRUD des établissements scolaires + classes (`ClassRoom`). Toutes les
écoles sont rattachées à une `Region` (obligatoire), une `Prefecture`
(optionnel), une `SubPrefecture` (optionnel). Le rattachement à la
sous-préfecture détermine la zone effective urbain/rural héritée
(Module 1C).

## Endpoints principaux

| Méthode | Route                                | RBAC                                         |
|---------|--------------------------------------|----------------------------------------------|
| GET     | `/api/schools`                       | authentifié (scope automatique)             |
| GET     | `/api/schools/{id}`                  | idem                                         |
| POST    | `/api/schools`                       | NATIONAL / MINISTRY / REGIONAL / PREFECTURE / SUB_PREFECTURE |
| PATCH   | `/api/schools/{id}`                  | idem                                         |
| DELETE  | `/api/schools/{id}`                  | idem                                         |
| PUT     | `/api/schools/{id}/zone-type`        | NATIONAL / MINISTRY / REGIONAL (Module 1C) |

## Module 1C — Override zone urbain / rural

### Quand l'utiliser
99% des écoles héritent la zone INS de leur sous-préfecture. L'override
sert pour les cas frontaliers :
- école dans un quartier urbain d'une sous-préfecture majoritairement rurale ;
- école rurale isolée dans une sous-préfecture urbanisée.

### Comportement
- `School.zoneType = NULL` → l'école hérite de `SubPrefecture.defaultZoneType`.
- `School.zoneType = URBAN | RURAL | PERI_URBAN` → override effectif.
- `PUT /api/schools/{id}/zone-type` body `{zoneType: ZoneType | null}` :
  passer `null` retire l'override (l'école revient à hériter).

### Permissions
- NATIONAL_ADMIN / MINISTRY_ADMIN : peuvent override n'importe quelle école.
- REGIONAL_ADMIN : peut override les écoles de sa région (vérifié via
  `_assert_can_access_school` classique).

### Audit
Chaque appel `PUT /zone-type` écrit dans `AuthAuditLog` :
- event `SET_SCHOOL_ZONE_TYPE_OVERRIDE`,
- `failureReason` (champ libre) porte `schoolId=... old=... new=...`,
- `userId` = auteur de l'écriture.

### Helper
`effective_zone_type(school, subPrefecture)` (cf.
`app.modules.territory.zone_type`) renvoie la zone réelle en un appel
synchrone si les objets sont déjà chargés.

### Effet de bord
Toute écriture (set ou clear) invalide le cache cockpit
`cockpit:urban_rural_gap:*` pour rafraîchir les KPI nationaux au prochain
hit.

## Lien avec les modules

- Module 1A (`enrollment`) : `aggregate(byZoneType=True)` calcule un
  breakdown effectifs par zone effective.
- Module 1C (`cockpit`) : `urbanRuralGap` agrège au niveau pays.
