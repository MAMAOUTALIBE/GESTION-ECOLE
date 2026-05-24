# Module 12 — Open Data Portal

Portail public sans authentification : datasets anonymisés (agrégats par
école/région) sous licence ouverte (CC-BY-4.0 par défaut).

## Endpoints publics

Tous sous `/api/opendata`, **aucun header `Authorization` requis** :

| Endpoint | Description |
|---|---|
| `GET /datasets` | Catalogue complet (6 datasets MVP) |
| `GET /datasets/{key}` | Métadonnées d'un dataset (titre, schema, licence…) |
| `GET /datasets/{key}/data?format=json\|csv` | Données + audit anonyme |
| `GET /stats` | Compteurs agrégés des téléchargements (anonymes) |

## Datasets exposés

| Key | Description | Cadence |
|---|---|---|
| `schools_by_region` | Écoles / élèves / enseignants par région | daily |
| `attendance_rate_by_region` | Taux de présence moyen par région | weekly |
| `gender_distribution_by_region` | Répartition F/H par région + GPI | monthly |
| `dropout_risk_by_region` | Comptage HIGH/MEDIUM/LOW (Module 8) | weekly |
| `schools_density` | Écoles/km² par sous-préfecture (MVP forfaitaire 600 km²) | monthly |
| `diplomas_issued_by_year` | Diplômes ISSUED par année + type (Module 11) | yearly |

Chaque dataset est défini dans `datasets.py` via une `DatasetSpec` qui
embarque `fetch(session) -> list[dict]` + un `SCHEMA` JSON Schema.

## Anonymisation

- **Aucun record nominal** : tout dataset qui contiendrait un champ
  ressemblant à un PII (`id`, `firstName`, `phone`…) serait refusé par
  `anonymization.is_anonymous`. Le test
  `test_anonymization_no_pii_in_response` joue ce garde-fou pour les 6
  datasets.
- **Audit anonyme** : chaque téléchargement persiste un `OpendataDownload`
  avec `ipHash = sha256(salt || ip)`. Le salt est lu dans
  `OPENDATA_IP_HASH_SALT` (fallback dérivé du JWT secret pour ne jamais
  écrire d'IP en clair). Hash déterministe → on peut compter les visiteurs
  uniques sans jamais stocker l'IP réelle.

## Rate limit

60 requêtes / minute / IP, via `RateLimiter` (Redis, fixed-window). Au-delà :
HTTP 429 `RateLimitedError`. La clé Redis est `rl:opendata:ip:<ip>`.

## Pourquoi ce module ?

Transparence ministérielle + données réutilisables par les journalistes,
chercheurs et citoyens. Les URLs publiques doivent rester citables dans
des publications académiques pendant plusieurs années → registry statique
en code (versionné) plutôt qu'UI d'admin.

## Backlog 12.1 (post-MVP)

- Exporter en `xlsx` / `parquet` (format flag extensible déjà prévu).
- Calcul `schools_density` basé sur `ST_Area(SubPrefecture.geom)` quand
  PostGIS sera disponible.
- Fenêtres temporelles dans `/stats` (24h, 7j, 30j).
- Job Celery beat qui appelle `refresh_dataset_metadata` toutes les nuits.
- Cache Redis (TTL 1h) sur `/datasets/{key}/data` pour absorber les pics.
- Documentation OpenAPI enrichie (exemples par dataset).
