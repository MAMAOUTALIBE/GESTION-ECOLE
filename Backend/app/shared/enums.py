"""Enums miroirs du schema.prisma. Les valeurs string DOIVENT rester
identiques pour garantir la compatibilité de la base de données et des
réponses JSON consommées par le frontend Angular existant.
"""
from enum import StrEnum


class UserRole(StrEnum):
    NATIONAL_ADMIN = "NATIONAL_ADMIN"
    MINISTRY_ADMIN = "MINISTRY_ADMIN"
    REGIONAL_ADMIN = "REGIONAL_ADMIN"
    INSPECTOR = "INSPECTOR"
    PREFECTURE_ADMIN = "PREFECTURE_ADMIN"
    SUB_PREFECTURE_ADMIN = "SUB_PREFECTURE_ADMIN"
    SCHOOL_DIRECTOR = "SCHOOL_DIRECTOR"
    TEACHER = "TEACHER"
    CENSUS_AGENT = "CENSUS_AGENT"


class ValidationStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ValidationEntityType(StrEnum):
    PREFECTURE = "PREFECTURE"
    SUB_PREFECTURE = "SUB_PREFECTURE"
    SCHOOL = "SCHOOL"
    TEACHER = "TEACHER"


class NotificationType(StrEnum):
    VALIDATION_REQUEST = "VALIDATION_REQUEST"
    VALIDATION_APPROVED = "VALIDATION_APPROVED"
    VALIDATION_REJECTED = "VALIDATION_REJECTED"
    CORRECTION_REQUIRED = "CORRECTION_REQUIRED"
    SYSTEM_ALERT = "SYSTEM_ALERT"
    MESSAGE = "MESSAGE"


class PersonType(StrEnum):
    STUDENT = "STUDENT"
    TEACHER = "TEACHER"


class Gender(StrEnum):
    FEMALE = "FEMALE"
    MALE = "MALE"
    OTHER = "OTHER"


class AttendanceStatus(StrEnum):
    PRESENT = "PRESENT"
    LATE = "LATE"
    ABSENT = "ABSENT"


class ParentRelationType(StrEnum):
    FATHER = "FATHER"
    MOTHER = "MOTHER"
    LEGAL_GUARDIAN = "LEGAL_GUARDIAN"
    EMERGENCY_CONTACT = "EMERGENCY_CONTACT"
    OTHER = "OTHER"


class AcademicPeriodType(StrEnum):
    TRIMESTER = "TRIMESTER"
    SEMESTER = "SEMESTER"


class AssessmentType(StrEnum):
    QUIZ = "QUIZ"
    HOMEWORK = "HOMEWORK"
    COMPOSITION = "COMPOSITION"
    NATIONAL_EXAM = "NATIONAL_EXAM"
    ORAL = "ORAL"
    PROJECT = "PROJECT"
    OTHER = "OTHER"


class AcademicValidationStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


class CommunicationChannel(StrEnum):
    SMS = "SMS"
    WHATSAPP = "WHATSAPP"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    IN_APP = "IN_APP"


class CommunicationStatus(StrEnum):
    DRAFT = "DRAFT"
    SENT = "SENT"
    FAILED = "FAILED"
    READ = "READ"


