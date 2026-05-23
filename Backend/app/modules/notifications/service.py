"""Notifications service — persists ParentCommunication rows + queues dispatch.

The actual transport call happens in the Celery worker
(``app.workers.notification_tasks``); the service writes the row in DRAFT,
returns immediately, and lets the worker flip the status to SENT or FAILED.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.modules.academics.models import Parent, ParentCommunication
from app.modules.auth.models import User
from app.modules.census.models import Student
from app.modules.notifications.channels.base import ChannelMessage
from app.modules.notifications.dispatcher import dispatch as dispatch_async
from app.modules.notifications.schemas import (
    BulkCommunicationRequest,
    BulkCommunicationResponse,
    CommunicationRead,
    CreateCommunicationRequest,
    DispatchTestRequest,
    DispatchTestResponse,
)
from app.modules.workflow.models import AuditLog
from app.shared.enums import CommunicationChannel, CommunicationStatus


def _resolve_recipient(parent: Parent, channel: CommunicationChannel) -> str | None:
    """Pick the right destination string (phone/email/userId) for ``channel``."""
    if channel in (
        CommunicationChannel.SMS,
        CommunicationChannel.WHATSAPP,
        CommunicationChannel.PHONE,
        CommunicationChannel.IN_APP,  # IN_APP needs a User.id, see note below
    ):
        # IN_APP currently has no User linked to Parent in the schema, so
        # fall back to phone — the inapp adapter requires a user id, so
        # this combination is rejected upstream when no link exists.
        return parent.phone
    if channel == CommunicationChannel.EMAIL:
        return parent.email
    return None


class NotificationsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ==================================================================
    # CREATE — single
    # ==================================================================
    async def create(
        self, user: User, dto: CreateCommunicationRequest
    ) -> CommunicationRead:
        parent = await self.session.get(Parent, dto.parentId)
        if parent is None:
            raise NotFoundError(detail="Parent introuvable")
        if dto.studentId is not None:
            student = await self.session.get(Student, dto.studentId)
            if student is None:
                raise NotFoundError(detail="Élève introuvable")

        if _resolve_recipient(parent, dto.channel) is None:
            raise ConflictError(
                detail=(
                    f"Aucun destinataire valide pour le canal {dto.channel.value} "
                    "(téléphone ou email manquant pour ce parent)."
                )
            )

        comm = ParentCommunication(
            parentId=dto.parentId,
            studentId=dto.studentId,
            channel=dto.channel,
            status=CommunicationStatus.DRAFT,
            subject=dto.subject,
            message=dto.message,
        )
        self.session.add(comm)
        await self.session.flush()

        self.session.add(
            AuditLog(
                actorId=user.id,
                action="CREATE_COMMUNICATION",
                entity="ParentCommunication",
                entityId=comm.id,
                metadata_={
                    "parentId": dto.parentId,
                    "channel": dto.channel.value,
                    "sendNow": dto.sendNow,
                },
            )
        )
        await self.session.flush()

        if dto.sendNow:
            from app.workers.notification_tasks import dispatch_communication

            dispatch_communication.delay(comm.id)

        return CommunicationRead.model_validate(comm)

    # ==================================================================
    # CREATE — bulk
    # ==================================================================
    async def create_bulk(
        self, user: User, dto: BulkCommunicationRequest
    ) -> BulkCommunicationResponse:
        unique_parent_ids = list({pid for pid in dto.parentIds if pid})
        if not unique_parent_ids:
            raise ConflictError(detail="Liste de parents vide après déduplication.")

        rows = (
            await self.session.execute(
                select(Parent).where(Parent.id.in_(unique_parent_ids))
            )
        ).scalars().all()
        if len(rows) != len(unique_parent_ids):
            raise NotFoundError(
                detail="Un ou plusieurs parents sont introuvables."
            )

        # Filter out parents missing the right address for the channel
        eligible: list[Parent] = []
        skipped = 0
        for parent in rows:
            if _resolve_recipient(parent, dto.channel) is not None:
                eligible.append(parent)
            else:
                skipped += 1

        new_ids: list[str] = []
        for parent in eligible:
            comm = ParentCommunication(
                parentId=parent.id,
                studentId=dto.studentId,
                channel=dto.channel,
                status=CommunicationStatus.DRAFT,
                subject=dto.subject,
                message=dto.message,
            )
            self.session.add(comm)
            await self.session.flush()
            new_ids.append(comm.id)

        self.session.add(
            AuditLog(
                actorId=user.id,
                action="CREATE_BULK_COMMUNICATION",
                entity="ParentCommunication",
                entityId=None,
                metadata_={
                    "channel": dto.channel.value,
                    "queued": len(new_ids),
                    "skipped": skipped,
                },
            )
        )
        await self.session.flush()

        task_id: str | None = None
        if new_ids:
            from app.workers.notification_tasks import (
                dispatch_communications_batch,
            )

            task = dispatch_communications_batch.delay(new_ids)
            task_id = task.id

        return BulkCommunicationResponse(queued=len(new_ids), taskId=task_id)

    # ==================================================================
    # LIST + GET + RETRY
    # ==================================================================
    async def list_communications(
        self,
        user: User,
        parent_id: str | None,
        student_id: str | None,
        status: CommunicationStatus | None,
        limit: int,
    ) -> list[CommunicationRead]:
        stmt = (
            select(ParentCommunication)
            .order_by(ParentCommunication.createdAt.desc())
            .limit(limit)
        )
        if parent_id:
            stmt = stmt.where(ParentCommunication.parentId == parent_id)
        if student_id:
            stmt = stmt.where(ParentCommunication.studentId == student_id)
        if status:
            stmt = stmt.where(ParentCommunication.status == status)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [CommunicationRead.model_validate(r) for r in rows]

    async def get(self, comm_id: str) -> CommunicationRead:
        comm = await self.session.get(ParentCommunication, comm_id)
        if comm is None:
            raise NotFoundError(detail="Communication introuvable")
        return CommunicationRead.model_validate(comm)

    async def retry(self, user: User, comm_id: str) -> CommunicationRead:
        comm = await self.session.get(ParentCommunication, comm_id)
        if comm is None:
            raise NotFoundError(detail="Communication introuvable")
        if comm.status not in (CommunicationStatus.FAILED, CommunicationStatus.DRAFT):
            raise ForbiddenError(
                detail="Seules les communications FAILED ou DRAFT peuvent être retentées."
            )
        comm.status = CommunicationStatus.DRAFT
        self.session.add(
            AuditLog(
                actorId=user.id,
                action="RETRY_COMMUNICATION",
                entity="ParentCommunication",
                entityId=comm.id,
            )
        )
        await self.session.flush()

        from app.workers.notification_tasks import dispatch_communication

        dispatch_communication.delay(comm.id)
        return CommunicationRead.model_validate(comm)

    # ==================================================================
    # TEST DISPATCH (national admins) — bypasses the ParentCommunication row
    # ==================================================================
    async def dispatch_test(self, dto: DispatchTestRequest) -> DispatchTestResponse:
        msg = ChannelMessage(
            recipient=dto.recipient, message=dto.message, subject=dto.subject
        )
        result = await dispatch_async(dto.channel, msg, session=self.session)
        return DispatchTestResponse(
            ok=result.ok, providerId=result.provider_id, error=result.error
        )

    # ==================================================================
    # WORKER HELPERS — called from Celery task, NOT from the API
    # ==================================================================
    async def mark_sent(self, comm_id: str, provider_id: str | None) -> None:
        comm = await self._load_or_raise(comm_id)
        comm.status = CommunicationStatus.SENT
        comm.sentAt = datetime.now(UTC)
        if provider_id:
            # We don't have a provider_id column — log it via AuditLog.
            self.session.add(
                AuditLog(
                    action="COMMUNICATION_SENT",
                    entity="ParentCommunication",
                    entityId=comm_id,
                    metadata_={"providerId": provider_id},
                )
            )
        await self.session.flush()

    async def mark_failed(self, comm_id: str, error: str) -> None:
        comm = await self._load_or_raise(comm_id)
        comm.status = CommunicationStatus.FAILED
        self.session.add(
            AuditLog(
                action="COMMUNICATION_FAILED",
                entity="ParentCommunication",
                entityId=comm_id,
                metadata_={"error": error[:500]},
            )
        )
        await self.session.flush()

    async def load_dispatch_payload(self, comm_id: str) -> dict[str, Any]:
        """Worker-side: load the row + parent contact info needed to dispatch."""
        comm = (
            await self.session.execute(
                select(ParentCommunication)
                .where(ParentCommunication.id == comm_id)
                .options(selectinload(ParentCommunication.parent))
            )
        ).scalar_one_or_none()
        if comm is None:
            raise NotFoundError(detail="Communication introuvable")
        recipient = _resolve_recipient(comm.parent, comm.channel)
        if recipient is None:
            raise ConflictError(
                detail=(
                    f"Aucun destinataire pour {comm.channel.value} "
                    f"(parent {comm.parentId})."
                )
            )
        return {
            "id": comm.id,
            "channel": comm.channel,
            "recipient": recipient,
            "subject": comm.subject,
            "message": comm.message,
        }

    async def _load_or_raise(self, comm_id: str) -> ParentCommunication:
        comm = await self.session.get(ParentCommunication, comm_id)
        if comm is None:
            raise NotFoundError(detail="Communication introuvable")
        return comm
