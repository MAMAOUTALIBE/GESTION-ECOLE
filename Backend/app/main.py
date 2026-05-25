import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from app.core.config import settings
from app.core.database import dispose_engine
from app.core.database import healthcheck as db_healthcheck
from app.core.exceptions import AppError, app_error_handler
from app.core.maintenance import MaintenanceModeMiddleware
from app.core.observability import RequestIdMiddleware
from app.core.redis import close_redis, get_redis
from app.core.redis import healthcheck as redis_healthcheck


def _configure_logging() -> None:
    logger.remove()
    if settings.is_production:
        logger.add(sys.stdout, level=settings.log_level, serialize=True)
    else:
        logger.add(sys.stdout, level=settings.log_level, backtrace=True, diagnose=True)


def _configure_sentry() -> None:
    if not settings.sentry_dsn:
        return
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        integrations=[FastApiIntegration()],
        traces_sample_rate=0.1 if settings.is_production else 1.0,
        send_default_pii=False,
    )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    _configure_logging()
    _configure_sentry()
    logger.info(
        "starting {name} env={env} prefix={prefix}",
        name=settings.app_name,
        env=settings.app_env,
        prefix=settings.api_prefix,
    )
    # Warm Redis connection
    get_redis()
    try:
        yield
    finally:
        logger.info("shutting down — closing Redis and DB engine")
        await close_redis()
        await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )
    # Phase 8 — propagate / mint X-Request-Id and bind it to loguru
    app.add_middleware(RequestIdMiddleware)
    # Module 15 — read-only platform mode. Added AFTER RequestId so the
    # 503 still carries a request id header.
    app.add_middleware(MaintenanceModeMiddleware)

    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]

    if settings.prometheus_enabled:
        from prometheus_fastapi_instrumentator import Instrumentator

        Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

    @app.get("/health", tags=["system"], summary="Liveness probe")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["system"], summary="Readiness probe (DB + Redis)")
    async def ready() -> JSONResponse:
        db = await db_healthcheck()
        rd = await redis_healthcheck()
        ok = db.get("database") == "ok" and rd.get("redis") == "ok"
        body: dict[str, Any] = {**db, **rd}
        return JSONResponse(
            status_code=status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE,
            content=body,
        )

    # Routers — alphabetical, registered in phase order below.
    from app.modules.academics.router import router as academics_router
    from app.modules.admin.router import router as admin_router
    from app.modules.analytics.router import router as analytics_router
    from app.modules.anomalies.router import router as anomalies_router
    from app.modules.assistant.router import router as assistant_router
    from app.modules.attendance.router import router as attendance_router
    from app.modules.auth.router import router as auth_router
    from app.modules.cartography.router import router as cartography_router
    from app.modules.census.router import router as census_router
    from app.modules.cockpit.router import router as cockpit_router
    from app.modules.diplomas.router import router as diplomas_router
    from app.modules.enrollment.router import router as enrollment_router
    from app.modules.finance.router import router as finance_router
    from app.modules.imports.router import router as imports_router
    from app.modules.inspections.router import router as inspections_router
    from app.modules.investment.router import router as investment_router
    from app.modules.library.router import router as library_router
    from app.modules.notifications.router import router as notifications_router
    from app.modules.opendata.router import router as opendata_router
    from app.modules.parent_portal.router import router as parent_portal_router
    from app.modules.pii_audit.router import router as pii_audit_router
    from app.modules.predictions.router import router as predictions_router
    from app.modules.projections.router import router as projections_router
    from app.modules.realtime.router import router as realtime_router
    from app.modules.reports.router import router as reports_router
    from app.modules.schoollife.router import router as schoollife_router
    from app.modules.schoollife.routers import (
        discipline_router as schoollife_discipline_router,
    )
    from app.modules.schoollife.routers import (
        health_router as schoollife_health_router,
    )
    from app.modules.schoollife.routers import (
        meals_router as schoollife_meals_router,
    )
    from app.modules.schoollife.routers import (
        transport_router as schoollife_transport_router,
    )
    from app.modules.schools.router import classes_router, schools_router
    from app.modules.simulator.router import router as simulator_router
    from app.modules.sms.router import router as sms_router
    from app.modules.territory.router import router as territory_router
    from app.modules.workflow.router import router as workflow_router

    app.include_router(auth_router, prefix=f"{settings.api_prefix}/auth")
    app.include_router(territory_router, prefix=f"{settings.api_prefix}/territory")
    app.include_router(schools_router, prefix=f"{settings.api_prefix}/schools")
    app.include_router(classes_router, prefix=f"{settings.api_prefix}/classes")
    app.include_router(census_router, prefix=f"{settings.api_prefix}/census")
    app.include_router(
        cartography_router, prefix=f"{settings.api_prefix}/cartography"
    )
    app.include_router(academics_router, prefix=f"{settings.api_prefix}/academics")
    app.include_router(reports_router, prefix=f"{settings.api_prefix}/reports")
    app.include_router(
        attendance_router, prefix=f"{settings.api_prefix}/attendance"
    )
    # NestJS mounts /validation-requests + /notifications at root
    # (no per-controller prefix), so workflow_router lives under /api directly.
    app.include_router(workflow_router, prefix=settings.api_prefix)
    # Phase 6 — multi-channel parent communications mounted under /api directly
    # (the routes are already prefixed with /communications inside the router).
    app.include_router(notifications_router, prefix=settings.api_prefix)
    # Phase 7 — library (matches NestJS /api/library/*) + imports (greenfield)
    app.include_router(library_router, prefix=f"{settings.api_prefix}/library")
    app.include_router(imports_router, prefix=f"{settings.api_prefix}/imports")
    # Phase 8 — analytics + audit-logs (greenfield)
    app.include_router(analytics_router, prefix=f"{settings.api_prefix}/analytics")
    # Phase 10 — inspections terrain (greenfield)
    app.include_router(
        inspections_router, prefix=f"{settings.api_prefix}/inspections"
    )
    # Phase 11 — finance & budget (greenfield)
    app.include_router(finance_router, prefix=f"{settings.api_prefix}/finance")
    # Phase 13 — vie scolaire (discipline / santé / transport / cantines / emploi du temps)
    app.include_router(schoollife_router, prefix=f"{settings.api_prefix}/schoollife")
    # Module 7 — 4 routers métier (discipline / health / meals / transport)
    app.include_router(
        schoollife_discipline_router,
        prefix=f"{settings.api_prefix}/schoollife/discipline",
    )
    app.include_router(
        schoollife_health_router,
        prefix=f"{settings.api_prefix}/schoollife/health",
    )
    app.include_router(
        schoollife_meals_router,
        prefix=f"{settings.api_prefix}/schoollife/meals",
    )
    app.include_router(
        schoollife_transport_router,
        prefix=f"{settings.api_prefix}/schoollife/transport",
    )
    # Phase 13bis — paramètres plateforme
    app.include_router(admin_router, prefix=f"{settings.api_prefix}/admin")
    # Phase 14 — Prédictions (détection précoce décrochage, forecasts)
    app.include_router(predictions_router, prefix=f"{settings.api_prefix}/predictions")
    # Phase 14 — Notifications temps réel WebSocket
    app.include_router(realtime_router, prefix=f"{settings.api_prefix}/realtime")
    # Phase 14 — SMS / USSD gateway
    app.include_router(sms_router, prefix=f"{settings.api_prefix}/sms")
    # Phase 14 — Diplômes signés (vérification PUBLIQUE)
    app.include_router(diplomas_router, prefix=f"{settings.api_prefix}/diplomas")
    # Phase 14 — Détection d'anomalies ML
    app.include_router(anomalies_router, prefix=f"{settings.api_prefix}/anomalies")
    # Phase 14 — Open Data portal (PUBLIC sans auth)
    app.include_router(opendata_router, prefix=f"{settings.api_prefix}/opendata")
    # Phase 14 — Assistant LLM (Claude API + scripted fallback)
    app.include_router(assistant_router, prefix=f"{settings.api_prefix}/assistant")
    # Module 18 — Portail parent (WhatsApp + USSD enrichi + page publique légère)
    app.include_router(
        parent_portal_router, prefix=f"{settings.api_prefix}/parent-portal"
    )
    # Module 19 — Cockpit ministériel (KPI live + briefing automatique)
    app.include_router(
        cockpit_router, prefix=f"{settings.api_prefix}/cockpit"
    )
    # Module 1A — Enrollment désagrégé (fondation Phase 1 carte scolaire IIPE)
    app.include_router(
        enrollment_router, prefix=f"{settings.api_prefix}/enrollment"
    )
    # Module 2A — Projections IIPE-UNESCO (taux de transition par cohortes)
    app.include_router(
        projections_router, prefix=f"{settings.api_prefix}/projections"
    )
    # Module 3B — Simulateur what-if de réorganisation du réseau scolaire
    app.include_router(
        simulator_router, prefix=f"{settings.api_prefix}/simulator"
    )
    # Module 3C — Score composite de priorité d'investissement par école
    app.include_router(
        investment_router, prefix=f"{settings.api_prefix}/investment"
    )
    # Module 5C — Audit des accès PII (loi 037/AN/2016 Guinée + RGPD)
    app.include_router(
        pii_audit_router, prefix=f"{settings.api_prefix}/pii-audit"
    )

    return app


app = create_app()
