# Notifications module

Five transport channels (SMS, WhatsApp, Email, Push, In-App) wrapped behind a
unified `NotificationsService`. Each parent-facing message is persisted as a
`ParentCommunication` row (DRAFT → SENT|FAILED), enqueued onto Celery, and
dispatched by `app.workers.notification_tasks`.

## Module 6 — i18n templates

`i18n.py` introduces a multilingual template engine keyed by
`(key, language, channel)`:

| Element        | Notes                                                       |
| -------------- | ----------------------------------------------------------- |
| Languages      | `fr` (canonical), `ff` (Pular), `sus` (Soussou), `man` (Maninka). Fallback always falls back to `fr`. |
| Mustache subset | Only `{{varName}}` is substituted. Missing variables become empty strings. |
| Storage        | `NotificationTemplate` table, unique `(key, language, channel)`. |
| Seed catalogue | `seed_default_templates()` — 60 rows (5 keys × 4 langs × 3 channels). Idempotent. |
| Admin API      | `GET /api/notifications/templates`, `POST /api/notifications/templates/seed`. |

### Sending via template

```python
service = NotificationsService(session)
ok, ref = await service.send_via_template(
    user_id="cln…",
    channel="sms",
    template_key="validation.approved",
    variables={"entityLabel": "SCHOOL cln…", "reviewerName": "Aminata B."},
)
```

`channel` is the lowercase template channel name. Recipient/language are
derived from the loaded `User` row (`user.email` for email channels,
`user.id` for `in_app`, etc.). Non-French translations not yet validated by
native speakers are prefixed with `[ff]/[sus]/[man]` for traceability —
backlog 6.1 tracks the review.

## Architecture diagram

```
                   ┌──────────────────────┐
HTTP POST /comms → │ NotificationsService │ → ParentCommunication (DRAFT)
                   └──────────┬───────────┘
                              │
                              ▼
                  Celery: dispatch_communication
                              │
                              ▼
               ┌─────────────────────────────┐
               │  Channel adapters (5)       │
               │  SMS / WhatsApp / Email /   │
               │  Push / InApp               │
               └─────────────────────────────┘
                              │
                              ▼
               ParentCommunication → SENT|FAILED
```

Module 6 layers `send_via_template` on top: the same dispatcher is reused but
the message body/subject come from the i18n catalogue, and the recipient is a
`User` (not a `Parent`).
