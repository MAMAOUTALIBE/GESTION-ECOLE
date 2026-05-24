"""Module 2A + 2B — Projections IIPE-UNESCO.

Module dédié aux calculs prospectifs :
* 2A — Transitions par paire de niveaux (CP1→CP2, …, CM1→CM2).
* 2B — Projections cohorte multi-années horizon 1..10 ans à partir
  de ces taux.
"""
from app.modules.projections.enums import (
    BASELINE_SCENARIO_ID,
    DEMOGRAPHIC_GROWTH_RATE_DEFAULT,
    TransitionScope,
)
from app.modules.projections.models import (
    ProjectedEnrollment,
    ProjectionScenario,
    TransitionRate,
)

__all__ = [
    "BASELINE_SCENARIO_ID",
    "DEMOGRAPHIC_GROWTH_RATE_DEFAULT",
    "ProjectedEnrollment",
    "ProjectionScenario",
    "TransitionRate",
    "TransitionScope",
]
