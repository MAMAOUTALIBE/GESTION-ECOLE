"""Module 11 — Génération des numéros de série publics des diplômes.

Format
------
``{TYPE}-{YEAR}-{8HEX}``

* ``TYPE``  : ``CEPE`` / ``BEPC`` / ``CFEE`` (cf. :class:`DiplomaType`).
* ``YEAR``  : année civile sur 4 chiffres (millésime du diplôme).
* ``8HEX``  : 8 caractères hexadécimaux MAJUSCULES tirés via
  :func:`secrets.token_hex` — entropie 32 bits (4.3 × 10⁹ valeurs). Sur
  l'horizon prévisible (≤ 1 M diplômes / an), le risque de collision est
  ~1/4300 par diplôme — la contrainte ``UNIQUE`` côté DB attrape le cas
  rare et le service ré-essaie (voir DiplomaService).

Exemple : ``CEPE-2026-3F2A91BC``.

Pourquoi pas un cuid ?
----------------------
Les cuids font 25 caractères et sont peu lisibles oralement. Le serial
diplôme est destiné à être imprimé en gros sur le PDF + lu au téléphone
quand un employeur veut vérifier — il faut un format court, anti-typo
(majuscules + chiffres uniquement). Le préfixe TYPE/YEAR donne aussi un
contexte humain immédiat ("CEPE-2026-..." = CEPE millésime 2026).
"""
from __future__ import annotations

import secrets


def generate_serial(diploma_type: str, year: int) -> str:
    """Génère un serial diplôme du format ``{TYPE}-{YEAR}-{8HEX}``.

    L'appelant DOIT vérifier l'unicité (contrainte DB ou pré-check). En
    cas de collision, ré-appeler cette fonction suffit — la randomness
    est tirée de :mod:`secrets` (cryptographiquement sûre).
    """
    suffix = secrets.token_hex(4).upper()  # 4 bytes → 8 hex chars
    return f"{diploma_type}-{year}-{suffix}"


__all__ = ["generate_serial"]
