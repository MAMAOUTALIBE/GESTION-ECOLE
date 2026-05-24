"""Workflow SLA helpers: deadlines, overdue detection, escalation.

Each :class:`ValidationEntityType` carries an implicit business SLA — for
instance, a school registration must be reviewed within three working days,
a teacher assignment within two days, a student transfer within five days.

When a request misses its deadline the system raises its escalation level
and re-notifies the reviewer. Levels 1 and 2 add the requester in copy
(so they know their case is unstuck); level 3 escalates the request to
``NATIONAL_ADMIN`` because the regional / prefecture chain has visibly
failed.

The escalation cap is hard-coded to 3 — once we hit it, the Celery beat
task stops emitting reminders for the same row (the level-3 notification
already pinged the top of the hierarchy).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.auth.models import User
from app.modules.workflow.models import ValidationRequest
from app.shared.enums import (
    UserRole,
    ValidationEntityType,
    ValidationStatus,
)

# ---------------------------------------------------------------------------
# Per-type SLA (in days)
# ---------------------------------------------------------------------------
SLA_BY_TYPE: Final[dict[ValidationEntityType, int]] = {
    # School registration / re-affiliation — strategic, must be quick.
    ValidationEntityType.SCHOOL: 3,
    # Teacher assignment & re-assignment.
    ValidationEntityType.TEACHER: 2,
    # Sub-prefecture territorial change — paperwork heavy.
    ValidationEntityType.SUB_PREFECTURE: 5,
    # Prefecture territorial change — same.
    ValidationEntityType.PREFECTURE: 5,
}

# Fallback SLA in days when the entity type is missing from SLA_BY_TYPE.
DEFAULT_SLA_DAYS: Final[int] = 3

MAX_ESCALATION_LEVEL: Final[int] = 3


def compute_sla_deadline(
    entity_type: ValidationEntityType | str,
    created_at: datetime,
) -> datetime:
    """Return ``created_at + SLA(entity_type)``. Naive ``created_at`` is
    promoted to UTC to keep TZ math consistent.
    """
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    if isinstance(entity_type, str):
        try:
            entity_type = ValidationEntityType(entity_type)
        except ValueError:
            return created_at + timedelta(days=DEFAULT_SLA_DAYS)
    days = SLA_BY_TYPE.get(entity_type, DEFAULT_SLA_DAYS)
    return created_at + timedelta(days=days)


async def check_overdue_requests(
    session: AsyncSession, *, now: datetime | None = None
) -> list[ValidationRequest]:
    """Return every ValidationRequest that is past its SLA and still SUBMITTED.

    We deliberately scope to ``status == SUBMITTED`` (DRAFT is the legacy
    label kept in the enum but unused in current flows — see
    :class:`app.shared.enums.ValidationStatus`). Rows whose
    ``escalationLevel`` already reached :data:`MAX_ESCALATION_LEVEL` are
    excluded — we already pinged NATIONAL_ADMIN.
    """
    if now is None:
        now = datetime.now(UTC)
    stmt = (
        select(ValidationRequest)
        .where(
            ValidationRequest.status == ValidationStatus.SUBMITTED,
            ValidationRequest.slaDeadline.is_not(None),
            ValidationRequest.slaDeadline < now,
            ValidationRequest.escalationLevel < MAX_ESCALATION_LEVEL,
        )
        .order_by(ValidationRequest.slaDeadline.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


class _Notifier(Protocol):
    """Minimal callable contract used by :func:`escalate_request`."""

    async def __call__(
        self,
        *,
        user_id: str,
        channel: str,
        template_key: str,
        variables: dict[str, object],
    ) -> None: ...


NotifierCallable = Callable[..., Awaitable[None]]


async def escalate_request(
    session: AsyncSession,
    request: ValidationRequest,
    notifier: NotifierCallable,
    *,
    now: datetime | None = None,
) -> int:
    """Bump ``request.escalationLevel``, re-notify everyone that matters.

    Returns the new escalation level. Notifications go through ``notifier``
    which is expected to wrap :class:`NotificationsService.send_via_template`
    (we pass it as a callable so the SLA module stays decoupled from the
    notifications service singleton).

    At level 3 we also notify every active ``NATIONAL_ADMIN`` — the regional
    chain has clearly stalled.
    """
    if now is None:
        now = datetime.now(UTC)

    request.escalationLevel = (request.escalationLevel or 0) + 1
    request.escalatedAt = now
    await session.flush()

    new_level = request.escalationLevel
    variables: dict[str, object] = {
        "entityLabel": f"{request.entityType.value} {request.entityId}",
        "level": new_level,
        "recipientName": "validateur",
    }

    # 1. notify reviewers (scoped users with the right role + territory)
    reviewer_ids = await _matching_reviewer_ids(session, request)
    for reviewer_id in reviewer_ids:
        for channel in ("in_app", "sms", "email"):
            await notifier(
                user_id=reviewer_id,
                channel=channel,
                template_key="validation.escalated",
                variables=variables,
            )

    # 2. always copy the requester so they know the file is still being chased
    await notifier(
        user_id=request.requestedById,
        channel="in_app",
        template_key="validation.escalated",
        variables={**variables, "recipientName": "demandeur"},
    )

    # 3. at the cap, ping every NATIONAL_ADMIN
    if new_level >= MAX_ESCALATION_LEVEL:
        national_ids = (
            await session.execute(
                select(User.id).where(
                    User.role == UserRole.NATIONAL_ADMIN,
                    User.isActive.is_(True),
                )
            )
        ).scalars().all()
        for national_id in national_ids:
            for channel in ("in_app", "email"):
                await notifier(
                    user_id=national_id,
                    channel=channel,
                    template_key="validation.escalated",
                    variables={
                        **variables,
                        "recipientName": "admin national",
                    },
                )

    return new_level


async def _matching_reviewer_ids(
    session: AsyncSession, request: ValidationRequest
) -> list[str]:
    """Resolve the list of users eligible to review ``request``.

    Mirrors the scope check in :meth:`WorkflowService.create_validation_request`.
    """
    _ = selectinload  # imported to keep optional eager loading available
    stmt = select(User.id).where(
        User.role == request.reviewerRole,
        User.isActive.is_(True),
    )
    if request.reviewerRegionId is not None:
        stmt = stmt.where(User.regionId == request.reviewerRegionId)
    if request.reviewerPrefectureId is not None:
        stmt = stmt.where(User.prefectureId == request.reviewerPrefectureId)
    if request.reviewerSubPrefectureId is not None:
        stmt = stmt.where(User.subPrefectureId == request.reviewerSubPrefectureId)
    return list((await session.execute(stmt)).scalars().all())
