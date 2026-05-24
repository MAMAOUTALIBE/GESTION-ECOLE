# Module 4 — Reports / Bulletins (génération PDF asynchrone)

## Vue d'ensemble

Le module `reports` produit les bulletins scolaires PDF officiels (un par
élève, par période académique). À l'échelle nationale (~3M élèves × 3
trimestres × ~3 retentatives moyennes), la génération synchrone via
WeasyPrint dans l'event-loop FastAPI saturerait l'API : un seul bulletin
chargé prend 200–800 ms et bloque entièrement le worker uvicorn.

Module 4 introduit une pipeline **asynchrone** :

1. Le client appelle `POST /api/reports/student/{id}/period/{id}/generate`.
2. Le service crée (ou réutilise) une ligne `ReportCard` en
   `pdfStatus = PENDING` et enqueue un task Celery
   (`generate_report_pdf_task`).
3. Le worker pioche le task, marque `PROCESSING`, génère le PDF via
   WeasyPrint, calcule SHA-256, upload vers
   `s3://gestionee-bulletins/bulletins/<schoolId>/<periodId>/<studentId>.pdf`,
   puis marque `DONE` avec les colonnes `pdfS3Key`, `pdfSha256`,
   `pdfGeneratedAt`.
4. Le client poll `GET /api/reports/{rc_id}/status` jusqu'à `DONE` puis
   redirige sur `GET /api/reports/{rc_id}/download` (302 → URL S3 presignée
   valide 1h).

## Endpoints

| Méthode | URL | Auth | Description |
|---|---|---|---|
| `POST` | `/api/reports/student/{student_id}/period/{period_id}/generate` | `TEACHER\|DIRECTEUR\|...` | Demande la génération (idempotent) |
| `GET`  | `/api/reports/{report_card_id}/status` | bearer | Poll de l'état |
| `GET`  | `/api/reports/{report_card_id}/download` | bearer | 302 vers l'URL S3 presignée |
| `GET`  | `/api/reports/bulletins/verify/{code}` | **public** | Vérification d'authenticité (QR) |
| `GET`  | `/api/reports/bulletins/{rc_id}/pdf` | bearer | Legacy: render sync inline |
| `POST` | `/api/reports/bulletins/generate-batch` | `DIRECTEUR+` | Legacy: batch Celery via `render_bulletins_batch` |

## États du bulletin (`ReportCard.pdfStatus`)

```
   ┌──────────┐    enqueue     ┌────────────┐    render+upload    ┌──────┐
   │ PENDING  │ ──────────────▶│ PROCESSING │ ──────────────────▶ │ DONE │
   └──────────┘                └────────────┘                     └──────┘
       ▲                              │
       │                              │ exception après retries
       │                              ▼
       │                       ┌──────────┐
       └─── re-request ─────── │ FAILED   │
                               └──────────┘
```

- **PENDING** : la ligne existe, le task est queue, le worker n'a pas démarré.
- **PROCESSING** : le worker est en train de rendre. Empêche les re-enqueues.
- **DONE** : `pdfS3Key`, `pdfSha256`, `pdfGeneratedAt` sont remplis. Le
  fichier est dans S3. Tout re-call POST renvoie directement l'URL cachée.
- **FAILED** : `pdfErrorMessage` détaille la cause. Un POST re-déclenche un
  nouveau task (le worker repassera en PENDING → PROCESSING).

## Schéma DB (migration 0011)

Colonnes ajoutées à `ReportCard` :

| Colonne | Type | Description |
|---|---|---|
| `pdfStatus` | enum `ReportCardPdfStatus` | Défaut `PENDING`. Indexé partiel sur `IN (PENDING, PROCESSING, FAILED)` |
| `pdfS3Key` | `VARCHAR(512)` | Clé S3 canonique (cf. `storage.bulletin_key`) |
| `pdfSha256` | `VARCHAR(64)` | Hex SHA-256 du PDF (vérification UI) |
| `pdfGeneratedAt` | `TIMESTAMPTZ` | Marqueur de fin de rendu |
| `pdfErrorMessage` | `TEXT` | Renseigné en `FAILED` |
| `pdfTaskId` | `VARCHAR(64)` | Celery task id (UUID) |

