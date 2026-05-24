# Module 9 — Anomalies detection

Détection automatique des saisies aberrantes et fraudes potentielles dans
les données GESTION-EE (notes impossibles, présences 100% suspectes,
transferts d'élèves excessifs, etc.) avec workflow human-in-the-loop.

## Architecture

```
detectors.py       --> 6 règles métier (SQL léger, pas de joins lourds)
service.py         --> AnomalyService : run, list, get, review, stats
router.py          --> 5 endpoints REST (scope territorial automatique)
models.py          --> Table AnomalyDetection (append-only)
enums.py           --> AnomalyType, AnomalySeverity, AnomalyStatus
schemas.py         --> Pydantic DTOs
../../workers/anomaly_tasks.py
                   --> Celery : detect_anomalies_school / detect_anomalies_all
```

### Approche : rules + statistiques, pas de ML pur

Le ministère exige une **explicabilité totale** de chaque alerte (un
directeur d'école doit pouvoir contester la décision). Un IsolationForest
produit un score opaque ; on garde une structure ouverte pour brancher
un détecteur ML plus tard sans changer l'API ni la table.

Chaque anomalie persiste dans `evidence` (JSONB) les **champs exacts** qui
ont déclenché la règle (score brut, dates, IDs source) — affichés tels
quels au directeur dans le frontend.

## Détecteurs (6)

| Type                    | Sévérité | Seuil                                                                       |
|-------------------------|----------|------------------------------------------------------------------------------|
| `IMPOSSIBLE_GRADE`      | CRITICAL | `score < 0` ou `score > 20`                                                  |
| `SUSPICIOUS_ATTENDANCE` | MEDIUM   | 100% de présence sur ≥ 60 jours distincts                                    |
| `GRADE_JUMP`            | HIGH     | `\|delta moyenne\| > 8` points entre deux périodes successives                |
| `INVALID_BIRTHDATE`     | CRITICAL | `birthDate > createdAt` (inscription)                                        |
| `DUPLICATE_CODE`        | HIGH     | `uniqueCode` partagé entre ≥ 2 élèves                                        |
| `EXCESSIVE_TRANSFER`    | MEDIUM   | > 3 transferts d'un même élève en 365 jours                                  |

Tous les détecteurs prennent un `school_id` optionnel pour limiter le
scope, et tous appliquent un `LIMIT` défensif (`PER_DETECTOR_LIMIT = 1000`).

## Workflow human-in-the-loop

```
PENDING ─┬─→ CONFIRMED         (anomalie réelle, action métier requise)
         ├─→ DISMISSED          (réelle mais acceptable, ex. transfert documenté)
         └─→ FALSE_POSITIVE     (le détecteur s'est trompé)
```

Le **taux de confirmation** (`confirmed / (confirmed + dismissed + false_positive)`)
remonte dans `/api/anomalies/stats` — il sert à mesurer la précision des
détecteurs et à désactiver ceux qui produisent trop de bruit.

## RBAC

| Endpoint                          | Rôle minimum     |
|-----------------------------------|------------------|
| `POST /api/anomalies/run`         | REGIONAL_ADMIN   |
| `GET  /api/anomalies`             | SCHOOL_DIRECTOR  |
| `GET  /api/anomalies/{id}`        | SCHOOL_DIRECTOR  |
| `POST /api/anomalies/{id}/review` | SCHOOL_DIRECTOR  |
| `GET  /api/anomalies/stats`       | SCHOOL_DIRECTOR  |

Le **scope territorial** s'applique automatiquement sur les endpoints de
lecture : NATIONAL_ADMIN voit tout, REGIONAL_ADMIN ne voit que sa région
(`regionId`), SCHOOL_DIRECTOR ne voit que son école (`schoolId`).

## Tâches Celery

| Task                                 | Cadence prévue              |
|--------------------------------------|-----------------------------|
| `anomalies.detect_anomalies_school`  | À la demande (sous-tâche)   |
| `anomalies.detect_anomalies_all`     | Hebdomadaire (beat)         |

`detect_anomalies_all_task` itère sur toutes les écoles APPROVED et
dispatche une sous-tâche par école — si une école a une corruption
massive, les autres ne sont pas affectées.

## Indexes DB (table `AnomalyDetection`)

* `(status, severity)` — triage par sévérité au sein des PENDING.
* `(entityType, entityId)` — historique d'une entité.
* `(schoolId, detectedAt DESC)` — listing scope école, plus récent d'abord.

## Backlog (Module 9.1)

* Déduplication SQL côté service : exposer un mode "dernière occurrence par
  `(entityType, entityId, type)`" en plus du listing complet.
* IsolationForest scikit-learn comme 7e détecteur — features = mêmes que
  Module 8 (predictions). À garder optionnel via feature flag.
* Notification push au directeur quand une `CRITICAL` est détectée (intégration
  Module 6 — notifications).
* Endpoint `POST /api/anomalies/{id}/comment` pour les échanges asynchrones
  entre régional ↔ directeur sur une anomalie disputée.
