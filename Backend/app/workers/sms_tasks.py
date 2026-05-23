from app.core.celery_app import celery_app


@celery_app.task(name="sms.noop")
def noop() -> str:
    """Placeholder task. Real SMS/WhatsApp dispatch lands in Phase 6."""
    return "sms.noop ok"
