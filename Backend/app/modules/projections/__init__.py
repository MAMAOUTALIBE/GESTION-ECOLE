"""Module 2A — Projections IIPE-UNESCO : taux de transition par cohortes.

Module dédié aux calculs prospectifs (taux de transition, projections cohorte).
Fondation Phase 2 carte scolaire :
* 2A — Transitions par paire de niveaux (CP1→CP2, …, CM1→CM2).
* 2B (à venir) — Projections cohorte multi-années depuis ces taux.
"""
from app.modules.projections.enums import TransitionScope
from app.modules.projections.models import TransitionRate

__all__ = ["TransitionRate", "TransitionScope"]
