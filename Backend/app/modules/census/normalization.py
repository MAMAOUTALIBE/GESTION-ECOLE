"""Normalisation des inputs census (noms, téléphones, dates de naissance).

Ce module est SANS dépendance DB — pur calcul. Les règles métier viennent du
contexte Guinée :

* Noms : on respecte les particules ("Diallo", "Ba", "Sow"), les apostrophes
  ("N'Diaye") et les tirets ("Sidi-Bah"). On applique Unicode NFC + Title Case
  par segment (mots séparés par espaces, apostrophes ou tirets), tout en
  collapsant les espaces redondants.
* Téléphones : format E.164 strict pour la Guinée (+224 + 9 chiffres). Les
  numéros mobiles valides commencent par 6 (préfixes Orange/MTN/Cellcom). On
  accepte les formats locaux (`622123456`), internationaux (`+224622123456`),
  préfixés `00224...`, espacés (`+224 622 12 34 56`). On rejette tout numéro
  hors Guinée — la plateforme ne gère que les tuteurs en Guinée.
* Birthdate : on vérifie la cohérence âge/niveau scolaire à la date de la
  rentrée (1er octobre de l'année académique courante). Tolérance ±1 an pour
  le primaire (CP à CM2) pour absorber les redoublements/retards courants.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

# ---------------------------------------------------------------------------
# Noms
# ---------------------------------------------------------------------------
# Séparateurs internes qu'on PRÉSERVE (vs. espaces qu'on collapse) :
# apostrophes (typographique ou ASCII), tirets, espaces.
_NAME_SEPARATORS = re.compile(r"([\-\s'’])")  # noqa: RUF001
_MULTI_SPACE = re.compile(r"\s+")


def normalize_name(raw: str) -> str:
    """Normalise un nom ou prénom.

    Pipeline :
    1. Unicode NFC pour assurer une forme canonique des accents.
    2. Strip + collapse des espaces multiples.
    3. Title Case par segment, en respectant ', - et les espaces comme
       séparateurs. Les apostrophes typographiques (U+2019) sont normalisées
       en ASCII (').

    Examples
    --------
    >>> normalize_name("AÏSSATOU diallo")
    'Aïssatou Diallo'
    >>> normalize_name("n'diaye-sow")
    "N'Diaye-Sow"
    >>> normalize_name("  mamadou  bah  ")
    'Mamadou Bah'
    """
    if not isinstance(raw, str):
        raise TypeError("normalize_name attend une str")
    text = unicodedata.normalize("NFC", raw).strip()
    if not text:
        raise ValueError("Nom vide après normalisation")
    # Normalise l'apostrophe typographique en ASCII pour cohérence d'affichage.
    text = text.replace("’", "'")  # noqa: RUF001
    # Collapse les espaces multiples
    text = _MULTI_SPACE.sub(" ", text)

    # Split en gardant les séparateurs ; capitalize chaque segment alpha
    parts = _NAME_SEPARATORS.split(text)
    result: list[str] = []
    for part in parts:
        if not part:
            continue
        if part in {"-", "'", "’", " "}:  # noqa: RUF001
            result.append(
                "-" if part == "-" else ("'" if part in {"'", "’"} else " ")  # noqa: RUF001
            )
        else:
            # capitalize() force le premier alpha en majuscule + reste lower.
            result.append(part.capitalize())
    return "".join(result)


# ---------------------------------------------------------------------------
# Téléphones Guinée
# ---------------------------------------------------------------------------
_GUINEA_CC = "+224"
_VALID_MOBILE_PREFIXES = ("6",)  # tous les mobiles Guinée commencent par 6


def normalize_phone_guinea(raw: str | None) -> str | None:
    """Normalise un numéro de téléphone guinéen au format E.164.

    Accepte les formats suivants (espaces et tirets ignorés) :

    * ``622123456`` (local, 9 chiffres)
    * ``+224622123456`` (E.164)
    * ``00224622123456`` (international avec 00)
    * ``224622123456`` (international sans +)
    * ``+224 622 12 34 56`` (espacé)

    Rejette (raise ``ValueError``) :

    * Préfixes pays autres que +224 (ex: +33, +1, etc.)
    * Numéros qui ne commencent pas par 6 après le code pays (les fixes ne
      sont pas pris en charge pour les tuteurs — la plateforme cible le SMS)
    * Numéros de longueur incorrecte (≠ 9 chiffres après +224)

    Returns
    -------
    str | None
        Le numéro E.164 ``+224XXXXXXXXX`` (13 chars) ou ``None`` si l'entrée
        est ``None`` ou vide après strip.
    """
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None

    # Retire espaces / tirets / parenthèses / points internes
    compact = re.sub(r"[\s\-().]", "", cleaned)

    # Détection du préfixe pays
    if compact.startswith("+"):
        # Doit commencer par +224
        if not compact.startswith(_GUINEA_CC):
            raise ValueError(
                f"Numéro hors Guinée non supporté : {raw!r} (code pays attendu +224)"
            )
        local = compact[len(_GUINEA_CC):]
    elif compact.startswith("00"):
        # 00224... → +224...
        if not compact.startswith("00224"):
            raise ValueError(
                f"Numéro hors Guinée non supporté : {raw!r} (code pays attendu 00224)"
            )
        local = compact[len("00224"):]
    elif compact.startswith("224") and len(compact) == 12:
        # 224XXXXXXXXX → +224XXXXXXXXX
        local = compact[3:]
    else:
        # Numéro local
        local = compact

    # Validation finale : exactement 9 chiffres, commençant par un préfixe mobile
    if not local.isdigit():
        raise ValueError(f"Numéro invalide (caractères non numériques) : {raw!r}")
    if len(local) != 9:
        raise ValueError(
            f"Numéro invalide (longueur {len(local)} ≠ 9 chiffres après code pays) : {raw!r}"
        )
    if not local.startswith(_VALID_MOBILE_PREFIXES):
        raise ValueError(
            f"Préfixe mobile Guinée invalide : {raw!r} (attendu 6XXXXXXXX)"
        )

    return f"{_GUINEA_CC}{local}"


# ---------------------------------------------------------------------------
# Birthdate ↔ niveau scolaire
# ---------------------------------------------------------------------------
# Mapping niveau → âge attendu à la rentrée (1er octobre).
# Tolérance par défaut : ±1 an pour le primaire (redoublements / retards
# fréquents). Pour la maternelle on a une plage 3-5 ans.
_LEVEL_AGES: dict[str, tuple[int, int]] = {
    # Maternelle : 3 à 5 ans
    "MATERNELLE": (3, 5),
    "MAT": (3, 5),
    "PS": (3, 4),
    "MS": (4, 5),
    "GS": (5, 6),
    # Primaire : 6 à 11 ans avec ±1 an de tolérance
    "CP": (5, 7),
    "CP1": (5, 7),
    "CP2": (6, 8),
    "CE1": (6, 8),
    "CE2": (7, 9),
    "CM1": (8, 10),
    "CM2": (9, 11),
}

# Plage globale acceptable quand le niveau n'est pas fourni.
_ABSOLUTE_MIN_AGE = 3
_ABSOLUTE_MAX_AGE = 16


def _start_of_school_year(reference: date | None = None) -> date:
    """Retourne le 1er octobre de l'année académique en cours.

    L'année académique guinéenne commence en octobre. Si la date de référence
    est avant le 1er octobre, l'année académique est ``year - 1`` ; sinon
    c'est ``year``.
    """
    today = reference or date.today()
    if today.month >= 10:
        return date(today.year, 10, 1)
    return date(today.year - 1, 10, 1)


def _years_between(start: date, end: date) -> int:
    """Âge en années pleines de ``start`` à ``end`` (sans heures/timezone)."""
    age = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        age -= 1
    return age


def validate_birthdate_for_classroom(
    birthdate: date,
    classroom_level: str | None,
    *,
    reference_date: date | None = None,
) -> tuple[bool, str | None]:
    """Vérifie que ``birthdate`` est cohérente avec ``classroom_level``.

    Si ``classroom_level`` est ``None``, on vérifie juste la plage absolue
    (3 à 16 ans à la rentrée).

    Returns
    -------
    tuple[bool, str | None]
        ``(True, None)`` si OK, ``(False, "raison")`` sinon.
    """
    start_of_year = _start_of_school_year(reference_date)
    if birthdate >= start_of_year:
        return (False, "Date de naissance dans le futur (postérieure à la rentrée)")
    age = _years_between(birthdate, start_of_year)

    if classroom_level is None or classroom_level == "":
        if age < _ABSOLUTE_MIN_AGE:
            return (False, f"Âge trop bas ({age} ans à la rentrée, min {_ABSOLUTE_MIN_AGE})")
        if age > _ABSOLUTE_MAX_AGE:
            return (False, f"Âge trop élevé ({age} ans à la rentrée, max {_ABSOLUTE_MAX_AGE})")
        return (True, None)

    key = classroom_level.upper().strip()
    expected = _LEVEL_AGES.get(key)
    if expected is None:
        # Niveau inconnu : on tombe sur la plage globale.
        if age < _ABSOLUTE_MIN_AGE or age > _ABSOLUTE_MAX_AGE:
            return (
                False,
                f"Âge {age} ans hors plage [{_ABSOLUTE_MIN_AGE}-{_ABSOLUTE_MAX_AGE}] "
                f"pour niveau inconnu '{classroom_level}'",
            )
        return (True, None)

    low, high = expected
    if age < low or age > high:
        return (
            False,
            f"Âge incohérent : {age} ans à la rentrée pour {classroom_level} "
            f"(attendu {low}-{high} ans)",
        )
    return (True, None)
