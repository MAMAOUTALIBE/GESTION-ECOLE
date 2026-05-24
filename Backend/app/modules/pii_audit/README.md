# Module 5C — Audit des accès PII

> Carte scolaire nationale (Guinée) — traçabilité légale des consultations
> de données personnelles d'élèves mineurs et de leurs représentants.

## Pourquoi ce module ?

La **loi guinéenne 037/AN/2016** sur la protection des données à caractère
personnel — alignée sur les bonnes pratiques **RGPD** (Art. 5(1)(c) et (e),
Art. 30, Art. 32) — impose de pouvoir répondre à la question :

> « Qui a consulté la fiche de mon enfant, et quand ? »

La table `AuthAuditLog` (Module 1.1) trace uniquement les évènements
d'**authentification** (login, MFA, logout, refresh, etc.). Elle ne trace
PAS les accès en **lecture** sur les fiches PII (Personally Identifiable
Information).

Ce module ajoute une table dédiée — `PiiAccessLog` — append-only,
indexée pour les deux requêtes opérationnelles principales.

## Schéma

| Colonne        | Type              | Notes                                        |
|----------------|-------------------|----------------------------------------------|
| `id`           | VARCHAR(30) PK    | cuid                                         |
| `userId`       | VARCHAR(30) FK    | NULLABLE (SET NULL si user supprimé)         |
| `userRole`     | VARCHAR(40)       | snapshot du rôle au moment de l'accès        |
| `entityType`   | ENUM              | STUDENT / PARENT / HEALTH_VISIT / …          |
| `entityId`     | VARCHAR(30)       | `"*"` pour les LIST agrégés                  |
| `accessType`   | ENUM              | VIEW / LIST / EXPORT                         |
| `endpoint`     | VARCHAR(200)      | path HTTP                                    |
| `ip`           | VARCHAR(45)       | IPv4 ou IPv6 (compat XFF Module 1.1 C-4)     |
| `userAgent`    | VARCHAR(512)      | tronqué + caractères de contrôle retirés     |
| `requestId`    | VARCHAR(60)       | corrélation Loki (X-Request-Id)              |
| `metadataJson` | JSONB             | ex. `{"count": 137}` pour LIST agrégé        |
| `accessedAt`   | TIMESTAMPTZ       | défaut `NOW()`                               |

### Index

* `(userId, accessedAt DESC)` — « accès récents d'un agent »
* `(entityType, entityId, accessedAt DESC)` — « qui a vu cette fiche ? »
* `(accessedAt)` — purge mensuelle par fenêtre temporelle

## Quels endpoints sont instrumentés (MVP) ?

| Endpoint                                            | entityType         | accessType |
|-----------------------------------------------------|--------------------|------------|
| `GET /api/census/students/{student_id}`             | STUDENT            | VIEW       |
| `GET /api/census/students`                          | STUDENT            | LIST       |
| `GET /api/parent-portal/parent/{phone_hash}`        | PARENT             | VIEW       |
| `GET /api/schoollife/health/visits`                 | HEALTH_VISIT       | LIST       |
| `GET /api/schoollife/discipline/incidents`          | INCIDENT           | LIST       |
| `POST /api/diplomas/verify/{serial}`                | STUDENT            | VIEW       |

> **Hors MVP — voir backlog 5C.1** : `GET /api/census/teachers/{id}` (PII
> adulte, ne révèle pas d'enfant) ; les endpoints `EXPORT` (CSV /
> Excel) ; un filtrage géographique RBAC pour REGIONAL_ADMIN.

## RBAC

| Rôle                              | `GET /logs`               | `GET /history`        | `POST /purge` |
|-----------------------------------|---------------------------|-----------------------|---------------|
| NATIONAL_ADMIN / MINISTRY_ADMIN   | toutes les lignes         | autorisé              | autorisé      |
| REGIONAL_ADMIN ↘ SCHOOL_DIRECTOR  | **leurs** propres accès   | 403                   | 403           |
| Autres rôles authentifiés         | 403 (cf. `LIST_LOGS_ROLES`)| 403                  | 403           |

## Instrumentation

Le module expose un décorateur `@audit_pii_access` (FastAPI-friendly) :

```python
from app.modules.pii_audit.decorators import audit_pii_access
from app.modules.pii_audit.enums import PiiAccessType, PiiEntityType

@router.get("/students/{student_id}")
@audit_pii_access(
    entity_type=PiiEntityType.STUDENT,
    access_type=PiiAccessType.VIEW,
    get_entity_id=lambda k: k["student_id"],
)
async def get_student(
    student_id: str,
    user: CurrentUserDep,
    service: CensusSvc,
    request: Request,
) -> StudentRead:
    return await service.get_student(user, student_id)
```

Pour les listes paginées (LIST), on appelle directement
`PiiAuditService.log_bulk_list(...)` depuis le service métier — c'est
lui qui connait les ids retournés.

### Comportement best-effort

L'audit est **non bloquant** : un échec d'insertion (DB indispo, time-out
réseau, etc.) est capturé par `PiiAuditService.log_access` et journalisé
via `loguru` — la réponse HTTP utilisateur n'est **jamais** affectée.

En mode test, on attend la fin de l'audit (`PII_AUDIT_AWAIT=1` ou flag
explicite sur le décorateur) pour rester déterministe.

## Volumétrie & agrégation

Un appel à `GET /api/census/students` peut potentiellement remonter 500
élèves. Pour éviter d'écrire 500 lignes par requête (×N agents/jour, ×3M
élèves cibles), le service `log_bulk_list` applique :

* **≤ 50 entités** → une ligne par entité (traçabilité fine).
* **> 50 entités** → UNE ligne agrégée (`entityId="*"`,
  `metadataJson={"count": N}`).

Le seuil est dans `enums.BULK_LIST_AGGREGATION_THRESHOLD` (modifiable
plus tard si besoin).

## Rétention & purge

* Durée : **1095 jours (3 ans)** — `PII_LOG_RETENTION_DAYS`.
* Mécanisme : tâche Celery `pii_audit.purge_old_logs` (worker
  `app/workers/pii_audit_tasks.py`).
* Beat suggéré : le **15 du mois à 03:30 UTC** (jour neutre,
  heure creuse — pas de conflit avec le snapshot cockpit 00:30 UTC).

Ajouter dans `celery_app.conf.beat_schedule` :

```python
from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    "purge-old-pii-audit-logs": {
        "task": "pii_audit.purge_old_logs",
        "schedule": crontab(day_of_month="15", hour=3, minute=30),
    },
}
```

## Cadre légal

* **Loi guinéenne 037/AN/2016** — protection des données personnelles
  (chap. IV — droits de la personne concernée, chap. V — sécurité).
* **RGPD** (référence pratique pour partenaires UE) :
  * Art. 5(1)(c) — minimisation
  * Art. 5(1)(e) — limitation de conservation
  * Art. 30 — registre des activités de traitement
  * Art. 32 — mesures techniques (traçabilité)

Les fiches élèves contiennent des données d'enfants mineurs : la
protection est **renforcée** (cf. RGPD Cons. 38).

## Tests

Suite d'intégration : `tests/integration/test_pii_audit_module_5c.py`
(≥ 12 tests — log, RBAC, bulk aggregation, purge, intégration HTTP).
