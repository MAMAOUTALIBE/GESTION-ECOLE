"""Module 5D — Service du droit à l'oubli.

Responsabilités :

* ``request_erasure`` — création d'une demande, status=GRACE_PERIOD,
  audit PII obligatoire.
* ``list_pending_erasures`` — listing scoped (NATIONAL / MINISTRY).
* ``get_erasure`` — détail.
* ``cancel_erasure`` — annulation pendant la grace period.
* ``execute_pending_erasures`` — batch (NATIONAL only) qui scanne les
  demandes prêtes et appelle ``anonymize_student``.

RBAC strict : seuls NATIONAL_ADMIN + MINISTRY_ADMIN peuvent demander,
lister, consulter, annuler. NATIONAL_ADMIN seul peut déclencher
l'exécution effective (cohérent avec la doctrine 5C — le ministère
contrôle, le national exécute).

Audit : chaque action enregistre une ligne PiiAccessLog (entityType=
STUDENT, accessType=EXPORT) avec ``metadata`` documentant l'opération.
La capture audit est best-effort — si elle échoue, le flux principal
continue (cf. PiiAuditService.log_access).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.modules.auth.models import User
from app.modules.census.models import Student
from app.modules.erasure.anonymizer import anonymize_student, initials_for
from app.modules.erasure.enums import (
    GRACE_PERIOD_DAYS,
    ErasureStatus,
)
from app.modules.erasure.models import ErasureRequest
from app.modules.erasure.schemas import (
    CancelErasureRequest,
    ErasureRequestCreate,
    ErasureRequestRead,
)
from app.modules.pii_audit.enums import PiiAccessType, PiiEntityType
from app.modules.pii_audit.service import PiiAuditService
from app.shared.base import generate_cuid
from app.shared.enums import UserRole

# RBAC matrices ---------------------------------------------------------
ERASURE_ADMIN_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN}
)
ERASURE_EXECUTE_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.NATIONAL_ADMIN}
)


class ErasureService:
    """Service stateless ; une instance par requête."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        # PiiAuditService partage la session ; on n'expose pas Request
        # ici (l'audit fonctionne sans, avec actor seul).
        self._audit = PiiAuditService(session)

    async def _audit_safely(
        self,
        *,
        actor: User,
        entity_id: str,
        endpoint: str,
        metadata: dict[str, str | int | dict[str, int]],
    ) -> None:
        """Wrappe ``PiiAuditService.log_access`` dans un SAVEPOINT.

        Pourquoi ? L'audit doit RESTER best-effort : si la table
        ``PiiAccessLog`` est indisponible ou si un JSONB échoue à
        s'écrire (cas ENV de test en SQL_ASCII), la session
        SQLAlchemy passe en ``DEACTIVE`` même si ``log_access``
        attrape l'exception — parce que le flush a déjà invalidé
        la transaction. Un SAVEPOINT isole l'échec sans toucher
        la transaction principale.
        """
        try:
            async with self.session.begin_nested():
                await self._audit.log_access(
                    actor=actor,
                    entity_type=PiiEntityType.STUDENT,
                    entity_id=entity_id,
                    access_type=PiiAccessType.EXPORT,
                    endpoint=endpoint,
                    metadata=metadata,  # type: ignore[arg-type]
                )
        except Exception as exc:
            logger.warning(
                "erasure.audit_failed entity_id={} endpoint={}: {}",
                entity_id,
                endpoint,
                exc,
            )

    # ==================================================================
    # Helpers
    # ==================================================================
    def _ensure_admin(self, actor: User) -> None:
        if actor.role not in ERASURE_ADMIN_ROLES:
            raise ForbiddenError(
                detail=(
                    "Seuls NATIONAL_ADMIN / MINISTRY_ADMIN peuvent gérer "
                    "les demandes de droit à l'oubli."
                ),
                extra={"required_any_of": [r.value for r in ERASURE_ADMIN_ROLES]},
            )

    def _ensure_executor(self, actor: User) -> None:
        if actor.role not in ERASURE_EXECUTE_ROLES:
            raise ForbiddenError(
                detail=(
                    "Seul NATIONAL_ADMIN peut exécuter le batch "
                    "d'anonymisation effective."
                ),
                extra={"required_any_of": [r.value for r in ERASURE_EXECUTE_ROLES]},
            )

    async def _build_read(
        self,
        row: ErasureRequest,
    ) -> ErasureRequestRead:
        """Sérialise une ligne en DTO avec initiales calculées."""
        initials: str | None = None
        if row.studentId is not None:
            stu = (
                await self.session.execute(
                    select(Student).where(Student.id == row.studentId)
                )
            ).scalars().one_or_none()
            if stu is not None:
                initials = initials_for(stu.firstName, stu.lastName)
        return ErasureRequestRead(
            id=row.id,
            studentId=row.studentId,
            studentInitials=initials,
            reason=row.reason,
            reasonDetails=row.reasonDetails,
            status=row.status,
            requestedAt=row.requestedAt,
            requestedById=row.requestedById,
            gracePeriodUntil=row.gracePeriodUntil,
            executedAt=row.executedAt,
            executedById=row.executedById,
            cancelledAt=row.cancelledAt,
            cancelledById=row.cancelledById,
            cancellationReason=row.cancellationReason,
        )

    # ==================================================================
    # CREATE
    # ==================================================================
    async def request_erasure(
        self,
        dto: ErasureRequestCreate,
        actor: User,
    ) -> ErasureRequestRead:
        """Crée une demande, met en GRACE_PERIOD, audit PII.

        Refuse si une demande active (GRACE_PERIOD) existe déjà pour
        cet élève — pour éviter les doublons par erreur.
        """
        self._ensure_admin(actor)

        # 1. Validation existence Student.
        student = (
            await self.session.execute(
                select(Student).where(Student.id == dto.studentId)
            )
        ).scalars().one_or_none()
        if student is None:
            raise NotFoundError(
                detail=f"Élève introuvable : {dto.studentId}",
            )

        # 2. Refus si demande active déjà en cours.
        existing = (
            await self.session.execute(
                select(ErasureRequest).where(
                    and_(
                        ErasureRequest.studentId == dto.studentId,
                        ErasureRequest.status.in_(
                            (
                                ErasureStatus.PENDING,
                                ErasureStatus.GRACE_PERIOD,
                            )
                        ),
                    )
                )
            )
        ).scalars().first()
        if existing is not None:
            raise ConflictError(
                detail=(
                    "Une demande de droit à l'oubli est déjà en cours "
                    "pour cet élève."
                ),
                extra={"existingRequestId": existing.id},
            )

        # 3. Création.
        now = datetime.now(UTC)
        row = ErasureRequest(
            id=generate_cuid(),
            studentId=dto.studentId,
            reason=dto.reason,
            reasonDetails=dto.reasonDetails,
            requestedById=actor.id,
            requestedAt=now,
            status=ErasureStatus.GRACE_PERIOD,
            gracePeriodUntil=now + timedelta(days=GRACE_PERIOD_DAYS),
        )
        self.session.add(row)
        await self.session.flush()

        # 4. Audit (best-effort, isolé en SAVEPOINT).
        await self._audit_safely(
            actor=actor,
            entity_id=dto.studentId,
            endpoint="POST /api/erasure/requests",
            metadata={
                "action": "REQUEST_ERASURE",
                "reason": dto.reason.value,
                "erasureRequestId": row.id,
                "gracePeriodUntil": row.gracePeriodUntil.isoformat(),
            },
        )

        # 5. Notif best-effort. On garde un log structuré loguru — un
        #    hook email/teams pourra l'écouter sans bloquer si absent.
        logger.info(
            "erasure.requested id={} student={} actor={} reason={}",
            row.id,
            dto.studentId,
            actor.id,
            dto.reason.value,
        )

        return await self._build_read(row)

    # ==================================================================
    # READ
    # ==================================================================
    async def list_pending_erasures(
        self,
        actor: User,
        *,
        status: ErasureStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ErasureRequestRead]:
        """Liste les demandes (filtre optionnel par statut)."""
        self._ensure_admin(actor)
        stmt = select(ErasureRequest).order_by(
            ErasureRequest.requestedAt.desc()
        )
        if status is not None:
            stmt = stmt.where(ErasureRequest.status == status)
        stmt = stmt.limit(max(1, min(int(limit), 500))).offset(max(0, int(offset)))
        rows = (await self.session.execute(stmt)).scalars().all()
        return [await self._build_read(r) for r in rows]

    async def get_erasure(
        self,
        erasure_id: str,
        actor: User,
    ) -> ErasureRequestRead:
        """Détail d'une demande."""
        self._ensure_admin(actor)
        row = (
            await self.session.execute(
                select(ErasureRequest).where(ErasureRequest.id == erasure_id)
            )
        ).scalars().one_or_none()
        if row is None:
            raise NotFoundError(
                detail=f"Demande de droit à l'oubli introuvable : {erasure_id}",
            )
        return await self._build_read(row)

    # ==================================================================
    # CANCEL
    # ==================================================================
    async def cancel_erasure(
        self,
        erasure_id: str,
        dto: CancelErasureRequest,
        actor: User,
    ) -> ErasureRequestRead:
        """Annule une demande pendant la grace period.

        Refus si la demande est déjà EXECUTED ou CANCELLED.
        """
        self._ensure_admin(actor)
        row = (
            await self.session.execute(
                select(ErasureRequest).where(ErasureRequest.id == erasure_id)
            )
        ).scalars().one_or_none()
        if row is None:
            raise NotFoundError(
                detail=f"Demande de droit à l'oubli introuvable : {erasure_id}",
            )
        if row.status not in (
            ErasureStatus.PENDING,
            ErasureStatus.GRACE_PERIOD,
        ):
            raise ConflictError(
                detail=(
                    "Cette demande ne peut plus être annulée "
                    f"(statut actuel : {row.status.value})."
                ),
                extra={"currentStatus": row.status.value},
            )

        now = datetime.now(UTC)
        row.status = ErasureStatus.CANCELLED
        row.cancelledAt = now
        row.cancelledById = actor.id
        row.cancellationReason = dto.cancellationReason
        await self.session.flush()

        await self._audit_safely(
            actor=actor,
            entity_id=row.studentId or "*",
            endpoint=f"POST /api/erasure/requests/{erasure_id}/cancel",
            metadata={
                "action": "CANCEL_ERASURE",
                "erasureRequestId": row.id,
                "cancellationReason": dto.cancellationReason,
            },
        )
        logger.info(
            "erasure.cancelled id={} actor={}",
            row.id,
            actor.id,
        )
        return await self._build_read(row)

    # ==================================================================
    # EXECUTE
    # ==================================================================
    async def execute_pending_erasures(
        self,
        actor: User,
    ) -> dict[str, int]:
        """Batch : exécute toutes les demandes éligibles.

        Une demande est éligible si ``status == GRACE_PERIOD`` ET
        ``gracePeriodUntil < now``. Pour chacune :

        1. Anonymise le student (anonymizer.anonymize_student).
        2. Bascule la demande en EXECUTED + remplit executedAt/executedById.
        3. Audit PII (EXPORT, metadata={action: EXECUTE_ERASURE, counts}).

        Si ``studentId`` est NULL (étrange — l'élève a été supprimé
        physiquement avant la grace period), on skip mais on bascule
        en EXECUTED pour ne pas re-tenter en boucle (counts vides
        consignés dans l'audit).
        """
        self._ensure_executor(actor)

        now = datetime.now(UTC)
        ready_stmt = select(ErasureRequest).where(
            and_(
                ErasureRequest.status == ErasureStatus.GRACE_PERIOD,
                ErasureRequest.gracePeriodUntil < now,
            )
        )
        rows = (await self.session.execute(ready_stmt)).scalars().all()

        executed = 0
        skipped = 0
        for row in rows:
            try:
                if row.studentId is None:
                    skipped += 1
                    counts: dict[str, int] = {"Student": 0}
                else:
                    counts = await anonymize_student(self.session, row.studentId)

                row.status = ErasureStatus.EXECUTED
                row.executedAt = now
                row.executedById = actor.id
                await self.session.flush()

                await self._audit_safely(
                    actor=actor,
                    entity_id=row.studentId or "*",
                    endpoint="POST /api/erasure/execute-pending",
                    metadata={
                        "action": "EXECUTE_ERASURE",
                        "erasureRequestId": row.id,
                        "counts": counts,
                    },
                )
                logger.info(
                    "erasure.executed id={} student={} counts={}",
                    row.id,
                    row.studentId,
                    counts,
                )
                executed += 1
            except Exception as exc:
                # Une erreur sur UNE demande ne doit pas planter le batch.
                # On laisse la demande en GRACE_PERIOD pour ré-essayer
                # demain. Sentry verra l'erreur via loguru.
                logger.error(
                    "erasure.execute_failed id={} student={}: {}",
                    row.id,
                    row.studentId,
                    exc,
                )
                skipped += 1

        return {"executed": executed, "skipped": skipped}


__all__ = [
    "ERASURE_ADMIN_ROLES",
    "ERASURE_EXECUTE_ROLES",
    "ErasureService",
]
