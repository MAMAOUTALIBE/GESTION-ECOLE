"""Module 1A — Enums du module Enrollment.

EnrollmentClassLevel
--------------------
Liste des niveaux scolaires guinéens (maternelle + primaire) utilisés pour
désagréger les effectifs déclarés à la carte scolaire. La nomenclature suit
la pratique du MENA Guinée (cf. doc IIPE 2023). On reste volontairement
limité au cycle 1 (préscolaire + primaire) — Module 1A est la fondation ;
le cycle secondaire (7e..12e) viendra avec Module 1C.

EnrollmentSource
----------------
Indique l'origine de la mesure pour gérer le conflit entre déclaration
officielle (CENSUS_DECLARED, source de vérité pilotage) et calcul live
sur la base Student (COMPUTED_FROM_STUDENTS, utilisé pour data quality).
IMPORT trace les bulks d'historique (avant migration logiciel).
"""
from __future__ import annotations

from enum import StrEnum


class EnrollmentClassLevel(StrEnum):
    """Niveaux scolaires primaire/préscolaire guinéens."""

    MATERNELLE_1 = "MATERNELLE_1"
    MATERNELLE_2 = "MATERNELLE_2"
    MATERNELLE_3 = "MATERNELLE_3"
    CP1 = "CP1"
    CP2 = "CP2"
    CE1 = "CE1"
    CE2 = "CE2"
    CM1 = "CM1"
    CM2 = "CM2"


class EnrollmentSource(StrEnum):
    """Origine d'une mesure d'effectif désagrégé."""

    CENSUS_DECLARED = "CENSUS_DECLARED"
    COMPUTED_FROM_STUDENTS = "COMPUTED_FROM_STUDENTS"
    IMPORT = "IMPORT"


__all__ = ["EnrollmentClassLevel", "EnrollmentSource"]
