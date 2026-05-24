from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "gestionee",
    broker=str(settings.celery_broker_url),
    backend=str(settings.celery_result_backend),
    include=[
        "app.workers.pdf_tasks",
        "app.workers.sms_tasks",
        "app.workers.import_tasks",
        "app.workers.geocoding_tasks",
        "app.workers.notification_tasks",
        "app.workers.attendance_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,        # hard limit 30 min
    task_soft_time_limit=25 * 60,   # graceful at 25 min
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=200,
    broker_connection_retry_on_startup=True,
)
