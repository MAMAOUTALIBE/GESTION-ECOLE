"""Workflow service — validation requests + notifications + entity status sync.

Mirrors NestJS workflow.service.ts (createValidationRequest, listValidationRequests,
review, notifications, unreadCount, markNotificationRead). Each entity type has
its own status update branch (Prefecture / SubPrefecture / School / Teacher).
"""
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ForbiddenError, NotFoundError
from app.modules.auth.models import User
from app.modules.census.models import Teacher
from app.modules.schools.models import School
from app.modules.territory.models import Prefecture, SubPrefecture
from app.modules.workflow.models import Notification, ValidationRequest
from app.modules.workflow.schemas import (
    NotificationRead,
    UnreadCountResponse,
    ValidationRequestRead,
)
from app.modules.workflow.sla import compute_sla_deadline
from app.shared.enums import (
    NotificationType,
    UserRole,
    ValidationEntityType,
    ValidationStatus,
)
from app.shared.permissions import NATIONAL_SCOPE_ROLES


@dataclass(frozen=True, slots=True)
class ValidationTarget:
    entity_type: ValidationEntityType
    entity_id: str
    requested_by_id: str
    reviewer_role: UserRole
    title: str
    message: str
    reviewer_region_id: str | None = None
    reviewer_prefecture_id: str | None = None
    reviewer_sub_prefecture_id: str | None = None


