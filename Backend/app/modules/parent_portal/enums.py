"""Module 18 — Enums du portail parent."""
from __future__ import annotations

from enum import StrEnum


class ParentChannel(StrEnum):
    """Canal d'entrée d'une session parent."""

    WHATSAPP = "WHATSAPP"
    USSD = "USSD"
    WEB = "WEB"


class WhatsAppDirection(StrEnum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class WhatsAppStatus(StrEnum):
    """Cycle de vie d'un message WhatsApp côté plateforme.

    RECEIVED  → enregistré, pas encore traité.
    PROCESSED → on a calculé une réponse (peut être suivie d'un SENT).
    SENT      → l'API Cloud WhatsApp a accepté la réponse.
    FAILED    → erreur côté provider ou parsing.
    """

    RECEIVED = "RECEIVED"
    PROCESSED = "PROCESSED"
    SENT = "SENT"
    FAILED = "FAILED"


class ParentIntent(StrEnum):
    """Intentions reconnues par le parser de messages libres.

    Volontairement étroit pour le MVP — toute extension passe par
    :data:`intent_parser._INTENT_KEYWORDS`.
    """

    MOYENNE = "MOYENNE"
    PRESENCE = "PRESENCE"
    BULLETIN = "BULLETIN"
    EVENEMENT = "EVENEMENT"
    AIDE = "AIDE"
