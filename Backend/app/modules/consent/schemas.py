"""Module 5B — Schémas Pydantic du consentement utilisateur."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AcceptConsentRequest(BaseModel):
    """Payload d'acceptation du consentement.

    ``consentVersion`` doit correspondre à la version actuellement
    requise par le backend (cf. ``CURRENT_CONSENT_VERSION``). Si le
    client envoie une version obsolète, le service la rejette (409)
    pour éviter qu'un client legacy "accepte" une version qui ne
    couvre plus toutes les clauses.
    """

    consentVersion: str = Field(..., min_length=1, max_length=20)


class ConsentStatus(BaseModel):
    """Représentation API du statut consentement pour l'utilisateur courant.

    * ``version`` — dernière version acceptée par l'utilisateur (``None``
      si aucun consentement enregistré).
    * ``acceptedAt`` — date/heure de la dernière acceptation.
    * ``needsAcceptance`` — vrai si l'utilisateur doit (re)consentir.
    * ``currentRequiredVersion`` — la version actuellement requise par
      la plateforme (cf. ``CURRENT_CONSENT_VERSION``).
    """

    model_config = ConfigDict(from_attributes=True)

    version: str | None = None
    acceptedAt: datetime | None = None
    needsAcceptance: bool
    currentRequiredVersion: str


__all__ = ["AcceptConsentRequest", "ConsentStatus"]