class WorkflowService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ==================================================================
    # CREATE (used by territory/census flows)
    # ==================================================================
    async def create_validation_request(
        self, target: ValidationTarget
    ) -> ValidationRequest:
        """Insert a ValidationRequest and notify all matching reviewers."""
        now = datetime.now(UTC)
        request = ValidationRequest(
            entityType=target.entity_type,
            entityId=target.entity_id,
            status=ValidationStatus.SUBMITTED,
            requestedById=target.requested_by_id,
            reviewerRole=target.reviewer_role,
            reviewerRegionId=target.reviewer_region_id,
            reviewerPrefectureId=target.reviewer_prefecture_id,
            reviewerSubPrefectureId=target.reviewer_sub_prefecture_id,
            slaDeadline=compute_sla_deadline(target.entity_type, now),
        )
        self.session.add(request)
        await self.session.flush()

        stmt = select(User.id).where(
            User.role == target.reviewer_role,
            User.isActive.is_(True),
        )
        if target.reviewer_region_id is not None:
            stmt = stmt.where(User.regionId == target.reviewer_region_id)
        if target.reviewer_prefecture_id is not None:
            stmt = stmt.where(User.prefectureId == target.reviewer_prefecture_id)
        if target.reviewer_sub_prefecture_id is not None:
            stmt = stmt.where(User.subPrefectureId == target.reviewer_sub_prefecture_id)

        recipient_ids = (await self.session.execute(stmt)).scalars().all()
        for recipient_id in recipient_ids:
            self.session.add(
                Notification(
                    recipientUserId=recipient_id,
                    senderUserId=target.requested_by_id,
                    title=target.title,
                    message=target.message,
                    type=NotificationType.VALIDATION_REQUEST,
                    entityType=target.entity_type,
                    entityId=target.entity_id,
                )
            )

        await self.session.flush()
        return request

    # ==================================================================
    # LIST + REVIEW
    # ==================================================================
    async def list_validation_requests(
        self, user: User, status: ValidationStatus | None = None
    ) -> list[ValidationRequestRead]:
        stmt = (
            select(ValidationRequest)
            .order_by(ValidationRequest.createdAt.desc())
            .options(
                selectinload(ValidationRequest.requestedBy),
                selectinload(ValidationRequest.reviewer),
            )
        )
        stmt = self._scope_validation_query(stmt, user)
        if status is not None:
            stmt = stmt.where(ValidationRequest.status == status)

        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [ValidationRequestRead.model_validate(r) for r in rows]

    async def review(
        self,
        user: User,
        request_id: str,
        new_status: ValidationStatus,
        reason: str | None,
    ) -> ValidationRequestRead:
        request = (
            await self.session.execute(
                select(ValidationRequest)
                .where(ValidationRequest.id == request_id)
                .options(selectinload(ValidationRequest.requestedBy))
            )
        ).scalar_one_or_none()
        if request is None:
            raise NotFoundError(detail="Demande de validation introuvable")

        if new_status not in (ValidationStatus.APPROVED, ValidationStatus.REJECTED):
            raise ForbiddenError(detail="Statut de validation non autorisé")

        if request.status != ValidationStatus.SUBMITTED:
            raise ForbiddenError(detail="Cette demande est déjà traitée")

        if not self._can_review(user, request):
            raise ForbiddenError(detail="Vous ne pouvez pas valider cette demande")

        cleaned_reason = (reason or "").strip() or None

        request.status = new_status
        request.reason = cleaned_reason
        request.reviewerUserId = user.id
        request.reviewedAt = datetime.now(UTC)

        await self._update_entity_status(
            request.entityType, request.entityId, user.id, new_status, cleaned_reason
        )

        # Notify the requester — legacy in-app Notification row (kept for
        # backwards compatibility with the existing frontend bell dropdown).
        if new_status == ValidationStatus.APPROVED:
            title = "Demande validée"
            message = "Votre demande a été validée par la hiérarchie."
            ntype = NotificationType.VALIDATION_APPROVED
        else:
            title = "Demande rejetée"
            message = (
                cleaned_reason
                or "Votre demande a été rejetée par la hiérarchie."
            )
            ntype = NotificationType.VALIDATION_REJECTED

        self.session.add(
            Notification(
                recipientUserId=request.requestedById,
                senderUserId=user.id,
                title=title,
                message=message,
                type=ntype,
                entityType=request.entityType,
                entityId=request.entityId,
            )
        )
        await self.session.flush()

        # Module 6 — additionally dispatch an i18n notification across SMS,
        # email and in_app channels using the requester's preferred language.
        await self._dispatch_review_i18n(
            request=request,
            requester=request.requestedBy,
            reviewer=user,
            new_status=new_status,
            reason=cleaned_reason,
        )

        # Reload with relations for the response
        loaded = (
            await self.session.execute(
                select(ValidationRequest)
                .where(ValidationRequest.id == request_id)
                .options(
                    selectinload(ValidationRequest.requestedBy),
                    selectinload(ValidationRequest.reviewer),
                )
            )
        ).scalar_one()
        return ValidationRequestRead.model_validate(loaded)

    # ==================================================================
    # NOTIFICATIONS
    # ==================================================================
    async def notifications(
        self, user: User, unread_only: bool = False
    ) -> list[NotificationRead]:
        stmt = (
            select(Notification)
            .where(Notification.recipientUserId == user.id)
            .order_by(Notification.createdAt.desc())
            .limit(100)
        )
        if unread_only:
            stmt = stmt.where(Notification.isRead.is_(False))
        rows = (await self.session.execute(stmt)).scalars().all()
        return [NotificationRead.model_validate(n) for n in rows]

    async def unread_count(self, user: User) -> UnreadCountResponse:
        from sqlalchemy import func

        count = (
            await self.session.execute(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.recipientUserId == user.id,
                    Notification.isRead.is_(False),
                )
            )
        ).scalar_one()
        return UnreadCountResponse(count=count)

    async def mark_notification_read(
        self, user: User, notification_id: str
    ) -> NotificationRead:
        notif = (
            await self.session.execute(
                select(Notification).where(
                    Notification.id == notification_id,
                    Notification.recipientUserId == user.id,
                )
            )
        ).scalar_one_or_none()
        if notif is None:
            raise NotFoundError(detail="Notification introuvable")

        notif.isRead = True
        notif.readAt = datetime.now(UTC)
        await self.session.flush()
        return NotificationRead.model_validate(notif)

    # ==================================================================
    # PRIVATE HELPERS
    # ==================================================================
    def _scope_validation_query(self, stmt: Any, user: User) -> Any:
        if user.role in NATIONAL_SCOPE_ROLES:
            return stmt
        # Match: requested-by-self OR (reviewer_role + scope match)
        reviewer_clauses = [ValidationRequest.reviewerRole == user.role]
        if user.regionId is not None:
            reviewer_clauses.append(
                or_(
                    ValidationRequest.reviewerRegionId.is_(None),
                    ValidationRequest.reviewerRegionId == user.regionId,
                )
            )
        else:
            reviewer_clauses.append(ValidationRequest.reviewerRegionId.is_(None))
        if user.prefectureId is not None:
            reviewer_clauses.append(
                or_(
                    ValidationRequest.reviewerPrefectureId.is_(None),
                    ValidationRequest.reviewerPrefectureId == user.prefectureId,
                )
            )
        else:
            reviewer_clauses.append(ValidationRequest.reviewerPrefectureId.is_(None))
        if user.subPrefectureId is not None:
            reviewer_clauses.append(
                or_(
                    ValidationRequest.reviewerSubPrefectureId.is_(None),
                    ValidationRequest.reviewerSubPrefectureId == user.subPrefectureId,
                )
            )
        else:
            reviewer_clauses.append(
                ValidationRequest.reviewerSubPrefectureId.is_(None)
            )

        from sqlalchemy import and_

        return stmt.where(
            or_(
                ValidationRequest.requestedById == user.id,
                and_(*reviewer_clauses),
            )
        )

    @staticmethod
    def _can_review(user: User, request: ValidationRequest) -> bool:
        if user.role in NATIONAL_SCOPE_ROLES:
            return True
        if user.role != request.reviewerRole:
            return False
        return (
            (request.reviewerRegionId is None or request.reviewerRegionId == user.regionId)
            and (
                request.reviewerPrefectureId is None
                or request.reviewerPrefectureId == user.prefectureId
            )
            and (
                request.reviewerSubPrefectureId is None
                or request.reviewerSubPrefectureId == user.subPrefectureId
            )
        )

    async def _dispatch_review_i18n(
        self,
        *,
        request: ValidationRequest,
        requester: User,
        reviewer: User,
        new_status: ValidationStatus,
        reason: str | None,
    ) -> None:
        """Send cross-channel i18n notification to the requester after a
        review decision (Module 6).

        Failures are swallowed: the legacy in-app row was already written,
        so the user is never left in the dark even if SMS/email transport
        explodes.
        """
        # Local import to avoid a service ↔ notifications circular import at
        # module load time. Both services are stateless apart from session.
        from app.modules.notifications.service import NotificationsService

        template_key = (
            "validation.approved"
            if new_status == ValidationStatus.APPROVED
            else "validation.rejected"
        )
        variables: dict[str, object] = {
            "entityLabel": f"{request.entityType.value} {request.entityId}",
            "recipientName": requester.fullName,
            "reviewerName": reviewer.fullName,
            "reason": reason or "",
        }
        notif_service = NotificationsService(self.session)
        for channel in ("sms", "email", "in_app"):
            try:
                await notif_service.send_via_template(
                    user_id=requester.id,
                    channel=channel,
                    template_key=template_key,
                    variables=variables,
                    language=requester.preferredLanguage,
                )
            except Exception:
                # Notification failure must not block the review commit.
                continue

    async def _update_entity_status(
        self,
        entity_type: ValidationEntityType,
        entity_id: str,
        reviewer_id: str,
        new_status: ValidationStatus,
        reason: str | None,
    ) -> None:
        rejection_reason = reason if new_status == ValidationStatus.REJECTED else None
        approved_by = reviewer_id if new_status == ValidationStatus.APPROVED else None
        approved_at = datetime.now(UTC) if new_status == ValidationStatus.APPROVED else None

        values = {
            "status": new_status,
            "rejectionReason": rejection_reason,
            "approvedById": approved_by,
            "approvedAt": approved_at,
        }

        model_map = {
            ValidationEntityType.PREFECTURE: Prefecture,
            ValidationEntityType.SUB_PREFECTURE: SubPrefecture,
            ValidationEntityType.SCHOOL: School,
            ValidationEntityType.TEACHER: Teacher,
        }
        model = model_map.get(entity_type)
        if model is None:
            return
        await self.session.execute(
            update(model).where(model.id == entity_id).values(**values)
        )
