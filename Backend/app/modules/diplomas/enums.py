"""Module 11 — Diplômes signés : enums dédiés.

Deux enums :

* :class:`DiplomaType` — type de diplôme national guinéen (CEPE, BEPC, CFEE).
  La liste est volontairement courte : seuls les diplômes officiels reconnus
  par le MEN sont émis par la plateforme. Ajouter un type ici impose une
  migration alembic (valeur native de l'enum Postgres).
* :class:`DiplomaStatus` — cycle de vie d'un diplôme : DRAFT (préparé mais
  pas encore signé), ISSUED (signé numériquement, vérifiable publiquement),
  REVOKED (annulé après émission — fraude, erreur académique…). Un
  diplôme REVOKED n'est PAS supprimé : la vérification publique renvoie
  explicitement `status: REVOKED` avec la raison, pour que les recruteurs
  voient l'historique réel.
"""
from enum import StrEnum


class DiplomaType(StrEnum):
    """Diplômes nationaux guinéens couverts par Module 11.

    * ``CEPE`` — Certificat d'Études Primaires Élémentaires (fin CM2).
    * ``BEPC`` — Brevet d'Études du Premier Cycle (fin 3ème).
    * ``CFEE`` — Certificat de Fin d'Études Élémentaires (équivalent CEPE
      dans certaines régions, conservé pour compat historique).
    """

    CEPE = "CEPE"
    BEPC = "BEPC"
    CFEE = "CFEE"


class DiplomaStatus(StrEnum):
    """Cycle de vie d'un diplôme.

    * ``DRAFT`` — préparé mais pas encore signé. Modifiable. Pas exposé
      publiquement par l'endpoint de vérification.
    * ``ISSUED`` — signé numériquement (Ed25519), gravé. La vérification
      publique le retourne avec ``status: VALID`` si la signature recompute
      bien.
    * ``REVOKED`` — annulé après émission. La vérification renvoie
      ``status: REVOKED`` avec la raison. Décision réservée à un
      ``NATIONAL_ADMIN``.
    """

    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    REVOKED = "REVOKED"
