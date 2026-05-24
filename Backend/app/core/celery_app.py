import os

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
        "app.workers.workflow_tasks",
        "app.workers.prediction_tasks",
        "app.workers.cockpit_tasks",
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

# Module 4 — pour les tests d'intégration on veut exécuter les tasks
# synchronement (pas besoin d'un worker Celery réel en CI). Activé via la
# variable d'environnement ``CELERY_TASK_ALWAYS_EAGER=1`` que la conftest
# pytest peut poser.
if os.environ.get("CELERY_TASK_ALWAYS_EAGER", "").lower() in {"1", "true", "yes"}:
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
