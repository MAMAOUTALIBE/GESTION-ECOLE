"""Module 3B — Logique pure du simulateur what-if.

Aucun accès DB. Deux fonctions principales :

* ``apply_operations(schools, operations)`` : applique la séquence
  d'opérations (CREATE / CLOSE / MERGE) sur une liste de ``VirtualSchool``
  pour produire l'état simulé. Soulève ``ValueError`` si une opération
  référence un id inconnu (validation forte avant compute).
* ``compute_impact(baseline, simulated)`` : compare les deux états et
  produit un ``ImpactReport`` (couverture / saturation / distance /
  redistribution).

Une ``VirtualSchool`` est une copie en mémoire d'une école (réelle ou
fictive). On ne touche jamais à la table ``School``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal

from app.modules.simulator.schemas import (
    CloseSchoolOp,
    CoverageImpact,
    CreateSchoolOp,
    DistanceImpact,
    ImpactReport,
    MergeSchoolsOp,
    Operation,
    SaturationImpact,
)

# Norme MEN Guinée — cohérente avec Module 2C
# (``STUDENTS_PER_CLASSROOM_NORM``). On accepte aussi un override via
# argument car le simulateur peut être appelé avec une norme cible
# alternative (ex. 40 pour Education 2030 UNESCO).
DEFAULT_STUDENTS_PER_CLASSROOM_NORM = 50

# Seuil de saturation au-delà duquel on classe une école "critique"
# (cohérent avec ``CapacitySeverity.CRITICAL`` Module 2C).
CRITICAL_SATURATION_PCT = Decimal("100")


@dataclass
class VirtualSchool:
    """Vue en mémoire d'une école (réelle ou fictive).

    * ``id`` : identifiant. Pour les écoles réelles c'est ``School.id``,
      pour les écoles fictives un id synthétique (préfixé ``virtual-``).
    * ``isVirtual`` : True si l'école a été créée par le scénario (CREATE
      ou MERGE) ; False si elle vient de la table ``School``.
    * ``capacity`` : nombre de places (= classroomsUsable × NORM ou
      ``capacity`` direct pour les écoles fictives).
    * ``studentsCount`` : nb d'élèves rattachés au moment de la photo.
      Sert au calcul ``redistributedStudents``.
    * ``subPrefectureId`` : optionnel ; sert au calcul distance moyenne
      école-élève (centroid sub-prefecture comme proxy de la position
      des élèves de l'école).
    """

    id: str
    name: str
    lat: float | None
    lon: float | None
    capacity: int
    studentsCount: int
    subPrefectureId: str | None
    isVirtual: bool = False
    # Tag interne (compute_impact) : ids des écoles d'origine quand
    # cette ``VirtualSchool`` résulte d'un MERGE — utile pour traçabilité
    # (impact ne l'expose pas mais on garde le champ pour debug futur).
    mergedFrom: list[str] = field(default_factory=list)


# ===========================================================================
# Applique opérations
# ===========================================================================
def apply_operations(
    schools: list[VirtualSchool],
    operations: list[Operation],
    *,
    students_per_classroom_norm: int = DEFAULT_STUDENTS_PER_CLASSROOM_NORM,
) -> list[VirtualSchool]:
    """Applique la séquence d'opérations sur la photo de base.

    On travaille sur des **copies** : les ``VirtualSchool`` reçus en
    paramètre ne sont jamais modifiés (l'état baseline reste utilisable
    pour ``compute_impact``).

    Validation :

    * ``CLOSE_SCHOOL`` / ``MERGE_SCHOOLS`` doivent référencer des ids
      présents (réels) — sinon ``ValueError``.
    * ``MERGE_SCHOOLS`` doit avoir au moins 2 sources (déjà enforced par
      le schema, mais on re-check ici pour défense en profondeur).
    """
    _ = students_per_classroom_norm  # Pas utilisé ici (les capacités
    # sont déjà calculées dans les VirtualSchool en amont du service).
    # On garde l'argument pour cohérence d'API (futur override norme).

    # Copie pour isolation (on n'efface jamais l'état baseline).
    current = {s.id: _copy_school(s) for s in schools}
    # Compteur pour générer des ids synthétiques uniques.
    virtual_counter = 0

    def _next_virtual_id() -> str:
        nonlocal virtual_counter
        virtual_counter += 1
        return f"virtual-{virtual_counter:04d}"

    for op in operations:
        if isinstance(op, CreateSchoolOp):
            new_id = _next_virtual_id()
            current[new_id] = VirtualSchool(
                id=new_id,
                name=op.name,
                lat=op.lat,
                lon=op.lon,
                capacity=op.capacity,
                studentsCount=0,  # une école nouvelle démarre vide
                subPrefectureId=op.subPrefectureId,
                isVirtual=True,
            )
        elif isinstance(op, CloseSchoolOp):
            if op.schoolId not in current:
                raise ValueError(
                    f"École introuvable : {op.schoolId}",
                )
            # On retire mais on garde le studentsCount pour le total
            # redistribué — c'est compute_impact qui le calcule à partir
            # de baseline vs simulated, donc ici un simple del suffit.
            del current[op.schoolId]
        elif isinstance(op, MergeSchoolsOp):
            if len(op.sourceSchoolIds) < 2:
                # Défense en profondeur (Pydantic valide déjà min_length=2).
                raise ValueError(
                    "MERGE_SCHOOLS exige au moins 2 écoles sources.",
                )
            unknown = [
                sid for sid in op.sourceSchoolIds if sid not in current
            ]
            if unknown:
                raise ValueError(
                    "Écoles sources introuvables pour MERGE : "
                    f"{', '.join(unknown)}",
                )
            merged_capacity = sum(
                current[sid].capacity for sid in op.sourceSchoolIds
            )
            merged_students = sum(
                current[sid].studentsCount for sid in op.sourceSchoolIds
            )
            for sid in op.sourceSchoolIds:
                del current[sid]
            new_id = _next_virtual_id()
            current[new_id] = VirtualSchool(
                id=new_id,
                name=op.targetName,
                lat=op.lat,
                lon=op.lon,
                capacity=merged_capacity,
                studentsCount=merged_students,
                subPrefectureId=op.subPrefectureId,
                isVirtual=True,
                mergedFrom=list(op.sourceSchoolIds),
            )
        else:  # pragma: no cover - Pydantic discriminated union refuse déjà
            raise ValueError(f"Type d'opération inconnu : {type(op)!r}")

    return list(current.values())


# ===========================================================================
# Compute impact
# ===========================================================================
def compute_impact(
    baseline_schools: list[VirtualSchool],
    simulated_schools: list[VirtualSchool],
    *,
    students_per_classroom_norm: int = DEFAULT_STUDENTS_PER_CLASSROOM_NORM,
    sub_prefecture_centroids: dict[str, tuple[float, float]] | None = None,
) -> ImpactReport:
    """Calcule l'impact d'un scénario en comparant baseline vs simulé.

    Métriques :

    * ``coverage`` : nb d'écoles avant/après + delta en pourcentage.
    * ``saturation`` : saturation moyenne (% studentsCount/capacity)
      pondérée par école, + nb d'écoles critiques (sat > 100 %).
    * ``distance`` : distance moyenne école-élève estimée via centroid
      sub-prefecture. ``None`` si on n'a pas de centroids fournis.
    * ``redistributedStudents`` : somme des élèves des écoles présentes
      dans baseline et **absentes** du simulé (fermées + sources d'un
      merge sont redistribués vers les écoles restantes / fusionnées).
    """
    _ = students_per_classroom_norm  # Conservé pour cohérence API
    # (les capacités sont déjà passées en argument via VirtualSchool).

    # Coverage ----------------------------------------------------------------
    before = len(baseline_schools)
    after = len(simulated_schools)
    if before == 0:
        coverage_delta = Decimal("0.00")
    else:
        raw = (Decimal(after - before) / Decimal(before)) * Decimal("100")
        coverage_delta = raw.quantize(
            Decimal("0.01"), rounding=ROUND_HALF_EVEN,
        )
    coverage = CoverageImpact(
        beforeCount=before,
        afterCount=after,
        deltaPct=coverage_delta,
    )

    # Saturation --------------------------------------------------------------
    sat_before_avg, critical_before = _mean_saturation(baseline_schools)
    sat_after_avg, critical_after = _mean_saturation(simulated_schools)
    saturation = SaturationImpact(
        beforeAvg=sat_before_avg,
        afterAvg=sat_after_avg,
        criticalSchoolsBefore=critical_before,
        criticalSchoolsAfter=critical_after,
    )

    # Distance ---------------------------------------------------------------
    distance = _compute_distance_impact(
        baseline_schools=baseline_schools,
        simulated_schools=simulated_schools,
        sub_prefecture_centroids=sub_prefecture_centroids or {},
    )

    # Redistributed students -------------------------------------------------
    baseline_by_id = {s.id: s for s in baseline_schools}
    simulated_ids = {s.id for s in simulated_schools}
    redistributed = sum(
        s.studentsCount
        for sid, s in baseline_by_id.items()
        if sid not in simulated_ids
    )

    return ImpactReport(
        coverage=coverage,
        saturation=saturation,
        distance=distance,
        redistributedStudents=redistributed,
    )


# ===========================================================================
# Helpers privés
# ===========================================================================
def _copy_school(s: VirtualSchool) -> VirtualSchool:
    """Renvoie une copie shallow (suffit pour notre dataclass plate)."""
    return VirtualSchool(
        id=s.id,
        name=s.name,
        lat=s.lat,
        lon=s.lon,
        capacity=s.capacity,
        studentsCount=s.studentsCount,
        subPrefectureId=s.subPrefectureId,
        isVirtual=s.isVirtual,
        mergedFrom=list(s.mergedFrom),
    )


def _mean_saturation(
    schools: list[VirtualSchool],
) -> tuple[Decimal | None, int]:
    """Saturation moyenne pondérée + nombre d'écoles critiques.

    On exclut les écoles à ``capacity == 0`` du calcul de la moyenne
    (sinon division par zéro) mais on les compte comme **critiques** si
    elles ont des élèves (capacity 0 + studentsCount > 0 = situation
    extrême — cohérent avec ``CapacitySeverity.CRITICAL`` Module 2C).
    """
    valid_saturations: list[Decimal] = []
    critical = 0
    for s in schools:
        if s.capacity == 0:
            if s.studentsCount > 0:
                critical += 1
            continue
        raw = (
            Decimal(s.studentsCount) / Decimal(s.capacity)
        ) * Decimal("100")
        sat = raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        valid_saturations.append(sat)
        if sat > CRITICAL_SATURATION_PCT:
            critical += 1
    if not valid_saturations:
        return None, critical
    total = sum(valid_saturations)
    avg = (total / Decimal(len(valid_saturations))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_EVEN,
    )
    return avg, critical


def _haversine_km(
    lat1: float, lon1: float, lat2: float, lon2: float,
) -> float:
    """Distance haversine (km) entre deux points lat/lon en degrés."""
    earth_radius_km = 6371.0
    rlat1 = math.radians(lat1)
    rlat2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.asin(min(1.0, math.sqrt(a)))
    return earth_radius_km * c


def _compute_distance_impact(
    *,
    baseline_schools: list[VirtualSchool],
    simulated_schools: list[VirtualSchool],
    sub_prefecture_centroids: dict[str, tuple[float, float]],
) -> DistanceImpact:
    """Distance école-élève moyenne, estimée via centroid sub-pref.

    Idée : pour chaque école, on prend le centroid de sa SubPrefecture
    comme proxy de la position moyenne de ses élèves, puis distance
    haversine école-centroid. On pondère par ``studentsCount`` pour
    refléter le poids démographique. Une école sans subPrefectureId ou
    sans coordonnées est exclue.

    ``deltaKm`` = ``afterKmMean - beforeKmMean`` (négatif = meilleure
    proximité élèves-écoles).
    """
    before = _weighted_mean_distance(
        baseline_schools, sub_prefecture_centroids,
    )
    after = _weighted_mean_distance(
        simulated_schools, sub_prefecture_centroids,
    )
    delta: Decimal | None
    if before is None or after is None:
        delta = None
    else:
        delta = (after - before).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_EVEN,
        )
    return DistanceImpact(
        beforeKmMean=before,
        afterKmMean=after,
        deltaKm=delta,
    )


def _weighted_mean_distance(
    schools: list[VirtualSchool],
    centroids: dict[str, tuple[float, float]],
) -> Decimal | None:
    """Distance pondérée par ``studentsCount`` (proxy IIPE simple)."""
    total_weighted = 0.0
    total_weight = 0
    for s in schools:
        if (
            s.lat is None
            or s.lon is None
            or s.subPrefectureId is None
            or s.subPrefectureId not in centroids
            or s.studentsCount <= 0
        ):
            continue
        c_lat, c_lon = centroids[s.subPrefectureId]
        dist = _haversine_km(s.lat, s.lon, c_lat, c_lon)
        total_weighted += dist * s.studentsCount
        total_weight += s.studentsCount
    if total_weight == 0:
        return None
    avg = total_weighted / total_weight
    return Decimal(str(avg)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_EVEN,
    )


__all__ = [
    "CRITICAL_SATURATION_PCT",
    "DEFAULT_STUDENTS_PER_CLASSROOM_NORM",
    "VirtualSchool",
    "apply_operations",
    "compute_impact",
]
