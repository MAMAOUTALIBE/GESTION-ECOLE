"""Module 3B — Service du simulateur what-if.

Responsabilités :

* CRUD sur ``SimulationScenario`` (création, archivage, lecture).
* ``compute_scenario`` : charge la photo du réseau (read-only sur
  ``School``), applique les opérations, calcule l'impact, persiste
  ``impactJson``.
* RBAC : write réservé à NATIONAL/MINISTRY/REGIONAL_ADMIN. Read filtré
  par scope (un user voit ses scénarios + ceux de son scope territorial
  pour les rôles centraux).

Lecture des écoles
------------------
On charge ``School`` + agrégats ``Student.schoolId`` count + Enrollment
moyens pour estimer un ``studentsCount`` par école — read-only. Pour
``capacity``, on réutilise la formule Module 2C
(``classroomsUsable × STUDENTS_PER_CLASSROOM_NORM``).

Centroids sub-prefecture
------------------------
On calcule à la volée la moyenne lat/lon des écoles APPROVED de chaque
sub-prefecture (proxy IIPE simple). Ces centroids servent au calcul de
distance école-élève.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError
from app.modules.academics.models import SchoolYear
from app.modules.auth.models import User
from app.modules.census.models import Student
from app.modules.projections.enums import STUDENTS_PER_CLASSROOM_NORM
from app.modules.schools.models import School
from app.modules.simulator.enums import ScenarioStatus
from app.modules.simulator.models import SimulationScenario
from app.modules.simulator.schemas import (
    CloseSchoolOp,
    CreateSchoolOp,
    ImpactReport,
    MergeSchoolsOp,
    Operation,
    ScenarioCreate,
    ScenarioRead,
)
from app.modules.simulator.simulator import (
    VirtualSchool,
    apply_operations,
    compute_impact,
)
from app.shared.base import generate_cuid
from app.shared.enums import UserRole, ValidationStatus
from app.shared.permissions import NATIONAL_SCOPE_ROLES

# Rôles autorisés à créer / compute / archiver un scénario. On inclut
# REGIONAL_ADMIN : la réorganisation du réseau se prépare souvent au
# niveau régional avant remontée nationale.
SIMULATOR_WRITE_ROLES: frozenset[UserRole] = frozenset(
    {
        UserRole.NATIONAL_ADMIN,
        UserRole.MINISTRY_ADMIN,
        UserRole.REGIONAL_ADMIN,
    }
)


class SimulatorService:
    """Calcule + lit les scénarios what-if de réorganisation du réseau."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ==================================================================
    # Création
    # ==================================================================
    async def create_scenario(
        self,
        dto: ScenarioCreate,
        actor: User,
    ) -> ScenarioRead:
        """Crée un scénario en statut DRAFT. RBAC : NATIONAL/MINISTRY/REGIONAL_ADMIN."""
        self._ensure_write_role(actor)

        # Validation année source.
        base_year = (
            await self.session.execute(
                select(SchoolYear).where(
                    SchoolYear.id == dto.baselineSchoolYearId,
                )
            )
        ).scalars().one_or_none()
        if base_year is None:
            raise NotFoundError(
                detail=(
                    "SchoolYear introuvable : "
                    f"{dto.baselineSchoolYearId}"
                ),
            )

        scenario = SimulationScenario(
            id=generate_cuid(),
            name=dto.name,
            description=dto.description,
            createdById=actor.id,
            status=ScenarioStatus.DRAFT,
            baselineSchoolYearId=dto.baselineSchoolYearId,
            scenarioJson=_serialise_operations(dto.operations),
            impactJson=None,
            computedAt=None,
        )
        self.session.add(scenario)
        await self.session.flush()
        return ScenarioRead.model_validate(scenario)

    # ==================================================================
    # Compute
    # ==================================================================
    async def compute_scenario(
        self,
        scenario_id: str,
        actor: User,
    ) -> ImpactReport:
        """Charge baseline, applique opérations, persiste impactJson.

        Idempotent : un recompute remplace l'``impactJson`` précédent.
        """
        self._ensure_write_role(actor)
        scenario = await self._get_or_404(scenario_id)
        self._ensure_visible(scenario, actor)

        if scenario.status == ScenarioStatus.ARCHIVED:
            raise ForbiddenError(
                detail=(
                    "Scénario archivé : recalcul interdit. "
                    "Recréer un nouveau scénario si besoin."
                ),
            )

        # Charge la photo du réseau (read-only). On garde toutes les
        # écoles APPROVED, indépendamment du scope du user — un
        # planificateur a besoin de la vue complète pour évaluer
        # l'impact (ex. fermer une école et voir l'effet sur les
        # voisines).
        baseline_schools = await self._load_baseline_schools()
        centroids = _compute_centroids(baseline_schools)

        operations = _deserialise_operations(scenario.scenarioJson)
        simulated_schools = apply_operations(baseline_schools, operations)

        report = compute_impact(
            baseline_schools,
            simulated_schools,
            sub_prefecture_centroids=centroids,
        )

        scenario.impactJson = report.model_dump(mode="json")
        scenario.status = ScenarioStatus.COMPUTED
        scenario.computedAt = datetime.now(UTC)
        await self.session.flush()
        return report

    # ==================================================================
    # Listing / Get
    # ==================================================================
    async def list_scenarios(
        self,
        actor: User,
    ) -> list[ScenarioRead]:
        """Liste les scénarios visibles par l'utilisateur.

        Règles de visibilité :

        * NATIONAL / MINISTRY : tout sauf ARCHIVED par défaut (les
          archives restent en DB ; on ne les expose pas par défaut pour
          alléger la liste).
        * Sinon (REGIONAL_ADMIN, autres) : uniquement les scénarios créés
          par l'utilisateur. (On ne fait pas de filtre par région sur
          ``scenarioJson`` pour éviter une logique fragile ; chaque
          utilisateur voit donc strictement ses propres scénarios sauf
          rôle central.)
        """
        stmt = select(SimulationScenario).where(
            SimulationScenario.status != ScenarioStatus.ARCHIVED,
        )
        if actor.role not in NATIONAL_SCOPE_ROLES:
            stmt = stmt.where(SimulationScenario.createdById == actor.id)
        stmt = stmt.order_by(SimulationScenario.createdAt.desc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [ScenarioRead.model_validate(r) for r in rows]

    async def get_scenario(
        self,
        scenario_id: str,
        actor: User,
    ) -> ScenarioRead:
        """Renvoie un scénario unique, validation RBAC inclut visibilité."""
        scenario = await self._get_or_404(scenario_id)
        self._ensure_visible(scenario, actor)
        return ScenarioRead.model_validate(scenario)

    # ==================================================================
    # Archive
    # ==================================================================
    async def archive_scenario(
        self,
        scenario_id: str,
        actor: User,
    ) -> ScenarioRead:
        """Marque le scénario comme ARCHIVED."""
        self._ensure_write_role(actor)
        scenario = await self._get_or_404(scenario_id)
        self._ensure_visible(scenario, actor)
        scenario.status = ScenarioStatus.ARCHIVED
        await self.session.flush()
        return ScenarioRead.model_validate(scenario)

    # ==================================================================
    # Helpers privés
    # ==================================================================
    def _ensure_write_role(self, actor: User) -> None:
        if actor.role not in SIMULATOR_WRITE_ROLES:
            raise ForbiddenError(
                detail=(
                    "Seuls les administrateurs central et régional peuvent "
                    "manipuler les scénarios de simulation."
                ),
                extra={
                    "required_any_of": sorted(
                        r.value for r in SIMULATOR_WRITE_ROLES
                    ),
                },
            )

    def _ensure_visible(
        self,
        scenario: SimulationScenario,
        actor: User,
    ) -> None:
        """Cohérent avec ``list_scenarios`` : un user non-central ne peut
        accéder qu'à ses propres scénarios."""
        if actor.role in NATIONAL_SCOPE_ROLES:
            return
        if scenario.createdById == actor.id:
            return
        raise ForbiddenError(
            detail=(
                "Vous n'avez pas la permission d'accéder à ce scénario."
            ),
        )

    async def _get_or_404(
        self, scenario_id: str,
    ) -> SimulationScenario:
        scenario = (
            await self.session.execute(
                select(SimulationScenario)
                .where(SimulationScenario.id == scenario_id)
            )
        ).scalars().one_or_none()
        if scenario is None:
            raise NotFoundError(
                detail=f"Scénario introuvable : {scenario_id}",
            )
        return scenario

    async def _load_baseline_schools(self) -> list[VirtualSchool]:
        """Charge toutes les écoles APPROVED en ``VirtualSchool``.

        Lecture pure (SELECT) : aucune modification de la table ``School``.
        """
        # 1) Schools.
        schools_stmt = select(
            School.id,
            School.name,
            School.latitude,
            School.longitude,
            School.classroomsUsable,
            School.subPrefectureId,
        ).where(School.status == ValidationStatus.APPROVED)
        school_rows = (await self.session.execute(schools_stmt)).all()

        # 2) Comptage students par école.
        students_stmt = (
            select(
                Student.schoolId,
                func.count(Student.id).label("total"),
            )
            .group_by(Student.schoolId)
        )
        students_rows = (await self.session.execute(students_stmt)).all()
        students_by_school: dict[str, int] = {
            sid: int(total) for sid, total in students_rows if sid
        }

        out: list[VirtualSchool] = []
        for sid, name, lat, lon, classrooms, sub_pref_id in school_rows:
            usable = int(classrooms or 0)
            out.append(
                VirtualSchool(
                    id=sid,
                    name=name,
                    lat=float(lat) if lat is not None else None,
                    lon=float(lon) if lon is not None else None,
                    capacity=usable * STUDENTS_PER_CLASSROOM_NORM,
                    studentsCount=students_by_school.get(sid, 0),
                    subPrefectureId=sub_pref_id,
                    isVirtual=False,
                )
            )
        return out


# ===========================================================================
# Helpers privés (module level)
# ===========================================================================
def _serialise_operations(operations: list[Operation]) -> dict[str, Any]:
    """Sérialise la liste d'opérations en payload JSONB stockable."""
    return {
        "operations": [op.model_dump(mode="json") for op in operations],
    }


def _deserialise_operations(payload: Any) -> list[Operation]:
    """Rebuild la liste d'opérations Pydantic depuis ``scenarioJson``."""
    if not isinstance(payload, dict):
        raise ValueError(
            "scenarioJson invalide : dict attendu.",
        )
    ops_raw = payload.get("operations")
    if not isinstance(ops_raw, list):
        raise ValueError(
            "scenarioJson.operations invalide : list attendue.",
        )
    out: list[Operation] = []
    for raw in ops_raw:
        if not isinstance(raw, dict):
            raise ValueError(
                "Opération invalide : dict attendu.",
            )
        op_type = raw.get("type")
        if op_type == "CREATE_SCHOOL":
            out.append(CreateSchoolOp.model_validate(raw))
        elif op_type == "CLOSE_SCHOOL":
            out.append(CloseSchoolOp.model_validate(raw))
        elif op_type == "MERGE_SCHOOLS":
            out.append(MergeSchoolsOp.model_validate(raw))
        else:
            raise ValueError(
                f"Type d'opération inconnu : {op_type!r}",
            )
    return out


def _compute_centroids(
    schools: list[VirtualSchool],
) -> dict[str, tuple[float, float]]:
    """Centroid lat/lon par subPrefectureId = moyenne des écoles.

    Proxy IIPE simple pour positionner les élèves : on prend la moyenne
    des positions des écoles d'une sub-prefecture. Les écoles sans
    coordonnées sont exclues du moyennage.
    """
    accum: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for s in schools:
        if (
            s.subPrefectureId is None
            or s.lat is None
            or s.lon is None
        ):
            continue
        accum[s.subPrefectureId].append((s.lat, s.lon))
    out: dict[str, tuple[float, float]] = {}
    for sub_id, points in accum.items():
        if not points:
            continue
        mean_lat = sum(p[0] for p in points) / len(points)
        mean_lon = sum(p[1] for p in points) / len(points)
        out[sub_id] = (mean_lat, mean_lon)
    return out


__all__ = [
    "SIMULATOR_WRITE_ROLES",
    "SimulatorService",
]
