# Module Territory

Référentiel géographique de la carte scolaire : Région → Préfecture →
Sous-préfecture. Sert de socle de scope RBAC à tous les autres modules
(census, schools, enrollment, cockpit…).

## Modèle

```
Region (1) ──< Prefecture (1) ──< SubPrefecture (1) ──< School
```

Toutes les entités portent un `status` ValidationStatus pour gérer un
workflow d'approbation (préfectures créées par REGIONAL_ADMIN passent en
SUBMITTED puis APPROVED).

## Endpoints

| Méthode | Route                                                         | RBAC                                                          |
|---------|---------------------------------------------------------------|---------------------------------------------------------------|
| GET     | `/api/territory/regions`                                      | authentifié (scope automatique)                              |
| GET     | `/api/territory/prefectures`                                  | idem                                                          |
| POST    | `/api/territory/prefectures`                                  | NATIONAL / REGIONAL                                          |
| GET     | `/api/territory/sub-prefectures`                              | authentifié                                                  |
| POST    | `/api/territory/sub-prefectures`                              | NATIONAL / REGIONAL / PREFECTURE                              |
| GET     | `/api/territory/sub-prefectures/zones`                        | authentifié (Module 1C)                                       |
| PUT     | `/api/territory/sub-prefectures/{id}/zone-type`               | NATIONAL / MINISTRY (Module 1C)                              |

## Module 1C — Segmentation urbain / rural

### Principe
- Source de vérité = `SubPrefecture.defaultZoneType` (NOT NULL DEFAULT 'RURAL').
- Référentiel déclaratif : c'est l'INS / le MEN qui pose la valeur. Pas de
  calcul GPS auto (pas de cadastre fiable en Guinée).
- Override par école possible via `School.zoneType` (cf. module `schools`).

### Trois valeurs ZoneType
- `URBAN` : commune urbaine, chef-lieu de préfecture, capitale régionale.
- `RURAL` : sous-préfecture rurale, valeur la plus fréquente (~~70%).
- `PERI_URBAN` : zone tampon (ex. extension de Conakry), valeur informative
  pour les KPI mais non comparée dans le détecteur d'écart Module 9.

### Audit
Toute écriture via `PUT /sub-prefectures/{id}/zone-type` est tracée dans
`AuthAuditLog` avec l'event `SET_SUBPREFECTURE_ZONE_TYPE` et l'ancienne /
nouvelle valeur.

### Effet de bord
Modifier une zone INS invalide le cache cockpit `cockpit:urban_rural_gap:*`
pour que le prochain hit du cabinet reflète la nouvelle valeur.

### Helper transverse
`app.modules.territory.zone_type.effective_zone_type(school, subPrefecture)`
retourne la zone effective d'une école (override OU défaut). Voir aussi
`get_effective_zone_for_school_id` pour un appel async direct.

## Lien avec les modules

- **Enrollment** (1A/1C) : `aggregate(byZoneType=True)` ventile les
  effectifs par zone effective.
- **Cockpit** (19/1C) : `urbanRuralGap` agrège les GPI par zone et calcule
  l'écart national.
- **Anomalies** (9/1C) : `detect_urban_rural_gpi_gap` signale les régions
  avec un delta GPI > 0.10.
