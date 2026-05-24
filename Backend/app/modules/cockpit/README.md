# Module 19 — Cockpit ministériel

Surface API agrégée pour le cabinet du Ministre de l'Éducation : KPI live,
top alertes, time series, briefing quotidien automatique, snapshots
historiques.

## Pourquoi

Le cabinet a besoin d'un poste de pilotage *temps réel* qui ne soit pas
encombré par les filtres territoriaux des autres modules. Toutes les
agrégations sont nationales (avec breakdown régional dans les snapshots).
Tous les endpoints sont verrouillés RBAC ≥ `MINISTRY_ADMIN` (les rôles
`NATIONAL_ADMIN` et `MINISTRY_ADMIN` y ont accès).

## Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/cockpit/kpis/national` | KPI agrégés (cache Redis 30 s) |
| `GET /api/cockpit/alerts/top?limit=10` | Top écoles + régions à problèmes |
| `GET /api/cockpit/timeseries/attendance?days=90` | Série jour-par-jour |
| `GET /api/cockpit/timeseries/anomalies?weeks=12` | Anomalies par semaine |
| `GET /api/cockpit/briefing/today` | Brief LLM ou template |
| `GET /api/cockpit/comparison/{kpi_key}` | Variation J/J-1 |

### KPI exposés (clés normalisées)

* `STUDENTS_TOTAL` — total d'élèves dans le pays.
* `ATTENDANCE_RATE` — taux de présence sur les 7 derniers jours (0..100).
* `BUDGET_CONSUMPTION` — sum(expenses)/sum(budgets) × 100.
* `CRITICAL_ANOMALIES_OPEN` — anomalies sévérité CRITICAL non revues.
* `ALERTS_OPEN` — anomalies en statut PENDING (toutes sévérités).

## Architecture

```
Router ──► CockpitService ──► SQLAlchemy (read)
                          └─► Redis (cache 30 s)
                          └─► Anthropic SDK (briefing LLM, optionnel)

Celery beat (00:30 UTC) ──► snapshot_daily_kpis_task ──► CockpitKpiSnapshot
```

* Cache : clé `cockpit:<méthode>[:args]`, TTL 30 s. Tous les endpoints
  lourds caches leur résultat — le frontend peut rafraîchir toutes les
  5 s sans saturer la DB.
* Briefing : si `ANTHROPIC_API_KEY` est défini, on appelle Claude Haiku
  (un seul tour, max 600 tokens, JSON forcé). Sinon mode template
  déterministe en français — utile en CI, en staging air-gapped, ou
  lorsque l'API Claude est en panne (fallback automatique sur exception).
* Snapshot : idempotent par date. Le worker delete d'abord les snapshots
  du jour avant d'insérer (replay sûr depuis Celery beat).

## Hook temps réel (Module 13)

`RealtimeService.publish_anomaly()` mirror automatiquement les anomalies
sévérité `CRITICAL` sur le canal Redis Pub/Sub `gestionee:events:cockpit:alert`.
Le cabinet peut s'y abonner via un client WebSocket pour recevoir les
alertes critiques en push (latence < 1 s).

## Snapshots historiques

Table : `CockpitKpiSnapshot(id, snapshotDate, kpiKey, value, metadata,
regionId, scope, createdAt)`. Index `(snapshotDate, kpiKey)` pour les
lectures temporelles, `(kpiKey, scope)` pour les filtres NATIONAL/REGIONAL.

Le snapshot est append-only au niveau du modèle mais le service garantit
l'idempotence pour une même date via un delete préalable (replay safe).

## Tests

`Backend/tests/integration/test_cockpit_module19.py` couvre :

1. KPI nationaux complets + format des items.
2. Cache Redis 30 s effectif (deuxième appel = `cached=True`).
3. Top alertes : 10 écoles classées descendant.
4. Time series jour-par-jour 90 jours.
5. Time series anomalies 12 semaines.
6. Briefing structuré (headline + bullets non vides).
7. Fallback template sans clé API.
8. Snapshot persiste 5 lignes (une par KPI).
9. Comparison J/J-1 calcule la variation %.
10. RBAC : 403 pour SCHOOL_DIRECTOR sur `/kpis/national`.
11. RBAC : 403 pour SCHOOL_DIRECTOR sur `/alerts/top`.
12. Briefing inclut le top 3 des alertes.
13. Idempotence : 2 snapshots même date = 5 lignes (pas 10).
14. Endpoint robuste sur dataset vide (renvoie 0, pas 500).

## TODO Module 19.1 (post-livraison)

* WebSocket dédié `/api/cockpit/stream` (filtré sur `cockpit:alert`).
* Snapshot REGIONAL (1 ligne par region × KPI) — actuellement seul
  NATIONAL est snapshot.
* Briefing multi-langues (fr/en/ar).
* Comparaison J/J-7 et J/J-30 (pour les courbes hebdo/mensuelles).
* Cache invalidation sélective sur événement (anomalie créée, budget
  approuvé) plutôt qu'attendre l'expiration 30 s.
