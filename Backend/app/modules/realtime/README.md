# Module 13 — Realtime WebSocket

Pipe temps réel pour le cockpit ministériel. Architecture Redis Pub/Sub avec
filtrage côté serveur via la sélection des channels selon le rôle utilisateur.

## Endpoints

| Méthode | URL | Description |
|---------|-----|-------------|
| `WS` | `/api/realtime/connect?token=<JWT>` | Pipe d'évènements typés. Auth via JWT access en query param. |
| `WS` | `/api/realtime/notifications?userId=<id>` | Legacy phase 14 — notifications individuelles (utilisé par `app.modules.sms`). |

## Types d'évènements

| Type | Source | Payload | Routes |
|------|--------|---------|--------|
| `ATTENDANCE_SCAN` | `attendance.bulk_scan` | `{schoolId, count}` | school + region + global |
| `INCIDENT_CREATED` | `schoollife.DiscplineService.create_incident` | `{schoolId, severity, incidentId}` | school + region + global |
| `ANOMALY_DETECTED` | `anomalies.run_all_detectors` (CRITICAL only) | `{anomalyType, severity, anomalyId, schoolId?}` | school + region + global |
| `DROPOUT_PREDICTION_HIGH` | `predictions.predict_student` (HIGH only) | `{studentId, schoolId, probability}` | school + region + global |
| `BULLETIN_GENERATED` | `workers.pdf_tasks.generate_report_pdf_task` | `{studentId, schoolId, reportCardId}` | school + region + global |

## Channels Redis

* `gestionee:events:global` — annonces ministérielles, tout évènement non-scopé
* `gestionee:events:region:<regionId>` — agrégation régionale
* `gestionee:events:school:<schoolId>` — évènements de l'école

## Politique de scope (channels souscrits par rôle)

| Rôle | Channels souscrits |
|------|-------------------|
| `NATIONAL_ADMIN`, `MINISTRY_ADMIN`, `INSPECTOR` | `global` (les évènements régionaux sont aussi republiés sur `global`) |
| `REGIONAL_ADMIN`, `PREFECTURE_ADMIN`, `SUB_PREFECTURE_ADMIN` | `region:<id>` + `global` |
| `SCHOOL_DIRECTOR`, `TEACHER`, `CENSUS_AGENT` | `school:<id>` + `region:<id>` + `global` |

## Heartbeat

Le serveur envoie un message `{"type": "PING"}` toutes les 30 secondes pour
détecter les sockets zombies. Le client peut répondre ou ignorer ; ce qui
compte est la détection serveur-side de la rupture de connexion.

## Reconnexion

C'est au client de re-tenter une connexion avec backoff exponentiel après
un disconnect. Le serveur ne maintient PAS d'état de session : à la reconnexion,
le client recommence à partir du dernier event observé (pas de replay côté
serveur — on n'a pas de Kafka pour ça en MVP).

## Publication depuis les services métier

Pattern : appel one-liner via `RealtimeService` à la fin de l'opération métier,
encapsulé dans un `try / except Exception: pass` pour ne JAMAIS casser la
transaction métier si Redis hoquette.

```python
from app.modules.realtime.service import RealtimeService

try:
    await RealtimeService.publish_incident(
        school_id=incident.schoolId,
        region_id=region_id,
        severity=incident.severity.value,
        incident_id=incident.id,
    )
except Exception:
    pass  # best-effort
```

## Tests

Voir `Backend/tests/integration/test_realtime_module13.py` — 13 tests
couvrant serialisation, channels par rôle, WebSocket auth/scope, et les hooks
métier.

## Limitations connues (backlog 13.1)

* Pas de replay : un event perdu pendant une rupture de connexion est perdu.
  Pour l'audit, les événements importants (incident, anomalie, prédiction)
  restent disponibles via leurs endpoints REST respectifs.
* Pas de Worker Celery dans la boucle async : le hook
  `workers.pdf_tasks._publish_bulletin_generated_sync` utilise `redis` (sync)
  pour publier — duplique la logique de `Event.channels()`. À refacto en
  Module 13.1 si on extrait un helper sync partagé.
* Pas de back-pressure : si un client lent ne consomme pas, Redis bufferise
  côté serveur (PubSub n'a pas de persistence ni d'ack). Acceptable en MVP.
