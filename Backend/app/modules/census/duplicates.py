"""Service pur de scoring de doublons (pas de DB).

Rationale du modèle de score
----------------------------
On combine 6 signaux orthogonaux pondérés. Les poids viennent des contraintes
opérationnelles d'un état civil scolaire en Guinée :

* **lastName (0.30)** : le nom de famille est le signal le plus stable, mais
  les variations orthographiques sont fréquentes (« Diallo » vs « Jallow »,
  « Conde » vs « Condé »). Jaro-Winkler est le bon outil — il privilégie les
  préfixes communs ET tolère la substitution.
* **firstName (0.20)** : moins stable que le nom (Aïssatou ↔ Aishatou), mais
  toujours discriminant. Même métrique.
* **birthDate (0.25)** : signal très fort si exact, mais les déclarations
  approximatives sont la norme dans le primaire — d'où la tolérance ±1 jour
  (saisie inversée jour/mois) et ±30 jours (approximation village/mois).
* **guardianPhone (0.15)** : très discriminant — un même numéro de tuteur sur
  deux fiches « Aminata » différentes est quasi toujours un doublon. Mais
  facultatif (beaucoup de tuteurs partagent un téléphone familial).
* **gender (0.05)** : peu discriminant en soi mais valeur ajoutée comme bonus.
* **schoolId (0.05)** : un doublon dans la même école est BEAUCOUP plus
  probable qu'entre deux écoles éloignées. Petit bonus.

Total = 1.0. Seuils :
* HIGH ≥ 0.85   → blocage à la création (force=true requis)
* MEDIUM ≥ 0.65 → avertissement seulement
* LOW < 0.65    → non-doublon

Pas de ML / pas d'embeddings : on veut un score auditable, interprétable et
re-jouable hors-ligne. Tout dépassement de seuil doit être justifiable par un
inspecteur.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from rapidfuzz.distance import JaroWinkler

# Poids des features (somme = 1.0)
WEIGHTS = {
    "lastName": 0.30,
    "firstName": 0.20,
    "birthDate": 0.25,
    "guardianPhone": 0.15,
    "gender": 0.05,
    "schoolId": 0.05,
}

THRESHOLD_HIGH = 0.85
THRESHOLD_MEDIUM = 0.65

Classification = Literal["HIGH", "MEDIUM", "LOW"]


def _name_score(a: str | None, b: str | None) -> float:
    """Similarité Jaro-Winkler normalisée (case + strip insensible)."""
    if not a or not b:
        return 0.0
    return JaroWinkler.normalized_similarity(a.strip().lower(), b.strip().lower())


def _date_score(a: Any, b: Any) -> float:
    """Score date : 1.0 si exact, 0.8 si ±1 jour, 0.4 si ±30 jours, sinon 0.0.

    Accepte ``date`` et ``datetime`` (la composante temps est ignorée).
    """
    if a is None or b is None:
        return 0.0
    da = a.date() if isinstance(a, datetime) else a
    db = b.date() if isinstance(b, datetime) else b
    if not isinstance(da, date) or not isinstance(db, date):
        return 0.0
    delta = abs((da - db).days)
    if delta == 0:
        return 1.0
    if delta <= 1:
        return 0.8
    if delta <= 30:
        return 0.4
    return 0.0


def _exact_score(a: Any, b: Any) -> float:
    """1.0 si égaux (str-comparé après normalisation simple), sinon 0.0."""
    if a is None or b is None:
        return 0.0
    return 1.0 if str(a).strip() == str(b).strip() else 0.0


def compute_similarity_score(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Calcule le score composite et les détails par feature.

    Parameters
    ----------
    a, b : dict
        Dicts avec les clés ``firstName``, ``lastName``, ``birthDate``,
        ``guardianPhone``, ``gender``, ``schoolId``. Toute clé absente vaut
        ``None``.

    Returns
    -------
    dict
        ``{"score": float, "matchedFields": [str, ...], "features": {...},
        "activeWeight": float}``. Le score est dans ``[0.0, 1.0]``. Une
        feature est listée dans ``matchedFields`` si son score >= 0.8.

    Notes
    -----
    **Renormalisation dynamique** : si une feature est ``None`` des DEUX
    côtés (a ET b), son poids est exclu du dénominateur. Sans cela, l'absence
    de ``birthDate`` et ``guardianPhone`` (40% des poids) ferait plafonner le
    score à 60 % même pour deux fiches strictement identiques sur tous les
    autres champs — produisant un faux négatif (cf. C-1 review Module 2).
    Quand au moins un des deux candidats a une valeur, la feature reste
    active (son score sera 0.0 si l'autre est absent, ce qui pénalise
    légitimement le candidat).
    """
    features = {
        "lastName": _name_score(a.get("lastName"), b.get("lastName")),
        "firstName": _name_score(a.get("firstName"), b.get("firstName")),
        "birthDate": _date_score(a.get("birthDate"), b.get("birthDate")),
        "guardianPhone": _exact_score(a.get("guardianPhone"), b.get("guardianPhone")),
        "gender": _exact_score(a.get("gender"), b.get("gender")),
        "schoolId": _exact_score(a.get("schoolId"), b.get("schoolId")),
    }
    # Renormalisation : on ignore les features où les deux côtés sont None.
    active_weight = 0.0
    raw_score = 0.0
    for key, weight in WEIGHTS.items():
        if a.get(key) is None and b.get(key) is None:
            continue
        active_weight += weight
        raw_score += features[key] * weight
    normalized = 0.0 if active_weight <= 0.0 else raw_score / active_weight
    matched = sorted([k for k, v in features.items() if v >= 0.8])
    return {
        "score": round(normalized, 4),
        "matchedFields": matched,
        "features": features,
        "activeWeight": round(active_weight, 4),
    }


