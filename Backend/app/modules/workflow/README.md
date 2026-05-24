# Workflow module

Hierarchical validation pipeline: a user submits a `ValidationRequest`
(`SUBMITTED`), the right reviewer (scoped by role + territorial level)
approves or rejects it, the linked entity's status follows along
(`School.status`, `Teacher.status`, etc.). Append-only notifications keep
every party informed.

## Module 6 — SLA escalation

`sla.py` adds business-grade SLAs per entity type:

| Entity type         | SLA (days) |
| ------------------- | ---------- |
| `SCHOOL`            | 3          |
| `TEACHER`           | 2          |
| `SUB_PREFECTURE`    | 5          |
| `PREFECTURE`        | 5          |
| (anything else)     | 3 (default) |

### Columns added to `ValidationRequest`

* `slaDeadline` — UTC timestamp computed at creation.
* `escalationLevel` — int, starts at 0, capped at 3.
* `escalatedAt` — timestamp of the last escalation.

### Lifecycle

1. `WorkflowService.create_validation_request` writes `slaDeadline` =
   `createdAt + SLA(entityType)`.
2. Celery beat invokes `app.workers.workflow_tasks.escalate_overdue_validations_task`
   daily at **06:00 UTC**. The task calls
   `check_overdue_requests` then `escalate_request` for each row.
3. `escalate_request` bumps `escalationLevel`, sets `escalatedAt`, and emits a
   `validation.escalated` notification to every matching reviewer plus a copy to
   the requester (in_app channel). At level 3 every active `NATIONAL_ADMIN` is
   pinged via in_app + email.

### Review notifications

When a reviewer approves or rejects a request, the requester receives a
cross-channel notification (`sms` + `email` + `in_app`) in their preferred
language (`User.preferredLanguage`). Templates live in `NotificationTemplate`
(see `notifications/README.md`). The legacy in-app `Notification` row is also
written for backwards compatibility with the existing frontend bell dropdown.

### Admin endpoint

`GET /api/workflow/sla-status` (NATIONAL_ADMIN / MINISTRY_ADMIN) lists every
overdue request — useful for the ministry dashboard.
