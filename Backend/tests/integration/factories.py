"""factory-boy factories pour les tests d'integration.

Conventions
-----------
* Les factories utilisent `factory.Faker` avec la locale `fr_FR` pour generer
  des donnees realistes (noms, telephones, adresses).
* Les ids respectent le format cuid (25 chars) genere par `generate_cuid()`.
* `SchoolFactory` n'inscrit PAS de geom PostGIS — les coordonnees lat/lon
  restent dans la bounding box de la Guinee (lat 7.2-12.7, lon -15.0--7.6).
  Quand l'extension PostGIS est presente, le trigger SQL `trg_school_sync_geom`
  (defini en migration 0003) synchronise la colonne `geom` automatiquement
  depuis lat/lon — donc rien a faire cote test.
* Les factories ont besoin d'une session SQLAlchemy : il faut appeler
  `factory_session(db_session)` au debut du test ou utiliser la fixture
  `db_session` via les factories `Async*Factory.set_session(...)`.

  Pour rester simple en Module 0, on expose un helper `bind(db_session)`
  qui assigne la session sur toutes les factories d'un coup.
"""

from __future__ import annotations

import random
from typing import Any

import factory
from factory.alchemy import SQLAlchemyModelFactory
from faker import Faker
from sqlalchemy.ext.asyncio import AsyncSession

from datetime import UTC, datetime, timedelta

from app.core.security import hash_password, hash_token
from app.modules.auth.models import (
    AuthAuditLog,
    MfaCredential,
    PasswordHistory,
    PasswordResetToken,
    RefreshTokenSession,
    User,
)
from app.modules.census.models import Student, Teacher
from app.modules.schools.models import ClassRoom, School
from app.modules.territory.models import Prefecture, Region, SubPrefecture
from app.shared.base import generate_cuid
from app.shared.enums import (
    Gender,
    SchoolAffiliation,
    UserRole,
    ValidationStatus,
)

fake = Faker("fr_FR")
Faker.seed(0)  # determinisme par defaut ; les tests peuvent re-seeder

# Bounding box Guinee (approximative mais largement suffisante pour des tests).
GUINEA_LAT_MIN, GUINEA_LAT_MAX = 7.2, 12.7
GUINEA_LON_MIN, GUINEA_LON_MAX = -15.0, -7.6


def random_guinea_lat() -> float:
    return round(random.uniform(GUINEA_LAT_MIN, GUINEA_LAT_MAX), 6)


def random_guinea_lon() -> float:
    return round(random.uniform(GUINEA_LON_MIN, GUINEA_LON_MAX), 6)


# ---------------------------------------------------------------------------
# Base factory : on N'utilise PAS SQLAlchemyModelFactory en mode async parce
# que factory-boy<3.4 ne sait pas appeler `session.add()` sur AsyncSession.
# A la place, on garde une session "ambient" thread-local et on l'ajoute
# manuellement dans `_create`.
# ---------------------------------------------------------------------------
class _AsyncSessionRegistry:
    """Container minimaliste pour partager la session entre factories.

    Usage typique dans un test :

        async def test_xxx(db_session):
            factories.bind(db_session)
            user = await factories.UserFactory.create_async(role=UserRole.TEACHER)

    On utilise une classe singleton plutot qu'un context var pour rester
    compatible pytest-xdist (chaque worker a son propre process).
    """

    _session: AsyncSession | None = None

    @classmethod
    def set(cls, session: AsyncSession) -> None:
        cls._session = session

    @classmethod
    def get(cls) -> AsyncSession:
        if cls._session is None:
            raise RuntimeError(
                "No AsyncSession bound. Call factories.bind(db_session) first."
            )
        return cls._session


def bind(session: AsyncSession) -> None:
    """Bind l'AsyncSession utilisee par toutes les factories du module."""
    _AsyncSessionRegistry.set(session)


class _AsyncBaseFactory(SQLAlchemyModelFactory):
    """Base pour toutes nos factories.

    On override `_create` pour qu'il NE FASSE PAS d'`Session.add()` synchrone
    (incompatible avec AsyncSession). A la place, on construit l'instance et
    on l'ajoute via la helper async `create_async`.
    """

    class Meta:
        abstract = True
        sqlalchemy_session = None  # rien — on gere a la main
        sqlalchemy_session_persistence = None

    @classmethod
    def _create(cls, model_class: type[Any], *args: Any, **kwargs: Any) -> Any:
        # Construit l'instance sans toucher a la DB.
        return model_class(*args, **kwargs)

    @classmethod
    async def create_async(cls, **kwargs: Any) -> Any:
        instance = cls(**kwargs)
        session = _AsyncSessionRegistry.get()
        session.add(instance)
        await session.flush()
        return instance

    @classmethod
    async def create_batch_async(cls, size: int, **kwargs: Any) -> list[Any]:
        return [await cls.create_async(**kwargs) for _ in range(size)]


