"""Module 8 — Génération de training set synthétique + tâche d'entraînement.

Why synthetic ?
---------------
On n'a pas de dataset historique labellisé "élève X a abandonné dans les 90
jours" — il faudrait 2-3 ans de suivi terrain pour en avoir un solide. Pour
bootstrap le pipeline MVP, on génère un dataset synthétique avec un
**score latent linéaire** + bruit gaussien :

    latent = -3.5*att90 - 3.5*att30 - 0.30*grade - 0.30*trend
             + 0.45*incidents + 0.20*late + 4.0 + N(0, 0.5)
    y = 1 si latent > median(latent) + 0.6   (≈ 35% positifs)

Le signe des coefficients respecte l'intuition métier : moins de présence
= plus de risque, moins bonnes notes = plus de risque, plus d'incidents
ou de retards = plus de risque. La structure linéaire facilite l'apprentis-
sage d'une logistic regression pour le bootstrap. Quand Module 8.1
récupèrera des données réelles, on remplacera ``generate_synthetic_training_set``
par un loader DB sans toucher au reste du pipeline.
"""
from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.predictions.features import FEATURE_NAMES
from app.modules.predictions.model import train
from app.modules.predictions.models import DropoutModelMetadata
from app.shared.base import generate_cuid

DEFAULT_ARTIFACT_PATH = "/tmp/dropout_model.joblib"


def generate_synthetic_training_set(
    n_samples: int = 5000, *, seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """Génère un dataset (X, y) synthétique de ``n_samples`` lignes.

    X shape: (n_samples, len(FEATURE_NAMES))
    y shape: (n_samples,) avec valeurs ∈ {0, 1}.

    Distributions des features :
        attendance_rate_90d, _30d : Beta(8, 2) → centré 0.8, queue basse
        grade_avg : Normal(10, 4) clamp [0, 20]
        grade_trend : Normal(0, 2)
        incidents_180d : Poisson(1)
        late_30d : Poisson(2)

    Label : règle déterministe + 5% bruit pour éviter qu'un modèle linéaire
    apprenne à 100% (irréaliste).
    """
    rng = np.random.default_rng(seed)
    n_feat = len(FEATURE_NAMES)
    X = np.zeros((n_samples, n_feat), dtype=np.float64)

    # 0: attendance_90d, 1: attendance_30d
    X[:, 0] = np.clip(rng.beta(8, 2, n_samples), 0.0, 1.0)
    # attendance_30d corrélé à _90d mais bruité
    X[:, 1] = np.clip(X[:, 0] + rng.normal(0, 0.05, n_samples), 0.0, 1.0)
    # 2: grade_avg_last_period
    X[:, 2] = np.clip(rng.normal(10, 4, n_samples), 0.0, 20.0)
    # 3: grade_trend
    X[:, 3] = rng.normal(0, 2, n_samples)
    # 4: incidents_180d
    X[:, 4] = rng.poisson(1, n_samples).astype(np.float64)
    # 5: late_30d
    X[:, 5] = rng.poisson(2, n_samples).astype(np.float64)

    # Label : on construit un score latent linéaire (corrélations plausibles)
    # puis on seuil pour obtenir une distribution ~25-35% de positifs. Le
    # signe des coefficients reflète l'intuition métier : moins de présence
    # = plus de risque, moins bonnes notes = plus de risque, etc.
    #
    # Cette structure linéaire est exprès "logistic regression-friendly"
    # pour que le bootstrap MVP atteigne une AUC > 0.7 facilement. Quand on
    # branchera de vraies données labellisées (Module 8.1), on supprimera
    # cette fonction et on calibrera sur la réalité (qui sera moins
    # linéaire, mais on aura aussi plus de samples).
    att90 = X[:, 0]
    att30 = X[:, 1]
    grade = X[:, 2]
    trend = X[:, 3]
    incidents = X[:, 4]
    late = X[:, 5]
    latent = (
        -3.5 * att90
        - 3.5 * att30
        - 0.30 * grade
        - 0.30 * trend
        + 0.45 * incidents
        + 0.20 * late
        + 4.0
    )
    # Bruit gaussien pour éviter un classifieur parfait
    latent = latent + rng.normal(0, 0.5, n_samples)
    y = (latent > np.median(latent) + 0.6).astype(np.int64)

    return X, y


def _next_version(now: datetime | None = None) -> str:
    """Construit une version lisible ``v1-YYYYMMDD-HHMMSS``."""
    now = now or datetime.now(UTC)
    return f"v1-{now.strftime('%Y%m%d-%H%M%S')}"


async def train_initial_model_task(
    session: AsyncSession, *,
    artifact_path: str = DEFAULT_ARTIFACT_PATH,
    n_samples: int = 5000,
) -> str:
    """Entraîne un nouveau modèle à partir du training set synthétique,
    sérialise l'artefact joblib et insère un row ``DropoutModelMetadata``.

    Retourne la version générée.
    """
    X, y = generate_synthetic_training_set(n_samples=n_samples)
    version = _next_version()
    model, metrics = train(X, y, version=version)
    model.save(artifact_path)

    meta = DropoutModelMetadata(
        id=generate_cuid(),
        version=version,
        trainedAt=datetime.now(UTC),
        metrics=metrics,
        artifactPath=artifact_path,
    )
    session.add(meta)
    await session.flush()
    return version
