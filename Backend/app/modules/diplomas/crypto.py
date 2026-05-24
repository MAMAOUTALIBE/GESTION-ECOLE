"""Module 11 — Cryptographie Ed25519 pour les diplômes signés.

Pourquoi Ed25519 plutôt que RSA ?
---------------------------------
* RFC 8032 / RFC 8410 — courbe elliptique edwards25519, sécurité 128-bit.
* Signature ~64 bytes (RSA-2048 → 256 bytes).
* Clé publique 32 bytes — tient confortablement dans un QR.
* Signature déterministe → reproductible côté tests.
* Pas de paramètre à choisir (vs choix exposants RSA, courbe NIST…).
* Implémentation Python : :mod:`cryptography` (déjà dans deps).

Schéma de signature
-------------------
1. Le payload (dict métier) est canonicalisé en JSON via
   :func:`canonicalize_payload` — RFC 8785 simplifié : sorted keys, no
   whitespace, ``ensure_ascii=False``, valeurs primitives en lowercase
   (bool/null inchangés). Le résultat est un ``bytes`` reproductible.
2. On calcule ``SHA-256(canonical)`` → 32 bytes binaire.
3. On signe le SHA-256 avec Ed25519. La signature (64 bytes) est encodée
   en base64 standard pour stockage.
4. La vérification ré-applique 1-3 et compare la signature avec
   :meth:`Ed25519PublicKey.verify` (lève si invalide).

Gestion de la clé privée
------------------------
* Production : variable d'env ``DIPLOMA_SIGNING_KEY_PEM`` (PKCS8 PEM).
  Rotation = nouvelle valeur déployée + montée de version applicative.
  Les signatures historiques portent ``publicKeyFingerprint`` pour qu'on
  sache toujours quelle clé a signé (cf. Module 11.x roadmap).
* Dev / tests : si l'env var est absente, on génère un keypair
  éphémère (et on log un WARN clair). Cohérent à l'intérieur d'un
  process — on évite donc une dépendance dure à un HSM/Vault pour les
  intégrations locales.

Sécurité publique offline
-------------------------
La vérification ne dépend QUE de :

* la clé publique (32 bytes, peut être distribuée sur un site .gouv),
* le payload retourné par l'API (ou recomposé à la main depuis le PDF),
* la signature stockée en DB et renvoyée par l'API.

Un agent peut donc vérifier hors-ligne sans interroger la base, à
condition d'avoir la clé publique de référence.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from loguru import logger

_ENV_VAR = "DIPLOMA_SIGNING_KEY_PEM"

# Cache process-local : la clé privée est chargée 1× et réutilisée. Threading
# Lock pour éviter de générer deux keypair éphémères concurrents en dev.
_KEY_LOCK = threading.Lock()
_CACHED_KEY: Ed25519PrivateKey | None = None
_CACHED_FINGERPRINT: str | None = None


def _public_key_fingerprint(public_key: Ed25519PublicKey) -> str:
    """SHA-256 hex tronqué (16 bytes → 32 hex chars) des bytes raw de la clé
    publique. Permet de tracer rapidement quelle clé a signé un diplôme
    sans avoir à stocker la clé publique entière à côté de chaque ligne.
    """
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()[:32]


def load_or_generate_signing_key() -> Ed25519PrivateKey:
    """Charge la clé privée depuis l'env ou génère un keypair éphémère.

    * Si ``DIPLOMA_SIGNING_KEY_PEM`` est définie : décode la clé PKCS8 PEM
      (lèvera ``ValueError`` si le contenu est mal formé — c'est délibéré,
      l'opérateur DOIT corriger le secret).
    * Sinon : génère un keypair éphémère et log un WARN clair. La clé
      restera stable pendant la durée de vie du process (cache local) pour
      que les tests d'intégration soient déterministes.

    Le résultat est mis en cache au niveau du module pour éviter de
    re-générer/re-parser à chaque appel.
    """
    global _CACHED_KEY, _CACHED_FINGERPRINT
    if _CACHED_KEY is not None:
        return _CACHED_KEY

    with _KEY_LOCK:
        if _CACHED_KEY is not None:
            return _CACHED_KEY

        pem = os.environ.get(_ENV_VAR)
        if pem:
            try:
                key = serialization.load_pem_private_key(
                    pem.encode("utf-8"),
                    password=None,
                )
            except Exception as exc:
                # On ne masque pas l'erreur : si la clé est mal formée, on
                # ne veut SURTOUT pas tomber silencieusement sur un keypair
                # éphémère qui invaliderait toutes les signatures stockées.
                raise ValueError(
                    f"{_ENV_VAR} contient une clé invalide : {exc}"
                ) from exc
            if not isinstance(key, Ed25519PrivateKey):
                raise ValueError(
                    f"{_ENV_VAR} doit être une clé Ed25519 (PKCS8 PEM)."
                )
            logger.info(
                "diplomas.crypto: clé Ed25519 chargée depuis l'env "
                "(fingerprint={})",
                _public_key_fingerprint(key.public_key()),
            )
        else:
            key = Ed25519PrivateKey.generate()
            logger.warning(
                "diplomas.crypto: aucune {} fournie — génération d'un "
                "keypair Ed25519 éphémère (mode dev/test uniquement). "
                "Fingerprint={}",
                _ENV_VAR,
                _public_key_fingerprint(key.public_key()),
            )

        _CACHED_KEY = key
        _CACHED_FINGERPRINT = _public_key_fingerprint(key.public_key())
        return _CACHED_KEY


def reset_signing_key_cache() -> None:
    """Helper de test : oublie la clé cachée pour forcer un re-chargement."""
    global _CACHED_KEY, _CACHED_FINGERPRINT
    with _KEY_LOCK:
        _CACHED_KEY = None
        _CACHED_FINGERPRINT = None


def canonicalize_payload(payload: dict[str, Any]) -> bytes:
    """Sérialise un dict en JSON canonique (RFC 8785 simplifié).

    Garanties :

    * Clés triées récursivement (``sort_keys=True``).
    * Aucun espace : séparateurs ``,`` / ``:``.
    * UTF-8 préservé (``ensure_ascii=False``) — les noms d'élèves
      contiennent des caractères accentués ; ne pas escaper évite des
      divergences inutiles entre signataires et vérificateurs.
    * Floats sérialisés via ``json.dumps`` (pas d'arrondi custom). En
      pratique le service amont arrondit les scores à 2 décimales avant
      signature.

    Renvoie ``bytes`` (UTF-8) pour que le hash SHA-256 soit calculé
    directement sans étape de ré-encodage.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def compute_payload_sha256(payload: dict[str, Any]) -> str:
    """SHA-256 hex (64 chars) du payload canonicalisé."""
    canonical = canonicalize_payload(payload)
    return hashlib.sha256(canonical).hexdigest()


def sign_payload(payload: dict[str, Any]) -> tuple[str, str]:
    """Signe le payload et retourne ``(signature_b64, public_key_fingerprint)``.

    La signature est calculée sur le SHA-256 binaire du payload
    canonicalisé (32 bytes), pas sur le payload brut. Cette indirection
    permet à un vérificateur "léger" de stocker seulement le hash + la
    signature, sans avoir à conserver tout le payload original.
    """
    key = load_or_generate_signing_key()
    canonical = canonicalize_payload(payload)
    digest = hashlib.sha256(canonical).digest()
    signature_bytes = key.sign(digest)
    signature_b64 = base64.b64encode(signature_bytes).decode("ascii")
    fingerprint = _CACHED_FINGERPRINT or _public_key_fingerprint(key.public_key())
    return signature_b64, fingerprint


def get_public_key_pem() -> str:
    """Renvoie la clé publique au format PEM (PKIX).

    Utilisable par un vérificateur externe pour valider hors-ligne. À
    exposer côté ``/api/diplomas/public-key`` (Module 11.x) ou hardcodé
    dans une app mobile auditrice.
    """
    key = load_or_generate_signing_key()
    pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return pem.decode("ascii")


def verify_signature(
    payload: dict[str, Any],
    signature_b64: str,
    public_key_pem: str,
) -> bool:
    """Vérifie une signature Ed25519.

    Recompute le SHA-256 du payload canonicalisé puis appelle
    ``public_key.verify(signature, digest)``. Renvoie ``True`` si la
    signature est valide, ``False`` sinon.

    Lève sur une clé publique malformée (c'est une erreur de
    l'appelant — il fournit un PEM corrompu) ; toute exception interne à
    la vérification cryptographique (``InvalidSignature``) est attrapée et
    renvoyée sous forme de ``False``.
    """
    try:
        public_key_obj = serialization.load_pem_public_key(
            public_key_pem.encode("ascii"),
        )
    except Exception as exc:
        raise ValueError(f"Clé publique PEM invalide : {exc}") from exc
    if not isinstance(public_key_obj, Ed25519PublicKey):
        raise ValueError("La clé publique fournie n'est pas Ed25519.")

    try:
        signature_bytes = base64.b64decode(signature_b64, validate=True)
    except Exception:
        return False

    canonical = canonicalize_payload(payload)
    digest = hashlib.sha256(canonical).digest()
    try:
        public_key_obj.verify(signature_bytes, digest)
    except InvalidSignature:
        return False
    return True


__all__ = [
    "canonicalize_payload",
    "compute_payload_sha256",
    "get_public_key_pem",
    "load_or_generate_signing_key",
    "reset_signing_key_cache",
    "sign_payload",
    "verify_signature",
]
