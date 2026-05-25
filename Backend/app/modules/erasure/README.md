# Module 5D — Droit à l'oubli (anonymisation post-sortie d'élève)

Cadre légal : **loi 037/AN/2016 (République de Guinée) sur la protection
des données à caractère personnel** + **RGPD Article 17** (droit à
l'effacement). Quand un élève quitte définitivement le système éducatif
(déménagement à l'étranger, décès, exclusion), le ministère a 2 ans
maximum pour permettre l'effacement des données nominatives à la
demande de la famille ou sur initiative du contrôle interne.

## Pourquoi anonymisation, pas suppression physique

La carte scolaire IIPE/UNESCO repose sur des **agrégats statistiques**
(Module 1A — `Enrollment.count` par école/année/genre/niveau) qui
servent à calculer :

* taux de scolarisation rétrospectifs (TBS, TNS),
* parité genre (GPI),
* taux de transition par cohortes (Module 2A),
* taux d'achèvement, redoublement.

Si on supprimait physiquement les `Student` rows, ces agrégats
seraient brisés rétroactivement (impossible de produire le rapport
"effectifs 2020-21 par genre" si les filles sorties après 2021 ont
disparu). On résout le conflit "droit à l'oubli vs continuité
statistique" par l'anonymisation en place : la fiche élève existe
toujours en DB, mais ses données nominatives sont remplacées par des
valeurs neutres.

## Workflow

```
POST /api/erasure/requests
    ├── validation Student existe
    ├── refus si demande active déjà en cours
    ├── création row status=GRACE_PERIOD, gracePeriodUntil = now + 30j
    ├── audit PiiAccessLog (EXPORT, metadata={action: REQUEST_ERASURE})
    └── 201 Created

[ 30 jours fenêtre de récupération ]
    │
    ├── POST /api/erasure/requests/{id}/cancel
    │       ├── status → CANCELLED
    │       └── audit (action: CANCEL_ERASURE)
    │
    └── Worker quotidien 04:00 UTC (Celery beat)
            └── erasure.execute_pending_erasures
                ├── scan { status=GRACE_PERIOD, gracePeriodUntil < now }
                ├── pour chaque demande :
                │       1. anonymize_student(student_id)
                │       2. status → EXECUTED, executedAt = now
                │       3. audit (action: EXECUTE_ERASURE, counts)
                └── retourne {executed, skipped}
```

## Tables affectées par `anonymize_student`

| Table                  | Action                                                  |
|------------------------|---------------------------------------------------------|
| `Student`              | firstName/lastName → "Anonyme" ; photoUrl/guardianName/guardianPhone → NULL ; `uniqueCode` PRÉSERVÉ |
| `StudentParent`        | DELETE (les liens disparaissent)                        |
| `Parent`               | DELETE **uniquement si orphelin** (plus aucun lien à un autre élève) |
| `QrCredential`         | DELETE (payload nominatif)                              |
| `ParentCommunication`  | subject/message → "[ANONYMISÉ]"                         |
| `Incident`             | description → "[ANONYMISÉ]" ; studentId → NULL          |
| `HealthVisit`          | description → "[ANONYMISÉ]" ; nurseName → NULL ; studentId → NULL |
| `Vaccination`          | DELETE (FK studentId NOT NULL)                          |
| `StudentAllergy`       | DELETE (FK studentId NOT NULL)                          |
| `StudentTransfer`      | reason → "[ANONYMISÉ]"                                  |
| `LibraryLoan`          | PRÉSERVÉ (pas de champ libre)                           |
| `AttendanceRecord`     | **PRÉSERVÉ** (agrégats Module 1A)                       |
| `Grade`                | **PRÉSERVÉ** (agrégats Module 1A)                       |
| `ReportCard`           | **PRÉSERVÉ** (agrégats Module 1A)                       |

## RBAC

| Action                         | Rôle minimum                       |
|--------------------------------|------------------------------------|
| Création de demande            | NATIONAL_ADMIN ou MINISTRY_ADMIN   |
| Listing / détail               | NATIONAL_ADMIN ou MINISTRY_ADMIN   |
| Annulation (grace period)      | NATIONAL_ADMIN ou MINISTRY_ADMIN   |
| Exécution effective (`POST /execute-pending`) | **NATIONAL_ADMIN seul** |

## Endpoints

* `POST /api/erasure/requests` — créer une demande
* `GET /api/erasure/requests?status=GRACE_PERIOD&limit=100&offset=0` — listing
* `GET /api/erasure/requests/{id}` — détail
* `POST /api/erasure/requests/{id}/cancel` — annulation
* `POST /api/erasure/execute-pending` — déclencher manuellement le batch

## Audit obligatoire

Chaque opération produit une ligne `PiiAccessLog` (entityType=STUDENT,
accessType=EXPORT). On réutilise la valeur EXPORT existante de l'enum
`PiiAccessType` (5C) comme proxy pour "extraction / suppression
contrôlée" — pas d'extension d'enum pour rester compatible.

Le `metadataJson` documente :

* `action` ∈ {`REQUEST_ERASURE`, `CANCEL_ERASURE`, `EXECUTE_ERASURE`}
* `erasureRequestId` (corrélation)
* `reason` / `cancellationReason` / `counts` selon l'action

## Réversibilité

Pendant les 30 jours de grace period, l'annulation restaure
intégralement la fiche élève (rien n'a été muté en base sauf la table
`ErasureRequest`). Après EXECUTED, **l'opération est irréversible**
— c'est par construction le but du droit à l'oubli. Une éventuelle
restauration via backup hors-ligne est hors scope de ce module.

## Tâche périodique

Worker Celery : `app.workers.erasure_tasks.execute_pending_erasures_task`,
nom `erasure.execute_pending_erasures`. Beat suggéré quotidien à 04:00
UTC. L'agent système se résout via `SYSTEM_EMAIL_HINT` puis fallback
sur le premier NATIONAL_ADMIN actif.

## Tests d'intégration

Voir `tests/integration/test_erasure_module_5d.py` (≥ 12 cas couvrant
RBAC, grace period, anonymisation effective, audit, préservation des
agrégats Module 1A).
