"""Module 2A + 2B + 2C + 2D — Projections IIPE-UNESCO.

Module dédié aux calculs prospectifs :
* 2A — Transitions par paire de niveaux (CP1→CP2, …, CM1→CM2).
* 2B — Projections cohorte multi-années horizon 1..10 ans.
* 2C — Capacité vs demande projetée (planification infrastructure).
* 2D — Snapshots staffing enseignants + recommandations transferts.
"""
from app.modules.projections.enums import (
    BASELINE_SCENARIO_ID,
    DEMOGRAPHIC_GROWTH_RATE_DEFAULT,
    RecommendationStatus,
    StaffingSeverity,
    TransitionScope,
)
from app.modules.projections.models import (
    ProjectedEnrollment,
    ProjectionScenario,
    TeacherStaffingSnapshot,
    TeacherTransferRecommendation,
    TransitionRate,
)

__all__ = [
    "BASELINE_SCENARIO_ID",
    "DEMOGRAPHIC_GROWTH_RATE_DEFAULT",
    "ProjectedEnrollment",
    "ProjectionScenario",
    "RecommendationStatus",
    "StaffingSeverity",
    "TeacherStaffingSnapshot",
    "TeacherTransferRecommendation",
    "TransitionRate",
    "TransitionScope",
]