# ---------------------------------------------------------------------------
# Territoire — Region / Prefecture / SubPrefecture
# ---------------------------------------------------------------------------
class RegionFactory(_AsyncBaseFactory):
    class Meta:
        model = Region

    id = factory.LazyFunction(generate_cuid)
    name = factory.Faker("city", locale="fr_FR")
    code = factory.Sequence(lambda n: f"REG-{n:04d}")


class PrefectureFactory(_AsyncBaseFactory):
    class Meta:
        model = Prefecture

    id = factory.LazyFunction(generate_cuid)
    name = factory.Faker("city", locale="fr_FR")
    code = factory.Sequence(lambda n: f"PREF-{n:04d}")
    regionId = ""  # a passer explicitement par le test
    status = ValidationStatus.APPROVED


class SubPrefectureFactory(_AsyncBaseFactory):
    class Meta:
        model = SubPrefecture

    id = factory.LazyFunction(generate_cuid)
    name = factory.Faker("city", locale="fr_FR")
    code = factory.Sequence(lambda n: f"SPREF-{n:04d}")
    regionId = ""
    prefectureId = ""
    status = ValidationStatus.APPROVED


# ---------------------------------------------------------------------------
# Ecoles & classes
# ---------------------------------------------------------------------------
class SchoolFactory(_AsyncBaseFactory):
    class Meta:
        model = School

    id = factory.LazyFunction(generate_cuid)
    name = factory.LazyAttribute(lambda _: f"Ecole {fake.last_name()}")
    code = factory.Sequence(lambda n: f"SCH-{n:06d}")
    regionId = ""  # a passer par le test
    prefectureId = None
    subPrefectureId = None

    address = factory.Faker("street_address", locale="fr_FR")
    type = factory.LazyFunction(lambda: random.choice(["PRIMARY", "SECONDARY"]))
    phone = factory.Faker("phone_number", locale="fr_FR")
    latitude = factory.LazyFunction(random_guinea_lat)
    longitude = factory.LazyFunction(random_guinea_lon)
    status = ValidationStatus.APPROVED
    affiliation = factory.LazyFunction(lambda: random.choice(list(SchoolAffiliation)))


class ClassRoomFactory(_AsyncBaseFactory):
    class Meta:
        model = ClassRoom

    id = factory.LazyFunction(generate_cuid)
    name = factory.Sequence(lambda n: f"6e-{chr(65 + (n % 26))}")
    level = factory.LazyFunction(lambda: random.choice(["CP1", "CP2", "CE1", "6e", "5e"]))
    maxStudents = factory.LazyFunction(lambda: random.choice([30, 40, 50]))
    schoolYear = "2025-2026"
    schoolId = ""  # a passer par le test


# ---------------------------------------------------------------------------
# Census — Students / Teachers
# ---------------------------------------------------------------------------
class StudentFactory(_AsyncBaseFactory):
    class Meta:
        model = Student

    id = factory.LazyFunction(generate_cuid)
    uniqueCode = factory.Sequence(lambda n: f"STU-{n:08d}")
    firstName = factory.Faker("first_name", locale="fr_FR")
    lastName = factory.Faker("last_name", locale="fr_FR")
    gender = factory.LazyFunction(lambda: random.choice([Gender.FEMALE, Gender.MALE]))
    guardianName = factory.Faker("name", locale="fr_FR")
    guardianPhone = factory.Faker("phone_number", locale="fr_FR")
    schoolId = ""  # a passer par le test
    classRoomId = None


class TeacherFactory(_AsyncBaseFactory):
    class Meta:
        model = Teacher

    id = factory.LazyFunction(generate_cuid)
    uniqueCode = factory.Sequence(lambda n: f"TCH-{n:06d}")
    firstName = factory.Faker("first_name", locale="fr_FR")
    lastName = factory.Faker("last_name", locale="fr_FR")
    gender = factory.LazyFunction(lambda: random.choice([Gender.FEMALE, Gender.MALE]))
    phone = factory.Faker("phone_number", locale="fr_FR")
    subject = factory.LazyFunction(
        lambda: random.choice(["Mathematiques", "Francais", "Histoire", "SVT", "Physique"])
    )
    diploma = factory.LazyFunction(lambda: random.choice(["Bac", "Licence", "Master"]))
    schoolId = ""  # a passer par le test
    status = ValidationStatus.APPROVED


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------
class UserFactory(_AsyncBaseFactory):
    """Cree un User en DB. Par defaut : role TEACHER, mot de passe `Test@Pa55word!`.

    Pour creer rapidement un admin national dans un test :

        admin = await UserFactory.create_async(role=UserRole.NATIONAL_ADMIN)
    """

    class Meta:
        model = User

    id = factory.LazyFunction(generate_cuid)
    email = factory.Sequence(lambda n: f"user-{n:06d}@test.local")
    fullName = factory.Faker("name", locale="fr_FR")
    role = UserRole.TEACHER
    isActive = True
    passwordHash = factory.LazyFunction(lambda: hash_password("Test@Pa55word!"))
    regionId = None
    prefectureId = None
    subPrefectureId = None
    schoolId = None
    # Module 1 columns (default to no-MFA)
    mfaRequired = False
    mfaEnabled = False
    passwordChangedAt = None
    # Module 6 i18n
    preferredLanguage = "fr"


