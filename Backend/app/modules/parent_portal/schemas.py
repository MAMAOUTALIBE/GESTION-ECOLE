"""Module 18 — Pydantic schemas pour le portail parent."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.modules.parent_portal.enums import (
    ParentChannel,
    WhatsAppStatus,
)


class WhatsAppWebhookEntry(BaseModel):
    """Payload INBOUND envoyé par WhatsApp Cloud API.

    Volontairement minimal pour le MVP : seuls les champs nécessaires à
    notre logique (numéro + texte + messageId). On accepte les keys
    additionnelles côté Cloud API en mode permissif.
    """

    model_config = ConfigDict(extra="allow")
    phoneNumber: str
    body: str
    messageId: str


class WhatsAppReplyOut(BaseModel):
    """Réponse renvoyée au provider après traitement d'un message."""

    model_config = ConfigDict(from_attributes=True)
    messageId: str
    intent: str
    reply: str
    status: WhatsAppStatus


class ChildSummary(BaseModel):
    """Résumé minimal d'un enfant pour l'affichage parent.

    Anonymisé : initiales seulement (pas de nom complet sur la page
    publique). ``className`` est facultatif (l'élève peut ne pas avoir
    de salle de classe assignée). ``lastAverage`` est ``None`` quand
    aucun bulletin n'a encore été émis.
    """

    initials: str
    className: str | None = None
    lastAverage: float | None = None


class ParentOverview(BaseModel):
    """Vue parent — exposée à la fois côté JSON et côté HTML."""

    model_config = ConfigDict(from_attributes=True)
    phoneHash: str
    childrenCount: int
    children: list[ChildSummary]
    upcomingEventNote: str | None = None


class ParentSessionOut(BaseModel):
    """Représentation d'une ParentSession (utile pour debug / admin)."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    phoneNumberHash: str
    channel: ParentChannel
    startedAt: datetime
    lastActivityAt: datetime
    expiresAt: datetime