def classify_score(score: float) -> Classification:
    """Classe un score brut en ``HIGH`` / ``MEDIUM`` / ``LOW``."""
    if score >= THRESHOLD_HIGH:
        return "HIGH"
    if score >= THRESHOLD_MEDIUM:
        return "MEDIUM"
    return "LOW"


# Seuils du fallback exact-match (cf. C-1 review Module 2). Quand le scoring
# fuzzy reste sous LOW pour cause d'absence de signaux discriminants
# (birthDate/phone), un appariement très fort sur les noms + même école + même
# genre doit FORCER une classification minimum, sinon le service ne propose
# jamais d'avertissement à l'agent de saisie.
_LASTNAME_EXACT_THRESHOLD = 0.9
_FIRSTNAME_EXACT_THRESHOLD = 0.7


def force_classification_floor(
    a: dict[str, Any], b: dict[str, Any], current: Classification
) -> Classification:
    """Garantit un plancher de classification sur les appariements évidents.

    Règles (cumulatives) :

    * ``lastName_score >= 0.9 AND firstName_score >= 0.7 AND schoolId match
      AND gender match`` → plancher MEDIUM.
    * Si en plus ``birthDate`` est exactement le même → plancher HIGH.

    On ne *redescend* jamais : si ``current`` est déjà HIGH on retourne HIGH.
    """
    last_s = _name_score(a.get("lastName"), b.get("lastName"))
    first_s = _name_score(a.get("firstName"), b.get("firstName"))
    same_school = (
        a.get("schoolId") is not None
        and a.get("schoolId") == b.get("schoolId")
    )
    same_gender = (
        a.get("gender") is not None
        and str(a.get("gender")) == str(b.get("gender"))
    )
    if not (
        last_s >= _LASTNAME_EXACT_THRESHOLD
        and first_s >= _FIRSTNAME_EXACT_THRESHOLD
        and same_school
        and same_gender
    ):
        return current

    floor: Classification = "MEDIUM"
    if _date_score(a.get("birthDate"), b.get("birthDate")) >= 1.0:
        floor = "HIGH"

    # On retourne le max entre current et floor.
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    return current if order[current] >= order[floor] else floor
