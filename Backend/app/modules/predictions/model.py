"""Module 8 — Encapsulation du modèle ML de prédiction de décrochage.

Pourquoi Logistic Regression ?
------------------------------
* Interprétable : on peut extraire les coefficients par feature et expliquer
  un score à un parent ("votre enfant a un risque élevé surtout à cause de
  l'absentéisme").
* Rapide à entraîner (1s sur 5k samples) et à inférer (< 1ms par élève).
* Calibration probabiliste correcte par défaut (les valeurs ``predict_proba``
  sont des probabilités quasi-valides, sans avoir à appliquer Platt scaling).
* Pas de risque d'overfitting massif comme avec un XGBoost mal régularisé
  sur notre petit jeu synthétique de bootstrap.

XGBoost / réseaux de neurones / calibration isotonique → Module 8.1, quand
on aura des données labellisées réelles.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from app.modules.predictions.enums import DropoutRiskLevel
from app.modules.predictions.features import FEATURE_NAMES, features_to_vector

# Seuils MVP (cf. enum DropoutRiskLevel). Faciles à ajuster avec des tests AB.
RISK_LOW_MAX: float = 0.30
RISK_HIGH_MIN: float = 0.65


def risk_level_for_proba(proba: float) -> DropoutRiskLevel:
    """Convertit une probabilité [0,1] vers un niveau de risque discret."""
    if proba < RISK_LOW_MAX:
        return DropoutRiskLevel.LOW
    if proba > RISK_HIGH_MIN:
        return DropoutRiskLevel.HIGH
    return DropoutRiskLevel.MEDIUM


@dataclass
class DropoutModel:
    """Wrapper autour d'un sklearn LogisticRegression + scaler + version."""

    classifier: LogisticRegression
    scaler: StandardScaler
    version: str
    feature_names: tuple[str, ...] = FEATURE_NAMES

    # -------------------------------------------------------------------
    # Inference
    # -------------------------------------------------------------------
    def predict_proba(
        self, features: dict[str, float],
    ) -> tuple[float, DropoutRiskLevel]:
        """Retourne (probabilité d'abandon, niveau de risque)."""
        vec = np.array(features_to_vector(features), dtype=np.float64).reshape(1, -1)
        scaled = self.scaler.transform(vec)
        # Classe positive = 1 (dropout). LogisticRegression renvoie 2 colonnes.
        proba_pos = float(self.classifier.predict_proba(scaled)[0, 1])
        # Clamp défensif (sklearn renvoie déjà dans [0,1] mais on évite les
        # surprises de flottants type 1.0000000002).
        proba_pos = max(0.0, min(1.0, proba_pos))
        return proba_pos, risk_level_for_proba(proba_pos)

    # -------------------------------------------------------------------
    # Persistance
    # -------------------------------------------------------------------
    def save(self, path: str) -> None:
        """Sérialise (classifier, scaler, version, feature_names) en joblib.

        On crée le répertoire parent au passage pour éviter les FileNotFoundError
        en local quand /tmp/sub-dir n'existe pas.
        """
        parent = os.path.dirname(path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)
        joblib.dump(
            {
                "classifier": self.classifier,
                "scaler": self.scaler,
                "version": self.version,
                "feature_names": list(self.feature_names),
            },
            path,
        )

    @classmethod
    def load(cls, path: str) -> DropoutModel:
        payload: dict[str, Any] = joblib.load(path)
        return cls(
            classifier=payload["classifier"],
            scaler=payload["scaler"],
            version=payload["version"],
            feature_names=tuple(payload["feature_names"]),
        )


# ---------------------------------------------------------------------------
# Training helper
# ---------------------------------------------------------------------------
def train(
    X: np.ndarray, y: np.ndarray, *, version: str,
) -> tuple[DropoutModel, dict[str, float]]:
    """Entraîne un nouveau modèle. Retourne (model, metrics).

    On split 80/20 pour calculer accuracy + ROC AUC. C'est le minimum
    syndical ; Module 8.1 ajoutera un vrai cross-validation k-fold.
    """
    from sklearn.metrics import accuracy_score, roc_auc_score
    from sklearn.model_selection import train_test_split

    if X.shape[0] < 10:
        raise ValueError("Need at least 10 samples to train")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y if len(set(y)) > 1 else None,
    )

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    clf = LogisticRegression(
        max_iter=1000, class_weight="balanced", random_state=42,
    )
    clf.fit(X_train_scaled, y_train)

    y_pred = clf.predict(X_test_scaled)
    metrics: dict[str, float] = {
        "accuracy": float(accuracy_score(y_test, y_pred)),
        "n_train_samples": float(X_train.shape[0]),
        "n_test_samples": float(X_test.shape[0]),
    }
    # ROC AUC peut planter si y_test contient une seule classe (cas dégénéré).
    try:
        proba_pos = clf.predict_proba(X_test_scaled)[:, 1]
        metrics["roc_auc"] = float(roc_auc_score(y_test, proba_pos))
    except ValueError:
        metrics["roc_auc"] = float("nan")

    return DropoutModel(
        classifier=clf, scaler=scaler, version=version,
    ), metrics