# ---------------------------------------------------------------------------
# Module 1 — Auth hardening factories
# ---------------------------------------------------------------------------
class MfaCredentialFactory(_AsyncBaseFactory):
    class Meta:
        model = MfaCredential

    id = factory.LazyFunction(generate_cuid)
    userId = ""               # required, set by test
    secret = ""               # encrypted TOTP secret (test provides encrypted value)
    enabled = False
    verifiedAt = None
    recoveryCodesHashed: list[str] = []  # type: ignore[assignment]


class PasswordHistoryFactory(_AsyncBaseFactory):
    class Meta:
        model = PasswordHistory

    id = factory.LazyFunction(generate_cuid)
    userId = ""
    passwordHash = factory.LazyFunction(lambda: hash_password("Legacy@Pa55word!"))


class RefreshTokenSessionFactory(_AsyncBaseFactory):
    class Meta:
        model = RefreshTokenSession

    id = factory.LazyFunction(generate_cuid)
    userId = ""
    tokenHash = factory.LazyFunction(
        lambda: hash_token(f"refresh-{generate_cuid()}")
    )
    userAgent = "pytest-ua"
    ipAddress = "127.0.0.1"
    lastUsedAt = None
    expiresAt = factory.LazyFunction(
        lambda: datetime.now(UTC) + timedelta(days=7)
    )
    revokedAt = None
    revokedReason = None


class AuthAuditLogFactory(_AsyncBaseFactory):
    class Meta:
        model = AuthAuditLog

    id = factory.LazyFunction(generate_cuid)
    userId = None
    email = "audit@test.local"
    event = "LOGIN_SUCCESS"
    ipAddress = "127.0.0.1"
    userAgent = "pytest-ua"
    success = True


class PasswordResetTokenFactory(_AsyncBaseFactory):
    class Meta:
        model = PasswordResetToken

    id = factory.LazyFunction(generate_cuid)
    userId = ""
    tokenHash = factory.LazyFunction(
        lambda: hash_token(f"reset-{generate_cuid()}")
    )
    expiresAt = factory.LazyFunction(
        lambda: datetime.now(UTC) + timedelta(minutes=30)
    )
    usedAt = None
    ipAddress = "127.0.0.1"


# ---------------------------------------------------------------------------
# Module 2 — helpers de doublons
# ---------------------------------------------------------------------------
async def make_duplicate_pair(
    school_id: str,
    *,
    last_name: str = "Diallo",
    first_name: str = "Aïssatou",
    birth_iso: str = "2018-03-15",
    phone: str = "+224622123456",
) -> tuple[Student, Student]:
    """Crée deux students presque identiques (orthographe légèrement différente).

    Utilise les normalisations attendues du module census : on stocke
    directement les valeurs déjà normalisées pour rester proche du runtime.
    Renvoie ``(original, near_duplicate)`` ; les deux pointent sur
    ``school_id``. Utile dans les tests qui veulent un score HIGH garanti.
    """
    birth = datetime.fromisoformat(birth_iso).replace(tzinfo=UTC)
    a = await StudentFactory.create_async(
        schoolId=school_id,
        firstName=first_name,
        lastName=last_name,
        gender=Gender.FEMALE,
        guardianPhone=phone,
        birthDate=birth,
    )
    # Variante : capitalisation différente, accent retiré.
    b = await StudentFactory.create_async(
        schoolId=school_id,
        firstName=first_name.upper().replace("Ï", "I"),
        lastName=last_name.upper(),
        gender=Gender.FEMALE,
        guardianPhone=phone,
        birthDate=birth,
    )
    return a, b


# ---------------------------------------------------------------------------
# High-level helpers — souvent on veut juste un "tree" complet
# (region -> prefecture -> sous-prefecture -> ecole).
# ---------------------------------------------------------------------------
async def make_territorial_tree() -> dict[str, Any]:
    """Cree Region -> Prefecture -> SubPrefecture -> School coherents.

    Renvoie un dict avec les 4 instances pour pouvoir les referencer dans
    le test.
    """
    region = await RegionFactory.create_async()
    prefecture = await PrefectureFactory.create_async(regionId=region.id)
    sub = await SubPrefectureFactory.create_async(
        regionId=region.id, prefectureId=prefecture.id
    )
    school = await SchoolFactory.create_async(
        regionId=region.id,
        prefectureId=prefecture.id,
        subPrefectureId=sub.id,
    )
    return {
        "region": region,
        "prefecture": prefecture,
        "subPrefecture": sub,
        "school": school,
    }
