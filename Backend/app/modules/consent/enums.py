"""Module 5B — Constantes du consentement utilisateur.

``CURRENT_CONSENT_VERSION`` est la version actuellement requise. Toute
version d'utilisateur strictement antérieure (string-compare lexico)
implique ``needsAcceptance=True`` au prochain login. Si la politique
de confidentialité change matériellement, on incrémente la version
(format ISO date ``YYYY-MM-DD``).
"""
from __future__ import annotations

from typing import Final

CURRENT_CONSENT_VERSION: Final[str] = "2026-05-01"


__all__ = ["CURRENT_CONSENT_VERSION"]
