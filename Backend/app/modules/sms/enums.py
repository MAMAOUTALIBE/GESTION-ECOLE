"""Module 14 — Enums : direction et cycle de vie d'un SMS."""
from __future__ import annotations

from enum import StrEnum


class SmsDirection(StrEnum):
    OUTBOUND = "OUTBOUND"
    INBOUND = "INBOUND"


class SmsStatus(StrEnum):
    PENDING = "PENDING"     # accepté localement, pas encore envoyé au provider
    SENT = "SENT"           # provider a accusé réception (HTTP 2xx)
    DELIVERED = "DELIVERED" # callback de delivery report reçu
    FAILED = "FAILED"       # provider en erreur ou delivery report négatif