L'index partiel sur `pdfStatus` permet à un job housekeeping de scanner
rapidement les bulletins coincés (`PENDING > 5min` → re-queue) sans payer
le coût d'un index sur la valeur dominante `DONE`.

## Storage (`app/modules/reports/storage.py`)

Wrapper minimal au-dessus de `boto3` (déjà dépendance) :

- `upload_pdf(key, bytes, metadata) -> "s3://bucket/key"` (async)
- `get_presigned_url(key, expires=3600) -> str` (async)
- `head_object(key) -> dict | None` (async)
- `bulletin_key(school_id, period_id, student_id) -> str` (sync helper)

Le client `boto3` est **cached** via `lru_cache` pour éviter le coût de
re-création par requête. Les opérations s'exécutent dans un thread pool
(`asyncio.to_thread`) pour ne pas bloquer l'event-loop FastAPI.

Idempotence du bucket : `ensure_bucket_sync()` est appelé à chaque upload
(coût ≈ 1 HEAD HTTP), créant le bucket s'il n'existe pas. Pratique en dev /
en CI où on n'a pas de seed initial.

## Concurrence

Le service utilise `SELECT ... FOR UPDATE` dans
`_find_or_create_report_card` : deux requêtes HTTP "simultanées" pour la
même paire `(student_id, period_id)` ne peuvent pas créer deux
`ReportCard` (cf. test
`test_generate_handles_concurrent_requests_same_student_period`).

Si un task est déjà `PROCESSING`, un nouveau POST retourne le `taskId` du
task en cours plutôt que d'enqueue un doublon.

## Configuration

```bash
# .env
S3_ENDPOINT_URL=http://localhost:9000      # MinIO en dev ; vide en prod AWS
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET_REPORTS=gestionee-bulletins
S3_REGION=us-east-1
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
```

En tests, on pose `CELERY_TASK_ALWAYS_EAGER=1` *avant* l'import de
`app.core.celery_app` (la conftest s'en charge). Le service détecte cette
configuration et exécute le rendu **inline** dans la session async de la
requête plutôt que via une session sync séparée — ainsi les mutations
sont visibles dans la transaction de test (qui rollback à la fin).

## Métriques Prometheus

```
gestionee_reports_pdf_requested_total           # toute demande HTTP
gestionee_reports_pdf_completed_total{status}   # status ∈ done|failed|cache_hit
gestionee_reports_pdf_duration_seconds          # histogram (0.1..30s)
```

Alerte recommandée :
- `rate(reports_pdf_completed_total{status="failed"}[5m]) > 0.05` → P2
- `histogram_quantile(0.95, reports_pdf_duration_seconds) > 10` → P3

## Limites connues (Module 4.1 / backlog)

- **Pas de TTL S3 / lifecycle policy** : on accumule les bulletins
  indéfiniment. À ajouter via une migration côté infra (S3 lifecycle).
- **Pas de "regenerate" forcé côté API** : si un bulletin est `DONE` mais
  qu'on veut le re-générer (par exemple notes corrigées), il faut
  manuellement repasser `pdfStatus = PENDING` en DB. Un endpoint
  `POST /api/reports/{id}/regenerate` ferait l'affaire.
- **Idempotence sur changement de données** : on ne re-rend pas si DONE,
  même si une note a changé entre temps. Une invalidation explicite par
  webhook depuis `academics` serait robuste.
- **Le worker légacy `render_bulletin` ne met pas à jour les colonnes
  `pdf*`** : seul `generate_report_pdf_task` le fait. À convertir si la
  voie batch est conservée à long terme.
