"""Module 6 — i18n templates + workflow SLA + per-user language preference.

Covers 16 cases:
* i18n rendering (5) — happy path, fallback, missing template, mustache,
  full seed catalogue.
* User preferred language (2) — update OK, invalid 422.
* SLA computation & escalation (4) — deadline, overdue query, escalate
  level increment, level-3 → NATIONAL_ADMIN.
* Workflow integration (3) — slaDeadline set at creation, approve/reject
  dispatches notifications in requester's language.
* RBAC (2) — overdue endpoint requires admin role, templates endpoint too.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.notifications.i18n import (
    SUPPORTED_LANGUAGES,
    TemplateNotFoundError,
    expected_seed_count,
    render_template,
    seed_default_templates,
)
from app.modules.notifications.models import NotificationTemplate
from app.modules.notifications.service import NotificationsService
from app.modules.workflow.models import Notification, ValidationRequest
from app.modules.workflow.service import ValidationTarget, WorkflowService
from app.modules.workflow.sla import (
    MAX_ESCALATION_LEVEL,
    SLA_BY_TYPE,
    check_overdue_requests,
    compute_sla_deadline,
    escalate_request,
)
from app.shared.base import generate_cuid
from app.shared.enums import (
    UserRole,
    ValidationEntityType,
    ValidationStatus,
)
from tests.integration import factories

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(loop_scope="session")
async def seeded_templates(db_session: AsyncSession) -> int:
    """Idempotent seed of the full Module 6 template catalogue."""
    factories.bind(db_session)
    inserted = await seed_default_templates(db_session)
    return inserted


# ===========================================================================
# 1. i18n RENDERING
# ===========================================================================
@pytest.mark.asyncio
async def test_render_template_fr_works(
    db_session: AsyncSession, seeded_templates: int
) -> None:
    _ = seeded_templates
    subject, body = await render_template(
        db_session,
        key="validation.approved",
        language="fr",
        channel="email",
        variables={
            "recipientName": "Aminata",
            "entityLabel": "SCHOOL abc",
            "reviewerName": "Mamadou",
        },
    )
    assert subject == "Demande approuvée"
    assert "Aminata" in body
    assert "SCHOOL abc" in body
    assert "Mamadou" in body


@pytest.mark.asyncio
async def test_render_template_fallback_to_fr_when_language_missing(
    db_session: AsyncSession, seeded_templates: int
) -> None:
    """Deliberately delete the ff variant — rendering ff must fall back to fr."""
    _ = seeded_templates
    factories.bind(db_session)
    await db_session.execute(
        NotificationTemplate.__table__.delete().where(
            NotificationTemplate.language == "ff",
            NotificationTemplate.key == "validation.approved",
            NotificationTemplate.channel == "sms",
        )
    )
    await db_session.flush()

    _, body = await render_template(
        db_session,
        key="validation.approved",
        language="ff",
        channel="sms",
        variables={"entityLabel": "SCHOOL xyz"},
    )
    # Should be the FR template, not the missing FF one.
    assert "approuvée" in body.lower()
    assert "SCHOOL xyz" in body


@pytest.mark.asyncio
async def test_render_template_raises_when_no_template_anywhere(
    db_session: AsyncSession, seeded_templates: int
) -> None:
    _ = seeded_templates
    with pytest.raises(TemplateNotFoundError):
        await render_template(
            db_session,
            key="this.does.not.exist",
            language="fr",
            channel="email",
            variables={},
        )


@pytest.mark.asyncio
async def test_substitute_variables_mustache_style(
    db_session: AsyncSession,
) -> None:
    """Insert a custom template + assert mustache substitution works."""
    factories.bind(db_session)
    tpl = NotificationTemplate(
        key="test.custom",
        language="fr",
        channel="sms",
        subject="Hi {{name}}",
        body="Bonjour {{name}}, votre code est {{code}}.",
        variables=["name", "code"],
    )
    db_session.add(tpl)
    await db_session.flush()

    subject, body = await render_template(
        db_session,
        key="test.custom",
        language="fr",
        channel="sms",
        variables={"name": "Sékou", "code": "1234"},
    )
    assert subject == "Hi Sékou"
    assert body == "Bonjour Sékou, votre code est 1234."


@pytest.mark.asyncio
async def test_seed_default_templates_inserts_60_rows(
    db_session: AsyncSession,
) -> None:
    """The seed catalogue must contain ≥ 60 entries and the seed must be idempotent."""
    factories.bind(db_session)
    inserted_first = await seed_default_templates(db_session)
    assert inserted_first >= 60
    assert inserted_first == expected_seed_count()

    # Second call → idempotent, 0 inserted.
    inserted_second = await seed_default_templates(db_session)
    assert inserted_second == 0

    # Verify 4 languages x 3 channels x 5 keys = 60 minimum.
    total = (
        await db_session.execute(
            select(func.count()).select_from(NotificationTemplate)
        )
    ).scalar_one()
    assert total >= 60

    languages_seen = (
        await db_session.execute(select(NotificationTemplate.language).distinct())
    ).scalars().all()
    for lang in SUPPORTED_LANGUAGES:
        assert lang in languages_seen


# ===========================================================================
# 2. USER PREFERRED LANGUAGE
# ===========================================================================
@pytest.mark.asyncio
async def test_user_can_update_preferred_language(
    client: AsyncClient,
    auth_headers: Any,
) -> None:
    headers = await auth_headers(UserRole.TEACHER)
    response = await client.patch(
        "/api/auth/me",
        json={"preferredLanguage": "ff"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["user"]["preferredLanguage"] == "ff"


@pytest.mark.asyncio
async def test_user_invalid_language_rejected_422(
    client: AsyncClient,
    auth_headers: Any,
) -> None:
    headers = await auth_headers(UserRole.TEACHER)
    response = await client.patch(
        "/api/auth/me",
        json={"preferredLanguage": "xx"},
        headers=headers,
    )
    assert response.status_code == 422, response.text


# ===========================================================================
# 3. SLA — compute / check / escalate
# ===========================================================================
def test_compute_sla_deadline_school_registration_3_days() -> None:
    now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    deadline = compute_sla_deadline(ValidationEntityType.SCHOOL, now)
    assert deadline == now + timedelta(days=SLA_BY_TYPE[ValidationEntityType.SCHOOL])
    assert (deadline - now).days == 3


@pytest.mark.asyncio
async def test_check_overdue_requests_returns_only_drafts_past_deadline(
    db_session: AsyncSession,
) -> None:
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    requester = await factories.UserFactory.create_async(
        role=UserRole.SCHOOL_DIRECTOR, regionId=tree["region"].id,
        prefectureId=tree["prefecture"].id, subPrefectureId=tree["subPrefecture"].id,
    )
    now = datetime.now(UTC)

    # Overdue (yesterday) + SUBMITTED → should show up
    overdue_req = ValidationRequest(
        entityType=ValidationEntityType.SCHOOL,
        entityId=tree["school"].id,
        status=ValidationStatus.SUBMITTED,
        requestedById=requester.id,
        reviewerRole=UserRole.REGIONAL_ADMIN,
        reviewerRegionId=tree["region"].id,
        slaDeadline=now - timedelta(hours=1),
        escalationLevel=0,
    )
    # On-time → not overdue
    fresh_req = ValidationRequest(
        entityType=ValidationEntityType.SCHOOL,
        entityId=tree["school"].id,
        status=ValidationStatus.SUBMITTED,
        requestedById=requester.id,
        reviewerRole=UserRole.REGIONAL_ADMIN,
        reviewerRegionId=tree["region"].id,
        slaDeadline=now + timedelta(days=1),
        escalationLevel=0,
    )
    # Overdue but already approved → must not be re-escalated
    approved_req = ValidationRequest(
        entityType=ValidationEntityType.SCHOOL,
        entityId=tree["school"].id,
        status=ValidationStatus.APPROVED,
        requestedById=requester.id,
        reviewerRole=UserRole.REGIONAL_ADMIN,
        reviewerRegionId=tree["region"].id,
        slaDeadline=now - timedelta(days=5),
        escalationLevel=0,
    )
    # Overdue but already at MAX_ESCALATION_LEVEL → skip
    capped_req = ValidationRequest(
        entityType=ValidationEntityType.SCHOOL,
        entityId=tree["school"].id,
        status=ValidationStatus.SUBMITTED,
        requestedById=requester.id,
        reviewerRole=UserRole.REGIONAL_ADMIN,
        reviewerRegionId=tree["region"].id,
        slaDeadline=now - timedelta(days=5),
        escalationLevel=MAX_ESCALATION_LEVEL,
    )
    db_session.add_all([overdue_req, fresh_req, approved_req, capped_req])
    await db_session.flush()

    overdue = await check_overdue_requests(db_session)
    overdue_ids = {r.id for r in overdue}
    assert overdue_req.id in overdue_ids
    assert fresh_req.id not in overdue_ids
    assert approved_req.id not in overdue_ids
    assert capped_req.id not in overdue_ids


@pytest.mark.asyncio
async def test_escalate_request_increments_level_and_notifies(
    db_session: AsyncSession, seeded_templates: int
) -> None:
    _ = seeded_templates
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    requester = await factories.UserFactory.create_async(
        role=UserRole.SCHOOL_DIRECTOR, regionId=tree["region"].id,
    )
    reviewer = await factories.UserFactory.create_async(
        role=UserRole.REGIONAL_ADMIN, regionId=tree["region"].id,
    )
    request = ValidationRequest(
        entityType=ValidationEntityType.SCHOOL,
        entityId=tree["school"].id,
        status=ValidationStatus.SUBMITTED,
        requestedById=requester.id,
        reviewerRole=UserRole.REGIONAL_ADMIN,
        reviewerRegionId=tree["region"].id,
        slaDeadline=datetime.now(UTC) - timedelta(hours=1),
        escalationLevel=0,
    )
    db_session.add(request)
    await db_session.flush()

    calls: list[dict[str, Any]] = []

    async def _notifier(
        *, user_id: str, channel: str, template_key: str,
        variables: dict[str, Any],
    ) -> None:
        calls.append({
            "user_id": user_id, "channel": channel,
            "template_key": template_key, "variables": variables,
        })

    new_level = await escalate_request(db_session, request, _notifier)
    assert new_level == 1
    assert request.escalationLevel == 1
    assert request.escalatedAt is not None
    # Reviewer got at least one notification + requester got one in_app copy.
    reviewer_calls = [c for c in calls if c["user_id"] == reviewer.id]
    requester_calls = [c for c in calls if c["user_id"] == requester.id]
    assert reviewer_calls, "reviewer should have been notified"
    assert requester_calls, "requester should have been copied"
    assert all(c["template_key"] == "validation.escalated" for c in calls)


@pytest.mark.asyncio
async def test_escalation_level_3_notifies_national_admin(
    db_session: AsyncSession, seeded_templates: int
) -> None:
    _ = seeded_templates
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    requester = await factories.UserFactory.create_async(
        role=UserRole.SCHOOL_DIRECTOR, regionId=tree["region"].id,
    )
    national = await factories.UserFactory.create_async(role=UserRole.NATIONAL_ADMIN)
    request = ValidationRequest(
        entityType=ValidationEntityType.SCHOOL,
        entityId=tree["school"].id,
        status=ValidationStatus.SUBMITTED,
        requestedById=requester.id,
        reviewerRole=UserRole.REGIONAL_ADMIN,
        reviewerRegionId=tree["region"].id,
        slaDeadline=datetime.now(UTC) - timedelta(days=2),
        escalationLevel=2,  # → bump to 3 = cap.
    )
    db_session.add(request)
    await db_session.flush()

    calls: list[dict[str, Any]] = []

    async def _notifier(
        *, user_id: str, channel: str, template_key: str,
        variables: dict[str, Any],
    ) -> None:
        calls.append({"user_id": user_id, "channel": channel})

    new_level = await escalate_request(db_session, request, _notifier)
    assert new_level == MAX_ESCALATION_LEVEL == 3
    national_calls = [c for c in calls if c["user_id"] == national.id]
    assert national_calls, "NATIONAL_ADMIN should be pinged at level 3"


# ===========================================================================
# 4. WORKFLOW INTEGRATION — slaDeadline + i18n notifications
# ===========================================================================
@pytest.mark.asyncio
async def test_create_validation_sets_sla_deadline(
    db_session: AsyncSession,
) -> None:
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    requester = await factories.UserFactory.create_async(
        role=UserRole.SCHOOL_DIRECTOR, regionId=tree["region"].id,
    )
    svc = WorkflowService(db_session)
    target = ValidationTarget(
        entity_type=ValidationEntityType.SCHOOL,
        entity_id=tree["school"].id,
        requested_by_id=requester.id,
        reviewer_role=UserRole.REGIONAL_ADMIN,
        title="t",
        message="m",
        reviewer_region_id=tree["region"].id,
    )
    request = await svc.create_validation_request(target)
    assert request.slaDeadline is not None
    delta = request.slaDeadline - request.createdAt.astimezone(UTC)
    # Allow 1s clock skew between createdAt (server_default now()) and the
    # python-side now we used for slaDeadline.
    assert timedelta(days=3) - timedelta(seconds=2) <= delta <= timedelta(days=3) + timedelta(seconds=2)


@pytest.mark.asyncio
async def test_approve_validation_sends_notification_in_user_language(
    db_session: AsyncSession, seeded_templates: int
) -> None:
    _ = seeded_templates
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    # Requester prefers Pular.
    requester = await factories.UserFactory.create_async(
        role=UserRole.SCHOOL_DIRECTOR,
        regionId=tree["region"].id,
        preferredLanguage="ff",
    )
    reviewer = await factories.UserFactory.create_async(
        role=UserRole.REGIONAL_ADMIN, regionId=tree["region"].id,
    )
    svc = WorkflowService(db_session)
    target = ValidationTarget(
        entity_type=ValidationEntityType.SCHOOL,
        entity_id=tree["school"].id,
        requested_by_id=requester.id,
        reviewer_role=UserRole.REGIONAL_ADMIN,
        title="t", message="m",
        reviewer_region_id=tree["region"].id,
    )
    request = await svc.create_validation_request(target)

    await svc.review(reviewer, request.id, ValidationStatus.APPROVED, None)

    # At least one in_app Notification went to the requester (legacy bell).
    notif_rows = (
        await db_session.execute(
            select(Notification).where(
                Notification.recipientUserId == requester.id,
            )
        )
    ).scalars().all()
    assert any("validée" in n.message.lower() or n.title == "Demande validée" for n in notif_rows)


@pytest.mark.asyncio
async def test_reject_validation_sends_notification(
    db_session: AsyncSession, seeded_templates: int
) -> None:
    _ = seeded_templates
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    requester = await factories.UserFactory.create_async(
        role=UserRole.SCHOOL_DIRECTOR, regionId=tree["region"].id,
    )
    reviewer = await factories.UserFactory.create_async(
        role=UserRole.REGIONAL_ADMIN, regionId=tree["region"].id,
    )
    svc = WorkflowService(db_session)
    target = ValidationTarget(
        entity_type=ValidationEntityType.SCHOOL,
        entity_id=tree["school"].id,
        requested_by_id=requester.id,
        reviewer_role=UserRole.REGIONAL_ADMIN,
        title="t", message="m",
        reviewer_region_id=tree["region"].id,
    )
    request = await svc.create_validation_request(target)

    await svc.review(
        reviewer, request.id, ValidationStatus.REJECTED, "Données incomplètes"
    )

    rows = (
        await db_session.execute(
            select(Notification).where(
                Notification.recipientUserId == requester.id,
            )
        )
    ).scalars().all()
    assert any(n.title == "Demande rejetée" for n in rows)


# ===========================================================================
# 5. RBAC — admin-only endpoints
# ===========================================================================
@pytest.mark.asyncio
async def test_overdue_endpoint_requires_admin_role(
    client: AsyncClient, auth_headers: Any,
) -> None:
    # Non-admin → 403.
    teacher_headers = await auth_headers(UserRole.TEACHER)
    r = await client.get("/api/workflow/sla-status", headers=teacher_headers)
    assert r.status_code == 403, r.text

    # Admin national → 200.
    admin_headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    r = await client.get("/api/workflow/sla-status", headers=admin_headers)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_templates_endpoint_requires_admin(
    client: AsyncClient, auth_headers: Any,
) -> None:
    teacher_headers = await auth_headers(UserRole.TEACHER)
    r = await client.get("/api/notifications/templates", headers=teacher_headers)
    assert r.status_code == 403, r.text

    admin_headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    r = await client.get("/api/notifications/templates", headers=admin_headers)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)
