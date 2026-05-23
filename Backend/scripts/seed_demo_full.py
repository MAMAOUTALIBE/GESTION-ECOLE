"""Seed démonstration complet — GESTION-EE.

Usage :  uv run python Backend/scripts/seed_demo_full.py

Idempotent : commence par supprimer toutes les données dont les identifiants
sont préfixés `DEMO-` / `DM-` ou dont l'email finit par `@demo.gn`. Ne touche
JAMAIS aux comptes/écoles réels.

Couvre 15 blocs fonctionnels :
    1.  Territoire (4 régions × 3 préfectures × 2 sous-préfectures)
    2.  Utilisateurs (1 par rôle, 8 comptes)
    3.  Écoles & classes (60 écoles, 4-6 classes/école)
    4.  Enseignants (avec mix ratio normal/tendu/critique)
    5.  Parents
    6.  Élèves
    7.  Année scolaire 2025-2026, périodes, matières
    8.  Notes & bulletins (T1 validé, T2 partiel, T3 vierge)
    9.  Présences sur 30 jours ouvrables
    10. Workflow (10 SUBMITTED + 5 APPROVED + 3 REJECTED)
    11. Notifications (20 non lues pour l'admin national)
    12. Communications parents (15 SMS + 5 emails + 3 échecs)
    13. Bibliothèque (inventaire + prêts actifs)
    14. Inspections (8 dont 2 critiques)
    15. Audit logs (50 entrées)
"""
from __future__ import annotations

import asyncio
import random
import secrets
import sys
import time
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Permet de lancer le script directement (uv run python Backend/scripts/seed_demo_full.py)
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import dispose_engine, get_engine
from app.core.security import hash_password
from app.modules.academics.models import (
    AcademicPeriod,
    Assessment,
    Grade,
    Parent,
    ParentCommunication,
    ReportCard,
    SchoolYear,
    StudentParent,
    Subject,
)
from app.modules.attendance.models import AttendanceRecord, QrCredential
from app.modules.auth.models import User
from app.modules.census.models import Student, StudentTransfer, Teacher
from app.modules.finance.models import Budget, Expense
from app.modules.schoollife.models import (
    BusRoute,
    HealthVisit,
    Incident,
    MealService,
    TimetableSlot,
)
from app.modules.inspections.models import (
    Inspection,
    InspectionActionItem,
    InspectionFinding,
)
from app.modules.library.models import LibraryInventory, LibraryLoan
from app.modules.schools.models import ClassRoom, School, class_room_teacher_table
from app.modules.territory.models import Prefecture, Region, SubPrefecture
from app.modules.workflow.models import AuditLog, Notification, ValidationRequest
from app.shared.enums import (
    AcademicPeriodType,
    AcademicValidationStatus,
    AssessmentType,
    AttendanceStatus,
    BudgetCategory,
    BudgetStatus,
    BuildingCondition,
    CommunicationChannel,
    CommunicationStatus,
    DayOfWeek,
    ElectricitySource,
    ExpenseStatus,
    FindingSeverity,
    Gender,
    HealthVisitStatus,
    HealthVisitType,
    IncidentSanction,
    IncidentSeverity,
    IncidentType,
    InspectionCriterion,
    InspectionStatus,
    LibraryLoanStatus,
    LibraryStockStatus,
    MealServiceType,
    NotificationType,
    ParentRelationType,
    PersonType,
    SchoolAffiliation,
    TransportRouteStatus,
    UserRole,
    ValidationEntityType,
    ValidationStatus,
    WaterSource,
)

# Reproducibility — mêmes données à chaque exécution
random.seed(20260505)

# ---------------------------------------------------------------------------
# Constantes territoriales
# ---------------------------------------------------------------------------
DEMO_PASSWORD = "Demo@2026"  # noqa: S105 — mot de passe de démo, valeur publique
DEMO_USERS = [
    ("admin.national@demo.gn",   "Aïssatou", "Diallo",   UserRole.NATIONAL_ADMIN),
    ("admin.regional@demo.gn",   "Mamadou",  "Camara",   UserRole.REGIONAL_ADMIN),
    ("inspecteur@demo.gn",       "Ibrahima", "Bah",      UserRole.INSPECTOR),
    ("prefet@demo.gn",           "Fanta",    "Sow",      UserRole.PREFECTURE_ADMIN),
    ("sous.prefet@demo.gn",      "Ousmane",  "Touré",    UserRole.SUB_PREFECTURE_ADMIN),
    ("directeur@demo.gn",        "Kadiatou", "Barry",    UserRole.SCHOOL_DIRECTOR),
    ("enseignant@demo.gn",       "Sékou",    "Condé",    UserRole.TEACHER),
    ("agent@demo.gn",            "Hadja",    "Soumah",   UserRole.CENSUS_AGENT),
]

# 4 régions naturelles de Guinée. Coordonnées = chef-lieu / centroïde régional.
REGIONS_DATA = [
    {
        "code": "DEMO-RG-MARITIME",
        "name": "Guinée maritime",
        "lat": 9.6,
        "lng": -13.5,
        "prefectures": [
            ("DEMO-PR-CONAKRY",  "Conakry",  9.5092,  -13.7122,
                [("DEMO-SP-CON-KAL", "Kaloum",  9.5121, -13.7128),
                 ("DEMO-SP-CON-MAT", "Matoto",  9.5736, -13.6324)]),
            ("DEMO-PR-DUBREKA",  "Dubréka",  9.7900,  -13.5167,
                [("DEMO-SP-DUB-CEN", "Centre",  9.7930, -13.5152),
                 ("DEMO-SP-DUB-FAL", "Falessadé", 9.8920, -13.4571)]),
            ("DEMO-PR-KINDIA",   "Kindia",   10.0560, -12.8650,
                [("DEMO-SP-KIN-CEN", "Centre",  10.0552, -12.8650),
                 ("DEMO-SP-KIN-MAD", "Madina",  10.1220, -12.9008)]),
        ],
    },
    {
        "code": "DEMO-RG-MOYENNE",
        "name": "Moyenne-Guinée",
        "lat": 11.3,
        "lng": -12.3,
        "prefectures": [
            ("DEMO-PR-LABE",     "Labé",     11.3167, -12.2833,
                [("DEMO-SP-LAB-CEN", "Centre",  11.3170, -12.2823),
                 ("DEMO-SP-LAB-DAR", "Daralabé", 11.4170, -12.3700)]),
            ("DEMO-PR-MAMOU",    "Mamou",    10.3754, -12.0911,
                [("DEMO-SP-MAM-CEN", "Centre",  10.3754, -12.0911),
                 ("DEMO-SP-MAM-OUR", "Ouré-Kaba", 10.6800, -11.9000)]),
            ("DEMO-PR-PITA",     "Pita",     11.0786, -12.3997,
                [("DEMO-SP-PIT-CEN", "Centre",  11.0786, -12.3997),
                 ("DEMO-SP-PIT-DON", "Donghol-Touma", 11.0500, -12.3000)]),
        ],
    },
    {
        "code": "DEMO-RG-HAUTE",
        "name": "Haute-Guinée",
        "lat": 10.4,
        "lng": -9.3,
        "prefectures": [
            ("DEMO-PR-KANKAN",   "Kankan",   10.3853, -9.3050,
                [("DEMO-SP-KAN-CEN", "Centre",  10.3850, -9.3050),
                 ("DEMO-SP-KAN-BAT", "Bate-Nafadji", 10.5500, -9.2800)]),
            ("DEMO-PR-SIGUIRI",  "Siguiri",  11.4144, -9.1689,
                [("DEMO-SP-SIG-CEN", "Centre",  11.4144, -9.1689),
                 ("DEMO-SP-SIG-DOK", "Doko",    11.7800, -9.1300)]),
            ("DEMO-PR-DABOLA",   "Dabola",   10.7500, -11.1167,
                [("DEMO-SP-DAB-CEN", "Centre",  10.7500, -11.1167),
                 ("DEMO-SP-DAB-DOG", "Dogomet", 10.5200, -10.7600)]),
        ],
    },
    {
        "code": "DEMO-RG-FORESTIERE",
        "name": "Guinée forestière",
        "lat": 8.0,
        "lng": -9.0,
        "prefectures": [
            ("DEMO-PR-NZEREKORE", "Nzérékoré", 7.7561,  -8.8276,
                [("DEMO-SP-NZE-CEN", "Centre",  7.7560, -8.8270),
                 ("DEMO-SP-NZE-GOU", "Gouéké",  7.9000, -8.5800)]),
            ("DEMO-PR-GUECKEDOU", "Guéckédou", 8.5667,  -10.1500,
                [("DEMO-SP-GUE-CEN", "Centre",  8.5670, -10.1500),
                 ("DEMO-SP-GUE-FAN", "Fangamadou", 8.4500, -10.5500)]),
            ("DEMO-PR-MACENTA",  "Macenta",   8.5400,  -9.4700,
                [("DEMO-SP-MAC-CEN", "Centre",  8.5400, -9.4700),
                 ("DEMO-SP-MAC-DAR", "Daro",    8.6500, -9.6000)]),
        ],
    },
]

GUINEAN_FIRST_NAMES_M = [
    "Mamadou", "Ibrahima", "Sékou", "Ousmane", "Mohamed", "Alpha", "Boubacar",
    "Souleymane", "Abdoulaye", "Lamine", "Mory", "Sory", "Karamoko", "Aboubacar",
    "Cheikh", "El Hadj", "Tidiane", "Koly",
]
GUINEAN_FIRST_NAMES_F = [
    "Aïssatou", "Fatoumata", "Kadiatou", "Mariama", "Hadja", "Adama", "Fanta",
    "Aminata", "Djenab", "Hawa", "Saran", "Salimatou", "Néné", "Binta", "Asmaou",
    "Fatima", "Oumou", "Ramatoulaye",
]
GUINEAN_LAST_NAMES = [
    "Diallo", "Bah", "Barry", "Sow", "Camara", "Touré", "Condé", "Sylla", "Soumah",
    "Bangoura", "Keita", "Diakité", "Cissé", "Kaba", "Camara", "Doumbouya",
    "Konaté", "Traoré", "Sako", "Béavogui",
]

SCHOOL_TYPES = ["École primaire", "Collège", "Lycée", "École franco-arabe", "Groupe scolaire"]
AFFILIATIONS = list(SchoolAffiliation)

LEVELS_PRIMARY = ["CP1", "CP2", "CE1", "CE2", "CM1", "CM2"]