class LibraryStockStatus(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    WATCH = "WATCH"
    SHORTAGE = "SHORTAGE"


class LibraryLoanStatus(StrEnum):
    BORROWED = "BORROWED"
    LATE = "LATE"
    RETURNED = "RETURNED"


# =============================================================
# Phase 10 — School infrastructure & Inspections
# =============================================================
class WaterSource(StrEnum):
    NONE = "NONE"
    WELL = "WELL"
    BOREHOLE = "BOREHOLE"
    NETWORK = "NETWORK"
    RIVER = "RIVER"


class ElectricitySource(StrEnum):
    NONE = "NONE"
    GRID = "GRID"
    SOLAR = "SOLAR"
    GENERATOR = "GENERATOR"
    HYBRID = "HYBRID"


class BuildingCondition(StrEnum):
    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    FAIR = "FAIR"
    POOR = "POOR"
    DANGEROUS = "DANGEROUS"


class SchoolAffiliation(StrEnum):
    PUBLIC = "PUBLIC"
    PRIVATE_SECULAR = "PRIVATE_SECULAR"
    CATHOLIC = "CATHOLIC"
    PROTESTANT = "PROTESTANT"
    ISLAMIC = "ISLAMIC"
    QURANIC = "QURANIC"
    FRANCO_ARABIC = "FRANCO_ARABIC"


class InspectionStatus(StrEnum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class InspectionCriterion(StrEnum):
    """Standardized rubric used across all inspections."""
    GOVERNANCE = "GOVERNANCE"
    PEDAGOGY = "PEDAGOGY"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    SAFETY = "SAFETY"
    HYGIENE = "HYGIENE"
    EQUITY = "EQUITY"
    ATTENDANCE = "ATTENDANCE"
    DOCUMENTS = "DOCUMENTS"


class FindingSeverity(StrEnum):
    INFO = "INFO"
    MINOR = "MINOR"
    MAJOR = "MAJOR"
    CRITICAL = "CRITICAL"


class ActionItemStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"


# =============================================================
# Phase 11 — Finance & Budget
# =============================================================
class BudgetStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class BudgetCategory(StrEnum):
    """Lignes budgétaires standardisées (alignées sur le PEFA Guinée)."""
    SALARIES = "SALARIES"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    EQUIPMENT = "EQUIPMENT"
    OPERATIONS = "OPERATIONS"
    TRAINING = "TRAINING"
    TRANSPORT = "TRANSPORT"
    MEALS = "MEALS"
    MISC = "MISC"


class ExpenseStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PAID = "PAID"


class PolicyUnitCostCode(StrEnum):
    """Codes utilisés par le simulateur de politique."""
    NEW_SCHOOL = "NEW_SCHOOL"
    NEW_CLASSROOM = "NEW_CLASSROOM"
    TEACHER_YEAR = "TEACHER_YEAR"
    GIRLS_TOILETS = "GIRLS_TOILETS"
    ELECTRICITY_SOLAR = "ELECTRICITY_SOLAR"
    WATER_BOREHOLE = "WATER_BOREHOLE"


# =============================================================
# Phase 13 — Vie scolaire (discipline / santé / transport / cantine / emploi du temps)
# =============================================================
class IncidentType(StrEnum):
    LATENESS = "LATENESS"
    INSUBORDINATION = "INSUBORDINATION"
    FIGHTING = "FIGHTING"
    ABSENCE = "ABSENCE"
    BULLYING = "BULLYING"
    PROPERTY_DAMAGE = "PROPERTY_DAMAGE"
    OTHER = "OTHER"


class IncidentSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class IncidentSanction(StrEnum):
    NONE = "NONE"
    WARNING = "WARNING"
    DETENTION = "DETENTION"
    PARENT_MEETING = "PARENT_MEETING"
    SUSPENSION = "SUSPENSION"
    EXPULSION = "EXPULSION"


class HealthVisitType(StrEnum):
    CHECKUP = "CHECKUP"
    ILLNESS = "ILLNESS"
    INJURY = "INJURY"
    VACCINATION = "VACCINATION"
    OTHER = "OTHER"


class HealthVisitStatus(StrEnum):
    REPORTED = "REPORTED"
    TREATED = "TREATED"
    REFERRED = "REFERRED"
    RESOLVED = "RESOLVED"


class TransportRouteStatus(StrEnum):
    ACTIVE = "ACTIVE"
    MAINTENANCE = "MAINTENANCE"
    INACTIVE = "INACTIVE"


class MealServiceType(StrEnum):
    BREAKFAST = "BREAKFAST"
    LUNCH = "LUNCH"
    SNACK = "SNACK"


class DayOfWeek(StrEnum):
    MONDAY = "MONDAY"
    TUESDAY = "TUESDAY"
    WEDNESDAY = "WEDNESDAY"
    THURSDAY = "THURSDAY"
    FRIDAY = "FRIDAY"
    SATURDAY = "SATURDAY"
