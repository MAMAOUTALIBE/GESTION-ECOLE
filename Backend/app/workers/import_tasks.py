"""Celery tasks for mass imports — students / teachers / schools.

The web layer parses + validates synchronously and POSTs only the validated
rows back via ``/commit`` — this task does the actual DB writes.

Each row is processed in its own try/except so a single bad row does not
halt the whole batch. Failures are returned in the result + logged via
AuditLog (action=IMPORT_ROW_FAILED).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings


def _async_session_factory() -> async_sessionmaker:
    engine = create_async_engine(str(settings.database_url), pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)


def _parse_iso_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        d = date.fromisoformat(value)
    except ValueError:
        return None
    return datetime.combine(d, datetime.min.time(), tzinfo=UTC)


# ----------------------------------------------------------------------
# Per-kind row writer
# ----------------------------------------------------------------------
async def _import_student(session: AsyncSession, row: dict[str, Any]) -> str:
    from app.modules.attendance.models import QrCredential
    from app.modules.census.models import Student
    from app.modules.schools.models import ClassRoom, School
    from app.shared.enums import Gender, PersonType

    school = (
        await session.execute(
            select(School).where(School.code == row["schoolCode"])
        )
    ).scalar_one_or_none()
    if school is None:
        raise ValueError(f"École inconnue: {row['schoolCode']}")

    class_room_id: str | None = None
    class_room_name = row.get("classRoomName")
    if class_room_name:
        cls = (
            await session.execute(
                select(ClassRoom).where(
                    ClassRoom.schoolId == school.id,
                    ClassRoom.name == class_room_name,
                )
            )
        ).scalar_one_or_none()
        if cls is None:
            raise ValueError(
                f"Classe inconnue pour {school.code}: {class_room_name}"
            )
        class_room_id = cls.id

    # Generate uniqueCode (REGION-SCHOOL-ELV-YEAR-NNNNNN)
    from app.modules.territory.models import Region

    region = (
        await session.execute(select(Region).where(Region.id == school.regionId))
    ).scalar_one()
    year = datetime.now(UTC).year
    seq = (
        await _next_sequence(session, "Student", school.id) + 1  # type: ignore[arg-type]
    )
    unique_code = f"{region.code}-{school.code}-ELV-{year}-{seq:06d}"

    student = Student(
        uniqueCode=unique_code,
        firstName=row["firstName"],
        lastName=row["lastName"],
        gender=Gender(row["gender"]),
        birthDate=_parse_iso_date(row.get("birthDate")),
        guardianName=row.get("guardianName"),
        guardianPhone=row.get("guardianPhone"),
        schoolId=school.id,
        classRoomId=class_room_id,
    )
    session.add(student)
    await session.flush()
    session.add(
        QrCredential(
            token=uuid4().hex,
            payload=unique_code,
            personType=PersonType.STUDENT,
            studentId=student.id,
        )
    )
    return student.id


async def _import_teacher(session: AsyncSession, row: dict[str, Any]) -> str:
    from app.modules.attendance.models import QrCredential
    from app.modules.census.models import Teacher
    from app.modules.schools.models import School
    from app.modules.territory.models import Region
    from app.shared.enums import Gender, PersonType, ValidationStatus

    school = (
        await session.execute(select(School).where(School.code == row["schoolCode"]))
    ).scalar_one_or_none()
    if school is None:
        raise ValueError(f"École inconnue: {row['schoolCode']}")

    region = (
        await session.execute(select(Region).where(Region.id == school.regionId))
    ).scalar_one()

    year = datetime.now(UTC).year
    seq = await _next_sequence(session, "Teacher", school.id) + 1
    unique_code = f"{region.code}-{school.code}-ENS-{year}-{seq:06d}"

    teacher = Teacher(
        uniqueCode=unique_code,
        firstName=row["firstName"],
        lastName=row["lastName"],
        gender=Gender(row["gender"]),
        birthDate=_parse_iso_date(row.get("birthDate")),
        phone=row.get("phone"),
        subject=row.get("subject"),
        diploma=row.get("diploma"),
        schoolId=school.id,
        # Bulk imports are pre-validated by the operator → APPROVED directly
        status=ValidationStatus.APPROVED,
        approvedAt=datetime.now(UTC),
    )
    session.add(teacher)
    await session.flush()
    session.add(
        QrCredential(
            token=uuid4().hex,
            payload=unique_code,
            personType=PersonType.TEACHER,
            teacherId=teacher.id,
        )
    )
    return teacher.id


async def _import_school(session: AsyncSession, row: dict[str, Any]) -> str:
    from app.modules.schools.models import School
    from app.modules.territory.models import Region
    from app.shared.enums import ValidationStatus

    region = (
        await session.execute(select(Region).where(Region.code == row["regionCode"]))
    ).scalar_one_or_none()
    if region is None:
        raise ValueError(f"Région inconnue: {row['regionCode']}")

    # Idempotency: if a school with this code exists, update it instead of inserting.
    existing = (
        await session.execute(select(School).where(School.code == row["code"]))
    ).scalar_one_or_none()
    if existing is not None:
        existing.name = row["name"]
        existing.regionId = region.id
        existing.prefecture = row.get("prefecture")
        existing.commune = row.get("commune")
        existing.address = row.get("address")
        existing.phone = row.get("phone")
        existing.latitude = row.get("latitude")
        existing.longitude = row.get("longitude")
        await session.flush()
        return existing.id

    school = School(
        name=row["name"],
        code=row["code"],
        regionId=region.id,
        prefecture=row.get("prefecture"),
        commune=row.get("commune"),
        address=row.get("address"),
        phone=row.get("phone"),
        latitude=row.get("latitude"),
        longitude=row.get("longitude"),
        status=ValidationStatus.APPROVED,
        approvedAt=datetime.now(UTC),
    )
    session.add(school)
    await session.flush()
    return school.id


async def _next_sequence(
    session: AsyncSession, kind: str, school_id: str
) -> int:
    """Best-effort: count existing rows for the school. Race with concurrent
    inserts is acceptable here because the unique-code generator retries on
    collision (the per-kind importer above doesn't yet — this is a known
    limitation; mass-import is operator-driven, not concurrent).
    """
    from sqlalchemy import func

    if kind == "Student":
        from app.modules.census.models import Student as Model
    else:
        from app.modules.census.models import Teacher as Model
    return (
        await session.execute(
            select(func.count())
            .select_from(Model)
            .where(Model.schoolId == school_id)
        )
    ).scalar_one()


_IMPORTERS = {
    "students": _import_student,
    "teachers": _import_teacher,
    "schools": _import_school,
}


async def _process_batch(
    kind: str, rows: list[dict[str, Any]], requested_by: str | None
) -> dict[str, Any]:
    from app.core.observability import import_commit_total
    from app.modules.workflow.models import AuditLog

    factory = _async_session_factory()
    succeeded: list[str] = []
    failed: list[dict[str, Any]] = []

    importer = _IMPORTERS.get(kind)
    if importer is None:
        return {
            "total": len(rows),
            "succeeded": 0,
            "failed": len(rows),
            "failures": [{"error": f"unknown kind: {kind}"}],
        }

    async with factory() as session:
        for index, row in enumerate(rows):
            try:
                created_id = await importer(session, row)
                await session.commit()
                succeeded.append(created_id)
                import_commit_total.labels(kind=kind, result="ok").inc()
            except Exception as exc:
                await session.rollback()
                failed.append({"index": index, "row": row, "error": str(exc)})
                import_commit_total.labels(kind=kind, result="failed").inc()
                # Log each failure with full context
                async with factory() as log_session:
                    log_session.add(
                        AuditLog(
                            actorId=requested_by,
                            action="IMPORT_ROW_FAILED",
                            entity=kind,
                            entityId=None,
                            metadata_={
                                "index": index,
                                "error": str(exc)[:500],
                            },
                        )
                    )
                    await log_session.commit()

    return {
        "kind": kind,
        "total": len(rows),
        "succeeded": len(succeeded),
        "failed": len(failed),
        "failures": failed[:20],  # cap response size
    }


# ----------------------------------------------------------------------
# Celery entry point
# ----------------------------------------------------------------------
@celery_app.task(name="import.import_rows", bind=True)
def import_rows(
    self, kind: str, rows: list[dict[str, Any]], requested_by: str | None = None
) -> dict[str, Any]:
    self.update_state(
        state="STARTED", meta={"kind": kind, "total": len(rows)}
    )
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            _process_batch(kind, rows, requested_by)
        )
    finally:
        loop.close()


@celery_app.task(name="import.noop")
def noop() -> str:
    """Placeholder retained for compatibility."""
    return "import.noop ok"