SUBJECTS_BASE = [
    ("DEMO-SUB-FRA", "Français",          2.0),
    ("DEMO-SUB-MAT", "Mathématiques",     2.0),
    ("DEMO-SUB-SCI", "Sciences",          1.5),
    ("DEMO-SUB-HG",  "Histoire-Géographie", 1.0),
    ("DEMO-SUB-EPS", "EPS",               0.5),
    ("DEMO-SUB-ARA", "Arabe",             1.0),  # affecté seulement aux écoles ISLAMIC/QURANIC/FRANCO_ARABIC
]

PROFESSIONS_PARENT = [
    "Commerçant·e", "Cultivateur·trice", "Artisan·e", "Couturier·ère",
    "Fonctionnaire", "Mécanicien·ne", "Enseignant·e du primaire", "Sans emploi",
    "Chauffeur·euse", "Pêcheur·euse",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(UTC)


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _random_in_box(lat_c: float, lng_c: float, radius_deg: float = 0.18) -> tuple[float, float]:
    """Tire un point GPS aléatoire autour d'un centroïde (radius en degrés)."""
    return (
        round(lat_c + random.uniform(-radius_deg, radius_deg), 5),
        round(lng_c + random.uniform(-radius_deg, radius_deg), 5),
    )


def _random_phone() -> str:
    """Téléphone format guinéen unique pour les parents (bloc démo).

    Préfixe `+224669` réservé aux comptes démo pour éviter les collisions
    avec d'éventuelles données réelles.
    """
    return f"+22466{random.randint(9000000, 9999999):07d}"


def _gender_for_name(first: str) -> Gender:
    return Gender.FEMALE if first in GUINEAN_FIRST_NAMES_F else Gender.MALE


def _random_birthdate(level: str | None) -> datetime:
    """Date de naissance plausible pour un niveau primaire."""
    age_by_level = {
        "CP1": 6, "CP2": 7, "CE1": 8, "CE2": 9, "CM1": 10, "CM2": 11,
    }
    age = age_by_level.get(level or "CP2", 8)
    age += random.randint(-1, 2)  # variabilité (redoublants)
    today = datetime.now(UTC).date()
    days = random.randint(0, 364)
    bd = today.replace(year=today.year - age) - timedelta(days=days)
    return datetime(bd.year, bd.month, bd.day, tzinfo=UTC)


def _hash_pwd_cached(plain: str, _cache: dict[str, str] = {}) -> str:  # noqa: B006
    """Cache argon2 pour ne pas re-hasher 8× le même mot de passe."""
    if plain not in _cache:
        _cache[plain] = hash_password(plain)
    return _cache[plain]


# ---------------------------------------------------------------------------
# Affichage (barre de progression simple sans dépendance)
# ---------------------------------------------------------------------------
class BlockProgress:
    """Mini barre de progression par bloc, sans dépendance externe."""

    def __init__(self, num: int, name: str) -> None:
        self.num = num
        self.name = name
        self.start = time.perf_counter()
        self.error: str | None = None
        self.counts: dict[str, int] = {}

    def __enter__(self) -> "BlockProgress":
        sys.stdout.write(f"\n[{self.num:>2}/15] {self.name} ... ")
        sys.stdout.flush()
        return self

    def step(self, label: str, n: int) -> None:
        self.counts[label] = self.counts.get(label, 0) + n

    def __exit__(self, exc_type, exc, tb) -> bool:
        elapsed = time.perf_counter() - self.start
        if exc:
            self.error = f"{exc_type.__name__}: {exc}"
            sys.stdout.write(f"❌ {self.error} ({elapsed:.1f}s)\n")
            traceback.print_exception(exc_type, exc, tb)
            return True  # swallow — autres blocs peuvent continuer
        details = ", ".join(f"{n} {k}" for k, n in self.counts.items())
        sys.stdout.write(f"✅ {details or 'OK'} ({elapsed:.1f}s)\n")
        return False


REPORT_ROWS: list[BlockProgress] = []


# ===========================================================================
# CLEANUP : repart à zéro pour les données démo
# ===========================================================================
async def cleanup_demo_data(session: AsyncSession) -> None:
    """Supprime tout ce qui porte un préfixe DEMO-/DM- ou un email @demo.gn.

    Ordre = inverse des FK pour respecter l'intégrité référentielle.
    """
    print("\n🧹 Cleanup données démo précédentes ...")

    # 0bis. Vie scolaire (Phase 13) — référencent écoles, élèves, classes, users
    await session.execute(delete(TimetableSlot).where(
        TimetableSlot.classRoomId.in_(
            select(ClassRoom.id).where(
                ClassRoom.schoolId.in_(
                    select(School.id).where(School.code.like("DEMO-%"))
                )
            )
        )
    ))
    for model in (Incident, HealthVisit, BusRoute, MealService):
        await session.execute(delete(model).where(
            model.schoolId.in_(
                select(School.id).where(School.code.like("DEMO-%"))
            )
        ))

    # 0. Finance — toujours nettoyé en premier puisque Budget/Expense référencent
    #    écoles, régions, préfectures et users.
    await session.execute(delete(Expense).where(
        Expense.description.like("DEMO ·%")
    ))
    await session.execute(delete(Budget).where(
        Budget.notes.like("DEMO ·%")
    ))

    # 1. Récupère les ids des entités démo (pour les filtres en cascade)
    demo_school_ids = (await session.execute(
        select(School.id).where(School.code.like("DEMO-%"))
    )).scalars().all()
    demo_user_ids = (await session.execute(
        select(User.id).where(User.email.like("%@demo.gn"))
    )).scalars().all()
    demo_student_ids = (await session.execute(
        select(Student.id).where(Student.uniqueCode.like("DM-S-%"))
    )).scalars().all()
    demo_teacher_ids = (await session.execute(
        select(Teacher.id).where(Teacher.uniqueCode.like("DM-T-%"))
    )).scalars().all()
    demo_parent_ids = (await session.execute(
        select(Parent.id).where(Parent.phone.like("+224669%"))
    )).scalars().all()
    demo_school_year_ids = (await session.execute(
        select(SchoolYear.id).where(SchoolYear.name.like("DEMO-%"))
    )).scalars().all()
    demo_subject_ids = (await session.execute(
        select(Subject.id).where(Subject.code.like("DEMO-%"))
    )).scalars().all()

    # 2. Cascade dépendances → racines
    if demo_school_ids:
        # Grades / Assessments / ReportCards
        await session.execute(delete(Grade).where(Grade.classRoomId.in_(
            select(ClassRoom.id).where(ClassRoom.schoolId.in_(demo_school_ids))
        )))
        await session.execute(delete(ReportCard).where(ReportCard.classRoomId.in_(
            select(ClassRoom.id).where(ClassRoom.schoolId.in_(demo_school_ids))
        )))
        await session.execute(delete(Assessment).where(Assessment.classRoomId.in_(
            select(ClassRoom.id).where(ClassRoom.schoolId.in_(demo_school_ids))
        )))
        # Attendance
        await session.execute(delete(AttendanceRecord).where(
            AttendanceRecord.schoolId.in_(demo_school_ids)
        ))
        # Library
        await session.execute(delete(LibraryLoan).where(LibraryLoan.inventoryId.in_(
            select(LibraryInventory.id).where(LibraryInventory.schoolId.in_(demo_school_ids))
        )))
        await session.execute(delete(LibraryInventory).where(
            LibraryInventory.schoolId.in_(demo_school_ids)
        ))
        # Inspections
        action_ids = (await session.execute(
            select(InspectionActionItem.id).where(InspectionActionItem.inspectionId.in_(
                select(Inspection.id).where(Inspection.schoolId.in_(demo_school_ids))
            ))
        )).scalars().all()
        if action_ids:
            await session.execute(delete(InspectionActionItem).where(
                InspectionActionItem.id.in_(action_ids)
            ))
        await session.execute(delete(InspectionFinding).where(InspectionFinding.inspectionId.in_(
            select(Inspection.id).where(Inspection.schoolId.in_(demo_school_ids))
        )))
        await session.execute(delete(Inspection).where(Inspection.schoolId.in_(demo_school_ids)))
        # Transferts
        await session.execute(delete(StudentTransfer).where(
            StudentTransfer.fromSchoolId.in_(demo_school_ids)
        ))
        await session.execute(delete(StudentTransfer).where(
            StudentTransfer.toSchoolId.in_(demo_school_ids)
        ))

    # 3. Communications, parent links, qr, students/teachers
    if demo_parent_ids:
        await session.execute(delete(ParentCommunication).where(
            ParentCommunication.parentId.in_(demo_parent_ids)
        ))
    if demo_student_ids:
        await session.execute(delete(StudentParent).where(
            StudentParent.studentId.in_(demo_student_ids)
        ))
        await session.execute(delete(QrCredential).where(
            QrCredential.studentId.in_(demo_student_ids)
        ))
        await session.execute(delete(Student).where(Student.id.in_(demo_student_ids)))
    if demo_teacher_ids:
        await session.execute(delete(QrCredential).where(
            QrCredential.teacherId.in_(demo_teacher_ids)
        ))
        # _ClassRoomTeacher (table M2M)
        await session.execute(delete(class_room_teacher_table).where(
            class_room_teacher_table.c.B.in_(demo_teacher_ids)
        ))
        await session.execute(delete(Teacher).where(Teacher.id.in_(demo_teacher_ids)))
    if demo_parent_ids:
        await session.execute(delete(Parent).where(Parent.id.in_(demo_parent_ids)))

    # 4. Classes & écoles
    if demo_school_ids:
        await session.execute(delete(ClassRoom).where(
            ClassRoom.schoolId.in_(demo_school_ids)
        ))
        await session.execute(delete(School).where(School.id.in_(demo_school_ids)))

    # 5. Académique partagé
    if demo_school_year_ids:
        await session.execute(delete(AcademicPeriod).where(
            AcademicPeriod.schoolYearId.in_(demo_school_year_ids)
        ))
        await session.execute(delete(SchoolYear).where(SchoolYear.id.in_(demo_school_year_ids)))
    if demo_subject_ids:
        await session.execute(delete(Subject).where(Subject.id.in_(demo_subject_ids)))

    # 6. Workflow / notifs / logs liés aux users démo (avant suppression Users)
    if demo_user_ids:
        await session.execute(delete(ValidationRequest).where(
            ValidationRequest.requestedById.in_(demo_user_ids)
        ))
        await session.execute(delete(ValidationRequest).where(
            ValidationRequest.reviewerUserId.in_(demo_user_ids)
        ))
        await session.execute(delete(Notification).where(
            Notification.recipientUserId.in_(demo_user_ids)
        ))
        await session.execute(delete(Notification).where(
            Notification.senderUserId.in_(demo_user_ids)
        ))
        await session.execute(delete(AuditLog).where(AuditLog.actorId.in_(demo_user_ids)))

    # 7. Users AVANT le territoire (User.regionId/prefectureId/subPrefectureId
    #    sont des FK qui empêchent sinon la suppression des territoires).
    if demo_user_ids:
        await session.execute(delete(User).where(User.id.in_(demo_user_ids)))

    # 8. Territoire (sub-pref → pref → region)
    await session.execute(delete(SubPrefecture).where(SubPrefecture.code.like("DEMO-%")))
    await session.execute(delete(Prefecture).where(Prefecture.code.like("DEMO-%")))
    await session.execute(delete(Region).where(Region.code.like("DEMO-%")))

    await session.commit()
    print("   ✅ cleanup terminé\n")


# ===========================================================================
# BLOC 1 — Territoire
# ===========================================================================
async def seed_block_1_territory(session: AsyncSession, ctx: dict) -> BlockProgress:
    bp = BlockProgress(1, "Territoire (régions / préfectures / sous-préfectures)")
    with bp:
        regions: dict[str, Region] = {}
        prefectures: dict[str, Prefecture] = {}
        sub_prefectures: dict[str, SubPrefecture] = {}

        for r in REGIONS_DATA:
            region = Region(name=r["name"], code=r["code"])
            session.add(region)
            await session.flush()
            regions[r["code"]] = region
            for pf_code, pf_name, _lat, _lng, sps in r["prefectures"]:
                pref = Prefecture(
                    name=pf_name, code=pf_code, regionId=region.id,
                    status=ValidationStatus.APPROVED,
                )
                session.add(pref)
                await session.flush()
                prefectures[pf_code] = pref
                for sp_code, sp_name, _slat, _slng in sps:
                    sp = SubPrefecture(
                        name=sp_name, code=sp_code,
                        regionId=region.id, prefectureId=pref.id,
                        status=ValidationStatus.APPROVED,
                    )
                    session.add(sp)
                    await session.flush()
                    sub_prefectures[sp_code] = sp

        await session.commit()
        ctx["regions"] = regions
        ctx["prefectures"] = prefectures
        ctx["sub_prefectures"] = sub_prefectures
        bp.step("régions", len(regions))
        bp.step("préfectures", len(prefectures))
        bp.step("sous-préfectures", len(sub_prefectures))
    return bp


# ===========================================================================
# BLOC 2 — Utilisateurs
# ===========================================================================
async def seed_block_2_users(session: AsyncSession, ctx: dict) -> BlockProgress:
    bp = BlockProgress(2, "Utilisateurs de démo (1 par rôle)")
    with bp:
        regions = ctx["regions"]
        prefectures = ctx["prefectures"]
        sub_prefectures = ctx["sub_prefectures"]

        # Pré-allocations de territoire
        region_maritime = regions["DEMO-RG-MARITIME"]
        prefecture_conakry = prefectures["DEMO-PR-CONAKRY"]
        sub_kaloum = sub_prefectures["DEMO-SP-CON-KAL"]

        users: dict[UserRole, User] = {}
        password_hash = _hash_pwd_cached(DEMO_PASSWORD)

        for email, first, last, role in DEMO_USERS:
            user = User(
                email=email,
                passwordHash=password_hash,
                fullName=f"{first} {last}",
                role=role,
                isActive=True,
            )
            if role == UserRole.REGIONAL_ADMIN:
                user.regionId = region_maritime.id
            elif role == UserRole.PREFECTURE_ADMIN:
                user.regionId = region_maritime.id
                user.prefectureId = prefecture_conakry.id
            elif role == UserRole.SUB_PREFECTURE_ADMIN:
                user.regionId = region_maritime.id
                user.prefectureId = prefecture_conakry.id
                user.subPrefectureId = sub_kaloum.id
            elif role == UserRole.INSPECTOR:
                user.regionId = region_maritime.id
            session.add(user)
            await session.flush()
            users[role] = user

        await session.commit()
        ctx["users"] = users
        bp.step("comptes", len(users))
    return bp


# ===========================================================================
# BLOC 3 — Écoles & classes
# ===========================================================================
async def seed_block_3_schools_classes(session: AsyncSession, ctx: dict) -> BlockProgress:
    bp = BlockProgress(3, "Écoles + classes")
    with bp:
        regions = ctx["regions"]
        prefectures = ctx["prefectures"]
        sub_prefectures = ctx["sub_prefectures"]

        schools: list[School] = []
        all_classes: list[ClassRoom] = []

        # On suit la liste REGIONS_DATA pour réutiliser les coords centroïdes
        for r_data in REGIONS_DATA:
            region = regions[r_data["code"]]
            for pf_code, pf_name, lat_c, lng_c, sps in r_data["prefectures"]:
                pref = prefectures[pf_code]
                # Les sous-préfs accessibles
                sub_codes = [sp[0] for sp in sps]
                for i in range(5):  # 5 écoles / préfecture
                    affiliation = random.choices(
                        AFFILIATIONS,
                        weights=[55, 15, 8, 5, 10, 3, 4],  # PUBLIC dominant
                        k=1,
                    )[0]
                    type_label = random.choice(SCHOOL_TYPES)
                    name = f"{type_label} de {pf_name} #{i + 1}"
                    code = f"DEMO-EC-{pf_code.split('-')[-1]}-{i + 1:02d}"
                    lat, lng = _random_in_box(lat_c, lng_c, radius_deg=0.20)

                    chosen_sp = sub_prefectures[random.choice(sub_codes)]
                    school = School(
                        name=name,
                        code=code,
                        regionId=region.id,
                        prefectureId=pref.id,
                        subPrefectureId=chosen_sp.id,
                        prefecture=pf_name,
                        commune=chosen_sp.name,
                        type=type_label,
                        phone=f"+22462{random.randint(1000000, 9999999):07d}",
                        latitude=lat,
                        longitude=lng,
                        status=ValidationStatus.APPROVED,
                        affiliation=affiliation,
                        waterSource=random.choice(list(WaterSource)),
                        electricitySource=random.choice(list(ElectricitySource)),
                        internetAvailable=(random.random() < 0.20),
                        toiletsBoys=random.choice([0, 1, 2, 3, 4, 6]),
                        toiletsGirls=random.choice([0, 0, 1, 2, 3, 5]),  # 0 plus fréquent (alerte)
                        toiletsAccessible=(random.random() < 0.25),
                        classroomsTotal=random.randint(4, 16),
                        classroomsUsable=None,  # ajusté dessous
                        buildingCondition=random.choice(list(BuildingCondition)),
                        buildingYear=random.randint(1965, 2024),
                        multiShift=(random.random() < 0.25),
                        distanceToHealthCenterKm=round(random.uniform(0.2, 18.0), 1),
                    )
                    school.classroomsUsable = max(
                        2, school.classroomsTotal - random.randint(0, 3)
                    )
                    session.add(school)
                    schools.append(school)

        await session.flush()  # IDs disponibles

        # 3-6 classes par école
        for school in schools:
            n_classes = random.randint(3, 6)
            chosen_levels = random.sample(LEVELS_PRIMARY, k=min(n_classes, len(LEVELS_PRIMARY)))
            for idx, level in enumerate(chosen_levels):
                klass = ClassRoom(
                    name=f"{level} - {chr(65 + idx)}",
                    level=level,
                    maxStudents=45,
                    schoolId=school.id,
                    schoolYear="2025-2026",
                )
                session.add(klass)
                all_classes.append(klass)

        await session.flush()
        await session.commit()
        ctx["schools"] = schools
        ctx["classes"] = all_classes
        bp.step("écoles", len(schools))
        bp.step("classes", len(all_classes))
    return bp


# ===========================================================================
# BLOC 4 — Enseignants
# ===========================================================================
async def seed_block_4_teachers(session: AsyncSession, ctx: dict) -> BlockProgress:
    bp = BlockProgress(4, "Enseignants (mix ratio normal / tendu / critique)")
    with bp:
        schools: list[School] = ctx["schools"]
        classes: list[ClassRoom] = ctx["classes"]

        # Distribution voulue : 30% normales, 40% tension, 20% critique, 10% extreme
        # Les ratios sont obtenus par le couple (élèves cible, nb enseignants)
        # En pratique, on choisit le NB d'enseignants par école selon l'objectif.
        # Le nb d'élèves est généré ensuite (BLOC 6) en cohérence.
        bucket_assignments: list[str] = []
        n = len(schools)
        bucket_assignments += ["normal"] * int(n * 0.30)
        bucket_assignments += ["tension"] * int(n * 0.40)
        bucket_assignments += ["critical"] * int(n * 0.20)
        bucket_assignments += ["extreme"] * (n - len(bucket_assignments))
        random.shuffle(bucket_assignments)

        teachers_by_school: dict[str, list[Teacher]] = {}
        teacher_count = 0
        teachers_to_classes: list[tuple[str, str]] = []

        contract_types = ["Titulaire", "Contractuel", "Vacataire"]
        diplomas = ["BTS pédagogique", "Licence", "ENI", "Maîtrise", "Master"]

        teachers: list[Teacher] = []

        for school, bucket in zip(schools, bucket_assignments, strict=True):
            school_classes = [c for c in classes if c.schoolId == school.id]
            n_classes = len(school_classes)
            # Nb élèves attendus (sera la cible du BLOC 6)
            avg_students_per_class = 32
            target_students = avg_students_per_class * n_classes

            if bucket == "normal":           # ratio 25-35
                n_teachers = max(1, round(target_students / random.randint(25, 35)))
            elif bucket == "tension":        # 36-45
                n_teachers = max(1, round(target_students / random.randint(36, 45)))
            elif bucket == "critical":       # >45
                n_teachers = max(1, round(target_students / random.randint(46, 60)))
            else:  # extreme : 0 ou 1 enseignant
                n_teachers = random.choice([0, 1])

            # Cas catastrophe : on accepte 0 enseignants
            for i in range(n_teachers):
                gender = random.choice([Gender.MALE, Gender.FEMALE])
                first = random.choice(
                    GUINEAN_FIRST_NAMES_F if gender == Gender.FEMALE else GUINEAN_FIRST_NAMES_M
                )
                last = random.choice(GUINEAN_LAST_NAMES)
                teacher_count += 1
                tch = Teacher(
                    uniqueCode=f"DM-T-{teacher_count:06d}",
                    firstName=first,
                    lastName=last,
                    birthDate=datetime(
                        random.randint(1965, 2000), random.randint(1, 12),
                        random.randint(1, 28), tzinfo=UTC,
                    ),
                    gender=gender,
                    phone=f"+22462{random.randint(1000000, 9999999):07d}",
                    subject=random.choice([
                        "Français", "Mathématiques", "Sciences",
                        "Histoire-Géographie", "EPS", "Polyvalent",
                    ]),
                    diploma=f"{random.choice(diplomas)} ({random.choice(contract_types)})",
                    schoolId=school.id,
                    status=ValidationStatus.APPROVED,
                )
                session.add(tch)
                teachers.append(tch)
                teachers_by_school.setdefault(school.id, []).append(tch)

        await session.flush()

        # Affecter chaque enseignant à 1-2 classes de son école
        m2m_rows = []
        for school in schools:
            school_classes = [c for c in classes if c.schoolId == school.id]
            for tch in teachers_by_school.get(school.id, []):
                k = random.randint(1, min(2, len(school_classes))) if school_classes else 0
                if k:
                    for klass in random.sample(school_classes, k=k):
                        m2m_rows.append({"A": klass.id, "B": tch.id})

        if m2m_rows:
            await session.execute(class_room_teacher_table.insert().values(m2m_rows))

        # QrCredentials enseignants
        for tch in teachers:
            session.add(QrCredential(
                token=secrets.token_urlsafe(24),
                payload=f"TEACHER:{tch.uniqueCode}",
                personType=PersonType.TEACHER,
                teacherId=tch.id,
            ))

        await session.commit()
        ctx["teachers"] = teachers
        ctx["teachers_by_school"] = teachers_by_school
        ctx["school_buckets"] = dict(zip(schools, bucket_assignments, strict=True))
        bp.step("enseignants", len(teachers))
        bp.step("affectations classes", len(m2m_rows))
    return bp


# ===========================================================================
# BLOC 5 — Parents (créés en parallèle des élèves au bloc 6 pour éviter les
# téléphones gaspillés sur des élèves qui n'auront aucun parent). On fait un
# pool de parents partagés.
# ===========================================================================
async def seed_block_5_parents_pool(session: AsyncSession, ctx: dict) -> BlockProgress:
    bp = BlockProgress(5, "Parents (pool global)")
    with bp:
        # Estimation : 2 parents / élève en moyenne, ~30 élèves / classe.
        n_classes = len(ctx["classes"])
        approx_students = n_classes * 30
        target_parents = int(approx_students * 1.6)  # mutualisation entre fratries

        used_phones: set[str] = set()
        used_emails: set[str] = set()
        parents: list[Parent] = []
        for _ in range(target_parents):
            gender = random.choice([Gender.MALE, Gender.FEMALE])
            first = random.choice(
                GUINEAN_FIRST_NAMES_F if gender == Gender.FEMALE else GUINEAN_FIRST_NAMES_M
            )
            last = random.choice(GUINEAN_LAST_NAMES)
            phone = _random_phone()
            while phone in used_phones:
                phone = _random_phone()
            used_phones.add(phone)

            email: str | None = None
            if random.random() < 0.4:
                stub = f"{first.lower()}.{last.lower()}{random.randint(1, 9999)}@demo.gn"
                if stub not in used_emails:
                    email = stub
                    used_emails.add(stub)

            preferred = random.choices(
                ["fr", "ff", "ma", "su"],
                weights=[60, 25, 10, 5], k=1,
            )[0]
            parents.append(Parent(
                firstName=first, lastName=last, phone=phone, email=email,
                profession=random.choice(PROFESSIONS_PARENT),
                preferredLanguage=preferred,
            ))

        # Insertion par lots
        BATCH = 1000
        for i in range(0, len(parents), BATCH):
            session.add_all(parents[i:i + BATCH])
            await session.flush()

        await session.commit()
        ctx["parents"] = parents
        bp.step("parents", len(parents))
    return bp


# ===========================================================================
# BLOC 6 — Élèves + StudentParent
# ===========================================================================
async def seed_block_6_students(session: AsyncSession, ctx: dict) -> BlockProgress:
    bp = BlockProgress(6, "Élèves + liens parents")
    with bp:
        classes: list[ClassRoom] = ctx["classes"]
        schools: list[School] = ctx["schools"]
        parents: list[Parent] = ctx["parents"]
        school_buckets = ctx["school_buckets"]

        students: list[Student] = []
        student_parents: list[StudentParent] = []
        qr_creds: list[QrCredential] = []
        student_count = 0

        # Lookup : school_id -> bucket
        bucket_by_school = {s.id: school_buckets[s] for s in schools}

        # Préférences canal de comm pour parents → ici on stocke seulement le canal
        # préféré simulé qui sera utilisé par BLOC 12. Pas de champ dans Parent.
        # On en attache un dict en context.
        preferred_channel: dict[str, CommunicationChannel] = {}
        for p in parents:
            preferred_channel[p.id] = random.choices(
                [
                    CommunicationChannel.SMS,
                    CommunicationChannel.WHATSAPP,
                    CommunicationChannel.EMAIL,
                ],
                weights=[60, 30, 10], k=1,
            )[0]
        ctx["preferred_channel"] = preferred_channel

        # Pour les fratries : groupe les parents par "famille" (nom de famille)
        parents_by_lastname: dict[str, list[Parent]] = {}
        for p in parents:
            parents_by_lastname.setdefault(p.lastName, []).append(p)

        relations = [
            ParentRelationType.FATHER,
            ParentRelationType.MOTHER,
            ParentRelationType.LEGAL_GUARDIAN,
            ParentRelationType.OTHER,  # mappe oncle/tante/...
        ]

        for klass in classes:
            bucket = bucket_by_school[klass.schoolId]
            # 25-45 élèves selon l'alerte ; les écoles "extreme" peuvent être bondées
            if bucket == "normal":
                n_students = random.randint(22, 32)
            elif bucket == "tension":
                n_students = random.randint(28, 40)
            elif bucket == "critical":
                n_students = random.randint(34, 45)
            else:
                n_students = random.randint(35, 45)

            for _ in range(n_students):
                gender = random.choice([Gender.MALE, Gender.FEMALE])
                first = random.choice(
                    GUINEAN_FIRST_NAMES_F if gender == Gender.FEMALE else GUINEAN_FIRST_NAMES_M
                )
                last = random.choice(GUINEAN_LAST_NAMES)
                student_count += 1
                bd = _random_birthdate(klass.level)
                stu = Student(
                    uniqueCode=f"DM-S-{student_count:07d}",
                    firstName=first, lastName=last,
                    birthDate=bd, gender=gender,
                    schoolId=klass.schoolId, classRoomId=klass.id,
                )
                students.append(stu)

        # Flush par lots
        BATCH = 2000
        for i in range(0, len(students), BATCH):
            session.add_all(students[i:i + BATCH])
            await session.flush()

        # QR + liens parents (2 parents en moyenne)
        for stu in students:
            qr_creds.append(QrCredential(
                token=secrets.token_urlsafe(24),
                payload=f"STUDENT:{stu.uniqueCode}",
                personType=PersonType.STUDENT,
                studentId=stu.id,
            ))
            # Trouver des parents potentiels (même nom de famille de préférence)
            candidates = parents_by_lastname.get(stu.lastName, [])
            random.shuffle(candidates)
            n_parents = random.choices([1, 2], weights=[20, 80], k=1)[0]
            chosen_parents = candidates[:n_parents]
            # Compléter avec random si pas assez
            while len(chosen_parents) < n_parents:
                chosen_parents.append(random.choice(parents))
            seen_pairs: set[tuple[str, ParentRelationType]] = set()
            for idx, parent in enumerate(chosen_parents):
                rel = relations[idx % len(relations)] if idx > 0 else (
                    ParentRelationType.MOTHER if parent.firstName in GUINEAN_FIRST_NAMES_F
                    else ParentRelationType.FATHER
                )
                key = (parent.id, rel)
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                stu.guardianName = stu.guardianName or f"{parent.firstName} {parent.lastName}"
                stu.guardianPhone = stu.guardianPhone or parent.phone
                student_parents.append(StudentParent(
                    studentId=stu.id, parentId=parent.id,
                    relation=rel,
                    isPrimary=(idx == 0),
                    isEmergencyContact=(idx == 0),
                ))

        for i in range(0, len(qr_creds), BATCH):
            session.add_all(qr_creds[i:i + BATCH])
            await session.flush()
        for i in range(0, len(student_parents), BATCH):
            session.add_all(student_parents[i:i + BATCH])
            await session.flush()

        await session.commit()
        ctx["students"] = students
        ctx["student_parents"] = student_parents
        bp.step("élèves", len(students))
        bp.step("liens parent-élève", len(student_parents))
        bp.step("QR credentials", len(qr_creds))
    return bp


# ===========================================================================
# BLOC 7 — Année scolaire, périodes, matières
# ===========================================================================
async def seed_block_7_academic_setup(session: AsyncSession, ctx: dict) -> BlockProgress:
    bp = BlockProgress(7, "Année scolaire 2025-2026 + matières")
    with bp:
        schools: list[School] = ctx["schools"]
        classes: list[ClassRoom] = ctx["classes"]

        sy = SchoolYear(
            name="DEMO-2025-2026",
            startDate=_utc(2025, 10, 1),
            endDate=_utc(2026, 7, 15),
            periodType=AcademicPeriodType.TRIMESTER,
            isActive=True,
        )
        session.add(sy)
        await session.flush()

        periods = [
            AcademicPeriod(
                name="Trimestre 1", type=AcademicPeriodType.TRIMESTER, order=1,
                startDate=_utc(2025, 10, 1), endDate=_utc(2025, 12, 20),
                schoolYearId=sy.id,
            ),
            AcademicPeriod(
                name="Trimestre 2", type=AcademicPeriodType.TRIMESTER, order=2,
                startDate=_utc(2026, 1, 5), endDate=_utc(2026, 4, 5),
                schoolYearId=sy.id,
            ),
            AcademicPeriod(
                name="Trimestre 3", type=AcademicPeriodType.TRIMESTER, order=3,
                startDate=_utc(2026, 4, 15), endDate=_utc(2026, 7, 15),
                schoolYearId=sy.id,
            ),
        ]
        for p in periods:
            session.add(p)
        await session.flush()

        subjects = [
            Subject(code=code, name=name, level="primary", coefficient=coef)
            for code, name, coef in SUBJECTS_BASE
        ]
        for s in subjects:
            session.add(s)
        await session.flush()

        # Lier les classes à l'année scolaire
        for klass in classes:
            klass.schoolYearId = sy.id
        await session.flush()

        await session.commit()
        ctx["school_year"] = sy
        ctx["periods"] = periods
        ctx["subjects"] = subjects
        ctx["subjects_by_code"] = {s.code: s for s in subjects}
        # Détermine quelles écoles utilisent l'arabe
        islamic_school_ids = {
            s.id for s in schools
            if s.affiliation in (
                SchoolAffiliation.ISLAMIC,
                SchoolAffiliation.QURANIC,
                SchoolAffiliation.FRANCO_ARABIC,
            )
        }
        ctx["islamic_school_ids"] = islamic_school_ids
        bp.step("année scolaire", 1)
        bp.step("périodes", len(periods))
        bp.step("matières", len(subjects))
    return bp


# ===========================================================================
# BLOC 8 — Notes & bulletins
# ===========================================================================
async def seed_block_8_grades_bulletins(session: AsyncSession, ctx: dict) -> BlockProgress:
    bp = BlockProgress(8, "Évaluations / Notes / Bulletins")
    with bp:
        schools: list[School] = ctx["schools"]
        classes: list[ClassRoom] = ctx["classes"]
        students: list[Student] = ctx["students"]
        sy: SchoolYear = ctx["school_year"]
        periods: list[AcademicPeriod] = ctx["periods"]
        subjects: list[Subject] = ctx["subjects"]
        islamic_school_ids: set[str] = ctx["islamic_school_ids"]
        teachers_by_school: dict[str, list[Teacher]] = ctx["teachers_by_school"]

        # Pour chaque (classe, période, matière) : 3 évaluations (devoir / compo / examen)
        # Pour limiter le volume :
        #   - T1 : 3 évals / matière, toutes notées (status=VALIDATED)
        #   - T2 : 1 éval / matière, notée partiellement (status=SUBMITTED)
        #   - T3 : 1 éval / matière sans notes (status=DRAFT)
        eval_specs = [
            (periods[0], 3, AcademicValidationStatus.VALIDATED, 1.0),  # T1
            (periods[1], 1, AcademicValidationStatus.SUBMITTED, 0.85), # T2
            (periods[2], 1, AcademicValidationStatus.DRAFT, 0.0),       # T3 sans note
        ]
        eval_types = [AssessmentType.HOMEWORK, AssessmentType.COMPOSITION, AssessmentType.NATIONAL_EXAM]

        students_by_class: dict[str, list[Student]] = {}
        for stu in students:
            if stu.classRoomId:
                students_by_class.setdefault(stu.classRoomId, []).append(stu)

        assessments: list[Assessment] = []
        grades: list[Grade] = []
        report_cards: list[ReportCard] = []
        graded_count = 0

        for klass in classes:
            class_students = students_by_class.get(klass.id, [])
            if not class_students:
                continue
            school_subjects = [
                s for s in subjects
                if s.code != "DEMO-SUB-ARA" or klass.schoolId in islamic_school_ids
            ]
            class_teachers = teachers_by_school.get(klass.schoolId, [])

            for period, n_evals, eval_status, fill_rate in eval_specs:
                for subject in school_subjects:
                    teacher = random.choice(class_teachers) if class_teachers else None
                    for j in range(n_evals):
                        a = Assessment(
                            title=f"{subject.name} — {eval_types[j].value} — {period.name}",
                            type=eval_types[j],
                            coefficient=subject.coefficient,
                            maxScore=20.0,
                            assessedAt=period.startDate + timedelta(days=15 * (j + 1)),
                            schoolYearId=sy.id,
                            periodId=period.id,
                            subjectId=subject.id,
                            classRoomId=klass.id,
                            teacherId=teacher.id if teacher else None,
                            status=eval_status,
                        )
                        session.add(a)
                        assessments.append(a)

        # On flush pour avoir les IDs des assessments
        await session.flush()

        # Notes : on ne génère que pour T1 (eval_status VALIDATED) et 60% pour T2
        for asmt in assessments:
            if asmt.status == AcademicValidationStatus.DRAFT:
                continue  # T3 : aucune note
            class_students = students_by_class.get(asmt.classRoomId, [])
            for stu in class_students:
                if random.random() < 0.15:  # 15% absents
                    continue
                if asmt.status == AcademicValidationStatus.SUBMITTED and random.random() > 0.6:
                    continue  # T2 partiel
                # Distribution réaliste
                pick = random.random()
                if pick < 0.20:        # excellents
                    score = round(random.uniform(16.0, 19.5), 1)
                elif pick < 0.85:      # moyens
                    score = round(random.uniform(10.0, 14.5), 1)
                else:                  # difficulté
                    score = round(random.uniform(4.0, 9.0), 1)
                grades.append(Grade(
                    assessmentId=asmt.id,
                    studentId=stu.id,
                    schoolYearId=asmt.schoolYearId,
                    periodId=asmt.periodId,
                    subjectId=asmt.subjectId,
                    classRoomId=asmt.classRoomId,
                    score=score,
                    status=asmt.status,
                    recordedAt=asmt.assessedAt or _now(),
                    updatedAt=_now(),
                ))
                graded_count += 1

        BATCH = 5000
        for i in range(0, len(grades), BATCH):
            session.add_all(grades[i:i + BATCH])
            await session.flush()

        # Bulletins : T1 APPROVED, T2 PENDING, T3 DRAFT
        bulletin_status = {
            periods[0].id: AcademicValidationStatus.VALIDATED,
            periods[1].id: AcademicValidationStatus.SUBMITTED,
            periods[2].id: AcademicValidationStatus.DRAFT,
        }
        for klass in classes:
            class_students = students_by_class.get(klass.id, [])
            for period in periods:
                # Calcul moyenne par élève sur cette période (rapide via dict)
                period_grades = [
                    g for g in grades
                    if g.periodId == period.id and g.classRoomId == klass.id
                ]
                grades_by_student: dict[str, list[Grade]] = {}
                for g in period_grades:
                    grades_by_student.setdefault(g.studentId, []).append(g)

                # Trier élèves par moyenne pondérée pour le rang
                averages: dict[str, float] = {}
                for stu in class_students:
                    student_grades = grades_by_student.get(stu.id, [])
                    if student_grades:
                        total = sum(g.score for g in student_grades)
                        averages[stu.id] = round(total / len(student_grades), 2)
                ranked = sorted(averages.items(), key=lambda kv: kv[1], reverse=True)
                rank_map = {sid: idx + 1 for idx, (sid, _) in enumerate(ranked)}

                for stu in class_students:
                    if stu.id not in averages and period.id != periods[2].id:
                        continue  # pas de bulletin si aucune note (sauf T3 vierge)
                    report_cards.append(ReportCard(
                        studentId=stu.id,
                        classRoomId=klass.id,
                        schoolYearId=sy.id,
                        periodId=period.id,
                        average=averages.get(stu.id),
                        rank=rank_map.get(stu.id),
                        totalStudents=len(ranked) or len(class_students),
                        verificationCode=secrets.token_urlsafe(8),
                        status=bulletin_status[period.id],
                        issuedAt=_now() if bulletin_status[period.id] == AcademicValidationStatus.VALIDATED else None,
                    ))

        for i in range(0, len(report_cards), BATCH):
            session.add_all(report_cards[i:i + BATCH])
            await session.flush()

        await session.commit()
        bp.step("évaluations", len(assessments))
        bp.step("notes", graded_count)
        bp.step("bulletins", len(report_cards))
    return bp


# ===========================================================================
# BLOC 9 — Présences (30 jours ouvrables)
# ===========================================================================
async def seed_block_9_attendance(session: AsyncSession, ctx: dict) -> BlockProgress:
    bp = BlockProgress(9, "Présences (30 jours ouvrables)")
    with bp:
        students: list[Student] = ctx["students"]
        schools: list[School] = ctx["schools"]
        school_buckets = ctx["school_buckets"]

        # Détermine taux de présence par école
        rate_by_school: dict[str, float] = {}
        for school in schools:
            bucket = school_buckets[school]
            urban = (school.prefecture or "").lower() in {"conakry", "kindia", "labé"}
            if random.random() < 0.04:  # 2 écoles avec absentéisme critique
                rate = random.uniform(0.40, 0.49)
            elif urban:
                rate = random.uniform(0.88, 0.95)
            else:
                rate = random.uniform(0.65, 0.78)
            # Penalité pour les écoles en alerte critique
            if bucket in ("critical", "extreme"):
                rate -= 0.05
            rate_by_school[school.id] = max(0.30, min(0.97, rate))

        # 30 jours ouvrables glissants — inclus aujourd'hui pour que l'écran
        # /attendance-monitoring (qui tape /api/attendance/today) soit alimenté.
        today = datetime.now(UTC).date()
        days: list[datetime] = []
        d = today
        while len(days) < 30:
            if d.weekday() < 5:  # lun-ven
                days.append(_utc(d.year, d.month, d.day, 7, 30))
            d -= timedelta(days=1)
        days.reverse()

        # Génération + insertion par lots
        BATCH = 5000
        records: list[AttendanceRecord] = []
        total = 0
        for stu in students:
            rate = rate_by_school.get(stu.schoolId, 0.85)
            for ts in days:
                pick = random.random()
                if pick < rate:
                    status = AttendanceStatus.PRESENT
                    minute_offset = random.randint(0, 30)
                elif pick < rate + 0.05:
                    status = AttendanceStatus.LATE
                    minute_offset = random.randint(35, 60)
                else:
                    status = AttendanceStatus.ABSENT
                    minute_offset = 0
                records.append(AttendanceRecord(
                    personType=PersonType.STUDENT,
                    status=status,
                    scannedAt=ts + timedelta(minutes=minute_offset),
                    schoolId=stu.schoolId,
                    studentId=stu.id,
                ))
                if len(records) >= BATCH:
                    session.add_all(records)
                    await session.flush()
                    total += len(records)
                    records = []
        if records:
            session.add_all(records)
            await session.flush()
            total += len(records)

        await session.commit()
        bp.step("scans présence", total)
    return bp


# ===========================================================================
# BLOC 10 — Validation requests
# ===========================================================================
async def seed_block_10_workflow(session: AsyncSession, ctx: dict) -> BlockProgress:
    bp = BlockProgress(10, "Workflow (validation requests)")
    with bp:
        users: dict[UserRole, User] = ctx["users"]
        schools: list[School] = ctx["schools"]
        teachers: list[Teacher] = ctx["teachers"]
        admin = users[UserRole.NATIONAL_ADMIN]
        director = users[UserRole.SCHOOL_DIRECTOR]
        agent = users[UserRole.CENSUS_AGENT]

        items = []
        # 10 SUBMITTED
        for i in range(10):
            kind = random.choice([
                ValidationEntityType.SCHOOL,
                ValidationEntityType.TEACHER,
                ValidationEntityType.PREFECTURE,
            ])
            entity_id = (
                random.choice(schools).id if kind == ValidationEntityType.SCHOOL
                else random.choice(teachers).id if kind == ValidationEntityType.TEACHER
                else next(iter(ctx["prefectures"].values())).id
            )
            items.append(ValidationRequest(
                entityType=kind, entityId=entity_id,
                status=ValidationStatus.SUBMITTED,
                requestedById=agent.id,
                reviewerRole=UserRole.NATIONAL_ADMIN,
                reason="Demande en attente de validation ministérielle.",
            ))
        # 5 APPROVED
        for _ in range(5):
            items.append(ValidationRequest(
                entityType=ValidationEntityType.SCHOOL,
                entityId=random.choice(schools).id,
                status=ValidationStatus.APPROVED,
                requestedById=director.id,
                reviewerRole=UserRole.NATIONAL_ADMIN,
                reviewerUserId=admin.id,
                reviewedAt=_now() - timedelta(days=random.randint(1, 14)),
                reason=None,
            ))
        # 3 REJECTED
        rejection_reasons = [
            "Documents administratifs incomplets — fournir patente + arrêté.",
            "Coordonnées GPS aberrantes — re-géolocaliser sur le terrain.",
            "Doublon avec une école existante du même quartier.",
        ]
        for reason in rejection_reasons:
            items.append(ValidationRequest(
                entityType=ValidationEntityType.SCHOOL,
                entityId=random.choice(schools).id,
                status=ValidationStatus.REJECTED,
                requestedById=director.id,
                reviewerRole=UserRole.NATIONAL_ADMIN,
                reviewerUserId=admin.id,
                reviewedAt=_now() - timedelta(days=random.randint(2, 20)),
                reason=reason,
            ))

        session.add_all(items)
        await session.commit()
        bp.step("demandes", len(items))
    return bp


# ===========================================================================
# BLOC 11 — Notifications
# ===========================================================================
async def seed_block_11_notifications(session: AsyncSession, ctx: dict) -> BlockProgress:
    bp = BlockProgress(11, "Notifications (admin national)")
    with bp:
        users: dict[UserRole, User] = ctx["users"]
        admin = users[UserRole.NATIONAL_ADMIN]
        # Mapping : ATTENDANCE_ALERT -> SYSTEM_ALERT, BULLETIN_READY -> MESSAGE
        types_pool = [
            (NotificationType.VALIDATION_APPROVED,
                "Validation approuvée",
                "Votre demande de validation d'école a été approuvée."),
            (NotificationType.VALIDATION_REJECTED,
                "Validation refusée",
                "La demande a été rejetée — voir motif détaillé."),
            (NotificationType.SYSTEM_ALERT,
                "Alerte d'absentéisme",
                "École Lycée de Conakry : taux de présence < 70% sur 7 jours."),
            (NotificationType.MESSAGE,
                "Bulletin disponible",
                "Le bulletin de Trimestre 1 est prêt à être consulté."),
            (NotificationType.VALIDATION_REQUEST,
                "Nouvelle demande de validation",
                "Un agent a soumis une demande à valider."),
        ]
        notifs = []
        for i in range(20):
            ntype, title, msg = random.choice(types_pool)
            notifs.append(Notification(
                recipientUserId=admin.id,
                title=title, message=msg, type=ntype,
                isRead=False,
                # CreatedAtMixin fournit createdAt automatique mais on le force
                # pour respecter "7 derniers jours"
            ))
        # Mettre des createdAt manuels pour la dispersion (override server_default)
        now = _now()
        for i, notif in enumerate(notifs):
            notif.createdAt = now - timedelta(days=random.randint(0, 6),
                                              hours=random.randint(0, 23))
        session.add_all(notifs)
        await session.commit()
        bp.step("notifications", len(notifs))
    return bp


# ===========================================================================
# BLOC 12 — Communications parents
# ===========================================================================
async def seed_block_12_communications(session: AsyncSession, ctx: dict) -> BlockProgress:
    bp = BlockProgress(12, "Communications parents (SMS/Email)")
    with bp:
        students: list[Student] = ctx["students"]
        student_parents: list[StudentParent] = ctx["student_parents"]
        # Index parent par id pour efficient lookup
        parents_by_student: dict[str, list[str]] = {}
        for sp in student_parents:
            parents_by_student.setdefault(sp.studentId, []).append(sp.parentId)
        students_by_id = {s.id: s for s in students}

        comms = []
        # 15 SMS
        for _ in range(15):
            stu = random.choice(students)
            parent_ids = parents_by_student.get(stu.id, [])
            if not parent_ids:
                continue
            comms.append(ParentCommunication(
                parentId=parent_ids[0], studentId=stu.id,
                channel=CommunicationChannel.SMS,
                status=CommunicationStatus.SENT,
                subject="Absence de votre enfant",
                message=(
                    f"Votre enfant {stu.firstName} a été absent le "
                    f"{(_now() - timedelta(days=random.randint(1, 5))).strftime('%d/%m/%Y')}."
                ),
                sentAt=_now() - timedelta(hours=random.randint(1, 96)),
            ))
        # 5 emails
        for _ in range(5):
            stu = random.choice(students)
            parent_ids = parents_by_student.get(stu.id, [])
            if not parent_ids:
                continue
            comms.append(ParentCommunication(
                parentId=parent_ids[0], studentId=stu.id,
                channel=CommunicationChannel.EMAIL,
                status=CommunicationStatus.SENT,
                subject="Bulletin de Trimestre 1 disponible",
                message=(
                    f"Bonjour, le bulletin de {stu.firstName} pour le T1 est "
                    f"désormais consultable depuis votre espace parent."
                ),
                sentAt=_now() - timedelta(hours=random.randint(1, 72)),
            ))
        # 3 échecs
        for _ in range(3):
            stu = random.choice(students)
            parent_ids = parents_by_student.get(stu.id, [])
            if not parent_ids:
                continue
            comms.append(ParentCommunication(
                parentId=parent_ids[0], studentId=stu.id,
                channel=CommunicationChannel.SMS,
                status=CommunicationStatus.FAILED,
                subject="Convocation du directeur",
                message=(
                    f"Convocation : merci de venir à l'école concernant "
                    f"{stu.firstName} demain à 09h00."
                ),
                sentAt=None,
            ))

        session.add_all(comms)
        await session.commit()
        bp.step("communications", len(comms))
    return bp


# ===========================================================================
# BLOC 13 — Bibliothèque
# ===========================================================================
async def seed_block_13_library(session: AsyncSession, ctx: dict) -> BlockProgress:
    bp = BlockProgress(13, "Bibliothèque (inventaire + prêts)")
    with bp:
        schools: list[School] = ctx["schools"]
        subjects: list[Subject] = ctx["subjects"]
        students: list[Student] = ctx["students"]
        islamic_school_ids: set[str] = ctx["islamic_school_ids"]

        # On veut 8 entrées par école — on prend 8 couples (subject, level) UNIQUES
        # par école pour respecter la contrainte uq (schoolId, subjectId, level, title).
        inventories: list[LibraryInventory] = []
        for school in schools:
            usable_subjects = [
                s for s in subjects
                if s.code != "DEMO-SUB-ARA" or school.id in islamic_school_ids
            ]
            # Génère le produit cartésien et tire 8 couples sans remise
            combos = [(subj, lvl) for subj in usable_subjects for lvl in LEVELS_PRIMARY]
            random.shuffle(combos)
            for subj, level in combos[:8]:
                stock = random.randint(8, 60)
                required = random.randint(20, 80)
                if stock >= required * 0.85:
                    status = LibraryStockStatus.SUFFICIENT
                elif stock >= required * 0.5:
                    status = LibraryStockStatus.WATCH
                else:
                    status = LibraryStockStatus.SHORTAGE
                inventories.append(LibraryInventory(
                    schoolId=school.id,
                    subjectId=subj.id,
                    level=level,
                    title=f"Manuel {subj.name} — {level}",
                    stock=stock,
                    damaged=random.randint(0, max(1, stock // 8)),
                    required=required,
                    lastInventoryAt=_now() - timedelta(days=random.randint(0, 60)),
                    status=status,
                ))

        BATCH = 1000
        for i in range(0, len(inventories), BATCH):
            session.add_all(inventories[i:i + BATCH])
            await session.flush()

        # 20 prêts actifs : certains en retard
        loans = []
        eligible = random.sample(inventories, k=min(50, len(inventories)))
        for k, inv in enumerate(eligible[:20]):
            stu = random.choice(students)
            borrowed = _now() - timedelta(days=random.randint(3, 40))
            due = borrowed + timedelta(days=14)
            late = due < _now()
            loans.append(LibraryLoan(
                inventoryId=inv.id, studentId=stu.id,
                borrowedAt=borrowed, dueAt=due, returnedAt=None,
                status=LibraryLoanStatus.LATE if late else LibraryLoanStatus.BORROWED,
            ))
        session.add_all(loans)
        await session.commit()
        bp.step("entrées inventaire", len(inventories))
        bp.step("prêts actifs", len(loans))
    return bp


# ===========================================================================
# BLOC 14 — Inspections
# ===========================================================================
async def seed_block_14_inspections(session: AsyncSession, ctx: dict) -> BlockProgress:
    bp = BlockProgress(14, "Inspections terrain")
    with bp:
        users: dict[UserRole, User] = ctx["users"]
        schools: list[School] = ctx["schools"]
        inspector = users[UserRole.INSPECTOR]

        chosen_schools = random.sample(schools, k=8)
        inspections: list[Inspection] = []
        for idx, school in enumerate(chosen_schools):
            critical = idx < 2  # 2 inspections critiques
            avg_score = random.uniform(40.0, 60.0) if critical else random.uniform(60.0, 95.0)
            insp = Inspection(
                schoolId=school.id,
                inspectorId=inspector.id,
                scheduledDate=_now() - timedelta(days=random.randint(15, 60)),
                performedDate=_now() - timedelta(days=random.randint(5, 14)),
                status=InspectionStatus.COMPLETED,
                overallScore=round(avg_score, 1),
                notes="Rapport détaillé disponible dans les archives.",
            )
            session.add(insp)
            inspections.append(insp)
        await session.flush()

        # Findings + actions
        all_findings = []
        all_actions = []
        for idx, insp in enumerate(inspections):
            critical = idx < 2
            n_findings = random.randint(4, 8)
            for j in range(n_findings):
                criterion = random.choice(list(InspectionCriterion))
                # Critique : 2 findings hard
                if critical and j < 2:
                    severity = FindingSeverity.CRITICAL
                    score = random.randint(0, 2)
                    comment = random.choice([
                        "Bâtiment en état dangereux — fissures, toiture défaillante",
                        "Aucune toilette filles — risque rétention adolescentes",
                    ])
                else:
                    severity = random.choice(list(FindingSeverity))
                    score = random.randint(2, 5)
                    comment = "Constat mineur observé lors de la visite."
                all_findings.append(InspectionFinding(
                    inspectionId=insp.id,
                    criterion=criterion,
                    score=score,
                    severity=severity,
                    comment=comment,
                ))
            # 2-3 plans d'action par inspection, certains en retard
            n_actions = random.randint(2, 3)
            for k in range(n_actions):
                due = _now() - timedelta(days=random.randint(3, 30)) if (k == 0 and critical) \
                    else _now() + timedelta(days=random.randint(7, 60))
                all_actions.append(InspectionActionItem(
                    inspectionId=insp.id,
                    description=random.choice([
                        "Réhabiliter la toiture avant la saison des pluies.",
                        "Construire un bloc sanitaire dédié aux filles.",
                        "Installer un panneau solaire (autonomie 2 jours minimum).",
                        "Forer un puits dans l'enceinte de l'école.",
                    ]),
                    dueDate=due,
                ))
        session.add_all(all_findings)
        session.add_all(all_actions)
        await session.commit()
        bp.step("inspections", len(inspections))
        bp.step("constats", len(all_findings))
        bp.step("plans d'action", len(all_actions))
    return bp


# ===========================================================================
# BLOC 15 — Audit logs
# ===========================================================================
async def seed_block_15_audit(session: AsyncSession, ctx: dict) -> BlockProgress:
    bp = BlockProgress(15, "Audit logs (50 entrées)")
    with bp:
        users: dict[UserRole, User] = ctx["users"]
        actor_pool = list(users.values())
        actions = [
            "CREATE_STUDENT", "SAVE_GRADES", "RENDER_BULLETIN_PDF",
            "TRANSFER_STUDENT", "CREATE_INSPECTION", "UPDATE_BUDGET",
            "VALIDATE_REPORT_CARD", "CREATE_NOTIFICATION", "EXPORT_CSV",
        ]
        entities = ["Student", "Grade", "ReportCard", "Inspection", "Budget", "School"]

        logs = []
        for i in range(50):
            actor = random.choice(actor_pool)
            log = AuditLog(
                actorId=actor.id,
                action=random.choice(actions),
                entity=random.choice(entities),
                entityId=secrets.token_urlsafe(10),
                metadata_={"source": "demo_seed", "seq": i},
            )
            log.createdAt = _now() - timedelta(
                days=random.randint(0, 29), hours=random.randint(0, 23),
            )
            logs.append(log)
        session.add_all(logs)
        await session.commit()
        bp.step("entrées audit", len(logs))
    return bp


# ===========================================================================
# BLOC 16 — Finance & Budget (Phase 11)
# ===========================================================================
async def seed_block_16_finance(session: AsyncSession, ctx: dict) -> BlockProgress:
    bp = BlockProgress(16, "Finance & Budget (Phase 11)")
    with bp:
        users: dict[UserRole, User] = ctx["users"]
        schools: list[School] = ctx["schools"]
        admin = users[UserRole.NATIONAL_ADMIN]

        # 3 lignes budgétaires par école sur l'exercice 2026, mix de catégories
        # avec consommations variées (équilibré / sous tension / dépassement).
        # `notes` préfixé "DEMO ·" pour permettre le cleanup idempotent.
        ALL_CATEGORIES = [
            BudgetCategory.OPERATIONS,
            BudgetCategory.INFRASTRUCTURE,
            BudgetCategory.MEALS,
            BudgetCategory.TRAINING,
            BudgetCategory.EQUIPMENT,
            BudgetCategory.SALARIES,
        ]

        # Coût annuel cible par catégorie (USD-équivalent en GNF)
        AMOUNT_BY_CATEGORY = {
            BudgetCategory.OPERATIONS: 32_000_000,
            BudgetCategory.INFRASTRUCTURE: 95_000_000,
            BudgetCategory.MEALS: 18_000_000,
            BudgetCategory.TRAINING: 12_000_000,
            BudgetCategory.EQUIPMENT: 22_000_000,
            BudgetCategory.SALARIES: 75_000_000,
        }

        budgets: list[Budget] = []
        for school in schools:
            categories = random.sample(ALL_CATEGORIES, k=3)
            for cat in categories:
                base = AMOUNT_BY_CATEGORY[cat]
                planned = base * random.uniform(0.85, 1.25)
                # Statut : 70% ACTIVE, 15% APPROVED, 10% DRAFT, 5% CLOSED
                status = random.choices(
                    [BudgetStatus.ACTIVE, BudgetStatus.APPROVED,
                     BudgetStatus.DRAFT, BudgetStatus.CLOSED],
                    weights=[70, 15, 10, 5], k=1,
                )[0]
                budgets.append(Budget(
                    fiscalYear=2026,
                    category=cat,
                    status=status,
                    schoolId=school.id,
                    regionId=school.regionId,
                    prefectureId=school.prefectureId,
                    subPrefectureId=school.subPrefectureId,
                    amountPlanned=round(planned, 2),
                    currency="GNF",
                    notes=f"DEMO · Enveloppe {cat.value} 2026 — école {school.code}",
                    createdById=admin.id,
                ))

        BATCH = 500
        for i in range(0, len(budgets), BATCH):
            session.add_all(budgets[i:i + BATCH])
            await session.flush()

        # Dépenses : 3-6 par budget ACTIVE/APPROVED. Distribution :
        #  - 60% des budgets : 30-70% consommé (équilibré)
        #  - 25% des budgets : 75-95% consommé (à surveiller)
        #  - 10% des budgets : 100-115% consommé (dépassement)
        #  - 5% des budgets : 5-25% consommé (sous-utilisé)
        spendable = [b for b in budgets if b.status in
                     (BudgetStatus.ACTIVE, BudgetStatus.APPROVED)]

        expenses: list[Expense] = []
        for budget in spendable:
            scenario = random.choices(
                ["balanced", "watch", "overrun", "underused"],
                weights=[60, 25, 10, 5], k=1,
            )[0]
            if scenario == "balanced":
                target_rate = random.uniform(0.30, 0.70)
            elif scenario == "watch":
                target_rate = random.uniform(0.75, 0.95)
            elif scenario == "overrun":
                target_rate = random.uniform(1.00, 1.15)
            else:
                target_rate = random.uniform(0.05, 0.25)

            n_expenses = random.randint(3, 6)
            target_amount = budget.amountPlanned * target_rate
            # Réparti aléatoirement entre les dépenses
            shares = [random.random() for _ in range(n_expenses)]
            shares_sum = sum(shares)
            descriptors = {
                BudgetCategory.OPERATIONS: ["Achat fournitures", "Facture eau/élec",
                                            "Maintenance courante", "Frais bureautiques"],
                BudgetCategory.INFRASTRUCTURE: ["Réfection toiture", "Construction salle",
                                                "Forage puits", "Clôture périmétrique"],
                BudgetCategory.MEALS: ["Achat riz/PAM", "Logistique cantine",
                                       "Cuisson + bois", "Eau potable repas"],
                BudgetCategory.TRAINING: ["Formation enseignants", "Atelier pédagogique",
                                          "Frais formateurs", "Documentation"],
                BudgetCategory.EQUIPMENT: ["Mobilier classe", "Tableaux + chaises",
                                           "Tablettes pédagogie", "Matériel sport"],
                BudgetCategory.SALARIES: ["Salaire trimestriel enseignants",
                                          "Indemnités directeur", "Cotisation sociale",
                                          "Heures supplémentaires"],
                BudgetCategory.TRANSPORT: ["Bus scolaire", "Carburant véhicule",
                                           "Maintenance flotte", "Sortie pédagogique"],
                BudgetCategory.MISC: ["Imprévu trésorerie", "Évènement scolaire",
                                      "Autres dépenses"],
            }
            for j, share in enumerate(shares):
                amount = round((share / shares_sum) * target_amount, 2)
                if amount <= 0:
                    continue
                # 70% PAID, 20% APPROVED, 10% PENDING (jamais REJECTED dans ce seed)
                est_status = random.choices(
                    [ExpenseStatus.PAID, ExpenseStatus.APPROVED, ExpenseStatus.PENDING],
                    weights=[70, 20, 10], k=1,
                )[0]
                expense_date = (
                    _now() - timedelta(days=random.randint(5, 240))
                ).date()
                expenses.append(Expense(
                    budgetId=budget.id,
                    category=budget.category,
                    amount=amount,
                    currency="GNF",
                    description=(
                        f"DEMO · {random.choice(descriptors[budget.category])}"
                    ),
                    expenseDate=expense_date,
                    status=est_status,
                    schoolId=budget.schoolId,
                    regionId=budget.regionId,
                    prefectureId=budget.prefectureId,
                    subPrefectureId=budget.subPrefectureId,
                    approvedById=admin.id if est_status != ExpenseStatus.PENDING else None,
                    approvedAt=_now() - timedelta(days=random.randint(1, 30))
                        if est_status != ExpenseStatus.PENDING else None,
                    createdById=admin.id,
                ))

        for i in range(0, len(expenses), BATCH):
            session.add_all(expenses[i:i + BATCH])
            await session.flush()

        await session.commit()
        bp.step("budgets", len(budgets))
        bp.step("dépenses", len(expenses))
    return bp


# ===========================================================================
# BLOC 17 — Vie scolaire (Phase 13)
# ===========================================================================
async def seed_block_17_school_life(session: AsyncSession, ctx: dict) -> BlockProgress:
    bp = BlockProgress(17, "Vie scolaire (incidents/santé/transport/cantines/EDT)")
    with bp:
        users: dict[UserRole, User] = ctx["users"]
        schools: list[School] = ctx["schools"]
        students: list[Student] = ctx["students"]
        classes: list[ClassRoom] = ctx["classes"]
        subjects: list[Subject] = ctx["subjects"]
        teachers: list[Teacher] = ctx["teachers"]
        admin = users[UserRole.NATIONAL_ADMIN]

        students_by_school: dict[str, list[Student]] = {}
        for stu in students:
            students_by_school.setdefault(stu.schoolId, []).append(stu)

        teachers_by_school: dict[str, list[Teacher]] = ctx["teachers_by_school"]

        today = datetime.now(UTC).date()

        # ---- Incidents : ~3-8 par école sur les 60 derniers jours -------
        incidents: list[Incident] = []
        incident_types = list(IncidentType)
        sanction_types = list(IncidentSanction)
        for school in schools:
            schl_students = students_by_school.get(school.id, [])
            if not schl_students:
                continue
            n_incidents = random.randint(3, 8)
            for _ in range(n_incidents):
                stu = random.choice(schl_students) if random.random() < 0.85 else None
                inc_type = random.choice(incident_types)
                # Distribution réaliste : majorité LOW, peu de HIGH
                severity = random.choices(
                    [IncidentSeverity.LOW, IncidentSeverity.MEDIUM, IncidentSeverity.HIGH],
                    weights=[60, 30, 10], k=1,
                )[0]
                sanction = random.choice(sanction_types)
                occurred = datetime.combine(
                    today - timedelta(days=random.randint(0, 60)),
                    datetime.min.time(), tzinfo=UTC,
                ) + timedelta(hours=random.randint(8, 16))
                descriptions = {
                    IncidentType.LATENESS: "Retard répété en classe",
                    IncidentType.INSUBORDINATION: "Refus d'obéir au règlement",
                    IncidentType.FIGHTING: "Bagarre dans la cour",
                    IncidentType.ABSENCE: "Absence non justifiée",
                    IncidentType.BULLYING: "Brimades signalées par un parent",
                    IncidentType.PROPERTY_DAMAGE: "Dégradation matériel",
                    IncidentType.OTHER: "Comportement inapproprié",
                }
                incidents.append(Incident(
                    schoolId=school.id,
                    studentId=stu.id if stu else None,
                    type=inc_type,
                    severity=severity,
                    description=descriptions.get(inc_type, "Incident divers"),
                    sanction=sanction,
                    occurredAt=occurred,
                    recordedById=admin.id,
                ))

        # ---- Health visits : ~5-12 par école sur 30 jours --------------
        health_visits: list[HealthVisit] = []
        hv_types = list(HealthVisitType)
        nurses = ["Mme Diallo", "M. Camara", "Mme Bah", "Mme Sow"]
        for school in schools:
            schl_students = students_by_school.get(school.id, [])
            if not schl_students:
                continue
            n_visits = random.randint(5, 12)
            for _ in range(n_visits):
                stu = random.choice(schl_students)
                vt = random.choice(hv_types)
                desc = {
                    HealthVisitType.CHECKUP: "Visite médicale annuelle",
                    HealthVisitType.ILLNESS: "Maux de tête, fièvre légère",
                    HealthVisitType.INJURY: "Blessure légère récréation",
                    HealthVisitType.VACCINATION: "Vaccination obligatoire",
                    HealthVisitType.OTHER: "Consultation diverse",
                }[vt]
                health_visits.append(HealthVisit(
                    schoolId=school.id, studentId=stu.id, type=vt,
                    description=desc,
                    visitDate=today - timedelta(days=random.randint(0, 30)),
                    nurseName=random.choice(nurses),
                    status=random.choice(list(HealthVisitStatus)),
                ))

        # ---- Bus routes : 1-3 par école (urbaines surtout) -------------
        bus_routes: list[BusRoute] = []
        urban_pref = ("Conakry", "Kindia", "Labé", "Kankan")
        for school in schools:
            urban = (school.prefecture or "") in urban_pref
            n_routes = random.randint(1, 3) if urban else random.randint(0, 1)
            existing_names: set[str] = set()
            for j in range(n_routes):
                name = f"Ligne {chr(65 + j)} — {school.commune or 'Centre'}"
                if name in existing_names:
                    continue
                existing_names.add(name)
                bus_routes.append(BusRoute(
                    schoolId=school.id,
                    name=name,
                    capacity=random.choice([35, 40, 50, 60]),
                    departureTime=f"{random.randint(6, 7):02d}:{random.choice([0, 15, 30, 45]):02d}",
                    returnTime=f"{random.randint(15, 17):02d}:{random.choice([0, 15, 30, 45]):02d}",
                    driverName=f"M. {random.choice(GUINEAN_LAST_NAMES)}",
                    driverPhone=f"+22462{random.randint(1000000, 9999999):07d}",
                    plate=f"GN-{random.randint(1000, 9999)}-{chr(65 + random.randint(0, 25))}",
                    status=random.choices(
                        list(TransportRouteStatus),
                        weights=[80, 15, 5], k=1,
                    )[0],
                    studentsAssigned=random.randint(15, 55),
                ))

        # ---- Meal services : 5 jours ouvrables × 60 écoles = 300 services
        meals: list[MealService] = []
        days_back = 5
        for d in range(days_back):
            sd = today - timedelta(days=d)
            if sd.weekday() >= 5:
                continue  # skip weekends
            for school in schools:
                planned = random.randint(80, 250)
                served = max(0, planned - random.randint(0, 30))
                meals.append(MealService(
                    schoolId=school.id,
                    type=MealServiceType.LUNCH,
                    serviceDate=sd,
                    mealsPlanned=planned, mealsServed=served,
                    costPerMealGNF=random.uniform(2200, 3000),
                    notes=None,
                ))

        # ---- Timetable : par classe, 5 jours × 4-6 créneaux ------------
        timetable: list[TimetableSlot] = []
        days_of_week = [DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY,
                        DayOfWeek.THURSDAY, DayOfWeek.FRIDAY]
        slot_starts = [(8, 0), (9, 0), (10, 15), (11, 15), (14, 0), (15, 0)]
        from datetime import time as dt_time
        for klass in classes:
            schl_teachers = teachers_by_school.get(klass.schoolId, [])
            for day in days_of_week:
                # Premier créneau au hasard pour varier
                n_slots = random.randint(4, 6)
                for hour, minute in slot_starts[:n_slots]:
                    end_hour = hour + 1 if minute < 30 else hour + 2
                    end_minute = (minute + 0) % 60
                    subj = random.choice(subjects)
                    teacher = random.choice(schl_teachers) if schl_teachers else None
                    timetable.append(TimetableSlot(
                        classRoomId=klass.id,
                        dayOfWeek=day,
                        startTime=dt_time(hour, minute),
                        endTime=dt_time(min(end_hour, 18), end_minute),
                        subjectId=subj.id,
                        teacherId=teacher.id if teacher else None,
                        room=f"Salle {random.randint(1, 20)}",
                    ))

        # Insertion par lots
        BATCH = 1000
        for items, name in [
            (incidents, "incidents"),
            (health_visits, "visites santé"),
            (bus_routes, "lignes de bus"),
            (meals, "services cantine"),
            (timetable, "créneaux EDT"),
        ]:
            for i in range(0, len(items), BATCH):
                session.add_all(items[i:i + BATCH])
                await session.flush()
            bp.step(name, len(items))

        await session.commit()
    return bp


# ===========================================================================
# Orchestration
# ===========================================================================
async def run() -> None:
    engine = get_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    ctx: dict = {}

    print("\n══════════════════════════════════════════════════════════")
    print(" GESTION-EE — Seed démo complet (17 blocs fonctionnels)")
    print("══════════════════════════════════════════════════════════")

    blocks = [
        seed_block_1_territory,
        seed_block_2_users,
        seed_block_3_schools_classes,
        seed_block_4_teachers,
        seed_block_5_parents_pool,
        seed_block_6_students,
        seed_block_7_academic_setup,
        seed_block_8_grades_bulletins,
        seed_block_9_attendance,
        seed_block_10_workflow,
        seed_block_11_notifications,
        seed_block_12_communications,
        seed_block_13_library,
        seed_block_14_inspections,
        seed_block_15_audit,
        seed_block_16_finance,
        seed_block_17_school_life,
    ]

    # Cleanup en premier — session dédiée
    async with factory() as session:
        try:
            await cleanup_demo_data(session)
        except Exception as exc:  # noqa: BLE001
            print(f"\n❌ Échec cleanup : {exc}")
            traceback.print_exc()
            await dispose_engine()
            return

    overall_start = time.perf_counter()

    for block_fn in blocks:
        async with factory() as session:
            try:
                bp = await block_fn(session, ctx)
                REPORT_ROWS.append(bp)
            except Exception:  # noqa: BLE001
                # Log capture déjà géré dans le contextmanager BlockProgress
                pass

    # Rapport final
    print("\n══════════════════════════════════════════════════════════")
    print("  📊 SYNTHÈSE")
    print("══════════════════════════════════════════════════════════")
    print(f"  Total : {time.perf_counter() - overall_start:.1f}s\n")
    for bp in REPORT_ROWS:
        if bp.error:
            print(f"  [{bp.num:>2}] ❌ {bp.name}\n         → {bp.error}")
        else:
            details = " · ".join(f"{n} {k}" for k, n in bp.counts.items())
            print(f"  [{bp.num:>2}] ✅ {bp.name}\n         → {details}")
    print()
    failed = [bp for bp in REPORT_ROWS if bp.error]
    if failed:
        print(f"⚠️  {len(failed)} bloc(s) en erreur — voir traces ci-dessus.")
    else:
        print("🎉 Seed terminé sans erreur.")
    print("\nComptes de démo (mot de passe = " + DEMO_PASSWORD + ") :")
    for email, _, _, role in DEMO_USERS:
        print(f"   {role.value:<22} {email}")
    print()

    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(run())
