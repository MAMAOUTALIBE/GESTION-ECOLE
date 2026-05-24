"""Module 8 — PredictionService : orchestration extraction → prédiction → persist."""
from __future__ import annotations

import os
import threading
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationFailedError
from app.modules.census.models import Student
from app.modules.predictions.enums import DropoutRiskLevel
from app.modules.predictions.features import extract_features
from app.modules.predictions.model import DropoutModel
from app.modules.predictions.models import DropoutModelMetadata, DropoutPrediction
from app.modules.predictions.training import DEFAULT_ARTIFACT_PATH
from app.shared.base import generate_cuid

# Cache process-level du modèle. ``threading.Lock`` parce qu'on peut se faire
# appeler depuis plusieurs workers gunicorn/uvicorn dans le même process.
_MODEL_CACHE: dict[str, DropoutModel | None] = {"current": None}
_MODEL_LOCK = threading.Lock()


def _reset_model_cache() -> None:
    """Vide le cache process-level (utile entre tests)."""
    with _MODEL_LOCK:
        _MODEL_CACHE["current"] = None


class PredictionService:
    """Service centralisé pour la prédiction du décrochage scolaire."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -------------------------------------------------------------------
    # Model loading
    # -------------------------------------------------------------------
    async def _load_model(self) -> DropoutModel:
        """Charge le modèle courant. Throws si aucun modèle dispo."""
        with _MODEL_LOCK:
            cached = _MODEL_CACHE["current"]
            if cached is not None:
                return cached

        # Récupère la dernière metadata
        stmt = (
            select(DropoutModelMetadata)
            .order_by(DropoutModelMetadata.trainedAt.desc())
            .limit(1)
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise ValidationFailedError(
                detail=(
                    "Aucun modèle de prédiction disponible. "
                    "Entraînez-en un via POST /api/predictions/model/train."
                ),
            )
        if not os.path.exists(row.artifactPath):
            raise ValidationFailedError(
                detail=(
                    f"Artefact modèle introuvable ({row.artifactPath}). "
                    "Ré-entraînez via POST /api/predictions/model/train."
                ),
            )
        model = DropoutModel.load(row.artifactPath)
        with _MODEL_LOCK:
            _MODEL_CACHE["current"] = model
        return model

    # -------------------------------------------------------------------
    # Single student
    # -------------------------------------------------------------------
    async def predict_student(self, student_id: str) -> DropoutPrediction:
        """Calcule + persiste un score pour un élève."""
        student = await self.session.get(Student, student_id)
        if student is None:
            raise NotFoundError(detail=f"Élève {student_id} introuvable")

        model = await self._load_model()
        now = datetime.now(UTC)
        features = await extract_features(self.session, student_id, now.date())
        proba, level = model.predict_proba(features)

        prediction = DropoutPrediction(
            id=generate_cuid(),
            studentId=student_id,
            schoolYearId=None,
            computedAt=now,
            probability=proba,
            riskLevel=level,
            featuresSnapshot=features,
            modelVersion=model.version,
        )
        self.session.add(prediction)
        await self.session.flush()

        # Module 13 — push realtime UNIQUEMENT pour les prédictions HIGH risk.
        # Les MEDIUM/LOW restent disponibles en GET ; on évite le bruit
        # cockpit (un batch_predict_school d'une école de 1000 élèves
        # n'émettra que les ~5% HIGH, soit ~50 events).
        if level == DropoutRiskLevel.HIGH:
            try:
                from app.modules.realtime.service import RealtimeService
                from app.modules.schools.models import School as _School

                region_id = (await self.session.execute(
                    select(_School.regionId).where(_School.id == student.schoolId)
                )).scalar_one_or_none()
                await RealtimeService.publish_dropout_prediction_high(
                    student_id=student_id,
                    school_id=student.schoolId,
                    region_id=region_id,
                    probability=proba,
                )
            except Exception:  # pragma: no cover — best-effort
                pass

        return prediction

    # -------------------------------------------------------------------
    # Batch (school)
    # -------------------------------------------------------------------
    async def batch_predict_school(self, school_id: str) -> int:
        """Calcule un score pour TOUS les élèves d'une école.

        Retourne le nombre de prédictions persistées. Pour des écoles avec
        plusieurs milliers d'élèves on déléguera à une task Celery
        (cf. ``app.workers.prediction_tasks``).
        """
        student_ids_stmt = select(Student.id).where(Student.schoolId == school_id)
        student_ids = list((await self.session.execute(student_ids_stmt)).scalars())
        count = 0
        for sid in student_ids:
            try:
                await self.predict_student(sid)
            except NotFoundError:
                continue
            count += 1
        return count

    # -------------------------------------------------------------------
    # Listing
    # -------------------------------------------------------------------
    async def list_at_risk(
        self, school_id: str, level: DropoutRiskLevel = DropoutRiskLevel.HIGH,
        limit: int = 100,
    ) -> list[DropoutPrediction]:
        """Liste le dernier score par élève (filtré par level) d'une école.

        Stratégie MVP : on récupère TOUS les rows ``DropoutPrediction``
        joints à Student pour l'école, on déduplique en mémoire (dernier
        par studentId), puis on filtre par level. Pour 1k élèves c'est OK.
        """
        stmt = (
            select(DropoutPrediction)
            .join(Student, Student.id == DropoutPrediction.studentId)
            .where(Student.schoolId == school_id)
            .where(DropoutPrediction.riskLevel == level)
            .order_by(DropoutPrediction.computedAt.desc())
        )
        rows = list((await self.session.execute(stmt)).scalars())
        # Dedup : ne garder que le dernier score par élève
        seen: set[str] = set()
        uniq: list[DropoutPrediction] = []
        for r in rows:
            if r.studentId in seen:
                continue
            seen.add(r.studentId)
            uniq.append(r)
            if len(uniq) >= limit:
                break
        return uniq

    # -------------------------------------------------------------------
    # Model info
    # -------------------------------------------------------------------
    async def get_current_model_info(self) -> DropoutModelMetadata | None:
        stmt = (
            select(DropoutModelMetadata)
            .order_by(DropoutModelMetadata.trainedAt.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


__all__ = [
    "DEFAULT_ARTIFACT_PATH",
    "PredictionService",
    "_reset_model_cache",
]
