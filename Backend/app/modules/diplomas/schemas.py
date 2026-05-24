"""Module 11 — Schemas Pydantic pour les diplômes signés.

Trois familles de schemas :

* **Issue** : payload envoyé par un admin pour émettre un diplôme.
* **Read** : représentation interne (admin / school director) — expose
  les champs sensibles (studentId, signature complète, hashes).
* **Verification** : représentation PUBLIQUE (sans auth) — on n'expose
  AUCUN identifiant interne et seulement les informations strictement
  nécessaires pour identifier humainement le titulaire et l'école.

⚠ La distinction Read / Verification est critique : un endpoint
public qui leak l'``id`` cuid d'un élève donne un vecteur d'énumération
(et viole la conformité avec les principes de minimisation des données).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.diplomas.enums import DiplomaStatus, DiplomaType


# ---------------------------------------------------------------------------
# Issue (admin)
# ---------------------------------------------------------------------------
class DiplomaIssueRequest(BaseModel):
    """Payload pour POST /api/diplomas (émission)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    studentId: str = Field(min_length=1, max_length=30)
    diplomaType: DiplomaType
    schoolId: str = Field(min_length=1, max_length=30)
    academicYearId: str | None = Field(default=None, max_length=30)
    examCenter: str | None = Field(default=None, max_length=200)
    score: float | None = Field(default=None, ge=0.0, le=20.0)
    mention: str | None = Field(default=None, max_length=40)


# ---------------------------------------------------------------------------
# Internal read (school director, admin)
# ---------------------------------------------------------------------------
class DiplomaRead(BaseModel):
    """Vue interne d'un diplôme (admin / school director).

    Expose la signature complète et le hash payload pour permettre une
    vérification offline ou un audit. Pas exposée publiquement.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    serial: str
    studentId: str
    schoolId: str
    academicYearId: str | None
    diplomaType: DiplomaType
    examCenter: str | None
    score: float | None
    mention: str | None
    status: DiplomaStatus
    issuedAt: datetime | None
    signedAt: datetime | None
    revokedAt: datetime | None
    revokedReason: str | None
    payloadSha256: str | None
    signature: str | None
    publicKeyFingerprint: str | None
    pdfS3Key: str | None
    createdAt: datetime
    updatedAt: datetime


class DiplomaListResponse(BaseModel):
    items: list[DiplomaRead]
    total: int


# ---------------------------------------------------------------------------
# Revocation (national admin)
# ---------------------------------------------------------------------------
class DiplomaRevokeRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    reason: str = Field(min_length=3, max_length=500)


# ---------------------------------------------------------------------------
# Public verification (sans auth)
# ---------------------------------------------------------------------------
class PublicStudentInfo(BaseModel):
    """Informations minimalistes sur l'élève — exposées publiquement.

    On expose ``firstName`` + ``lastNameInitial`` (1 lettre + ``.``) :
    suffisant pour qu'un humain confirme "oui c'est bien telle personne",
    insuffisant pour identifier sans ambiguïté à grande échelle. Pas
    de date de naissance, pas de nom du tuteur, pas d'ID interne.
    """

    firstName: str
    lastNameInitial: str
    schoolName: str | None = None


class DiplomaVerification(BaseModel):
    """Réponse publique de GET /api/diplomas/verify/{serial}.

    Trois ``status`` possibles :

    * ``VALID``    — diplôme ISSUED, signature recompute correctement.
    * ``REVOKED``  — diplôme révoqué après émission ; affiche ``revokedReason``.
    * ``NOT_FOUND`` — serial inconnu (anti-énumération : on renvoie 404 mais
      structurée pour que le frontend distingue clairement le cas).

    On expose la ``signature`` et le ``payloadSha256`` pour que des
    vérificateurs externes (ex. app mobile auditrice) puissent recalculer
    et confirmer hors-ligne avec la clé publique distribuée.
    """

    status: Literal["VALID", "REVOKED", "NOT_FOUND"]
    serial: str
    diplomaType: DiplomaType | None = None
    issuedAt: datetime | None = None
    student: PublicStudentInfo | None = None
    score: float | None = None
    mention: str | None = None
    examCenter: str | None = None
    revokedAt: datetime | None = None
    revokedReason: str | None = None
    payloadSha256: str | None = None
    signature: str | None = None
    publicKeyFingerprint: str | None = None
    # Payload canonical (sans champs internes) que l'auditeur peut
    # recanonicaliser pour recalculer le hash et vérifier la signature
    # avec la clé publique. C'est exactement le dict qui a été signé.
    payload: dict[str, Any] | None = None


__all__ = [
    "DiplomaIssueRequest",
    "DiplomaListResponse",
    "DiplomaRead",
    "DiplomaRevokeRequest",
    "DiplomaVerification",
    "PublicStudentInfo",
]
