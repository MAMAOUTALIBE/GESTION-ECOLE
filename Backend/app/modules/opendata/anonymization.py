"""Module 12 — Anonymisation des datasets publics.

Deux primitives :

* :func:`hash_ip` — SHA-256(salt || ip) → hex 64 chars. Le salt est lu
  dans la variable d'environnement ``OPENDATA_IP_HASH_SALT`` (avec fallback
  sur ``settings.jwt_secret`` pour garantir qu'on n'écrit jamais d'IP en
  clair même si l'opérateur oublie de définir le salt). Le hash est
  déterministe pour permettre des agrégats "unique downloaders" sans
  jamais stocker l'IP réelle.
* :func:`is_anonymous` — garde-fou côté tests / service : refuse tout
  record contenant un champ dont le NOM ressemble à un PII (id technique,
  prénom/nom, téléphone, email, date de naissance). Aide à empêcher une
  régression involontaire qui leakerait des données nominatives.

Pourquoi déterministe ?
-----------------------
La traçabilité publique exige qu'on puisse compter "X visiteurs uniques
sur 30 jours" sans révéler qui ils sont. Un hash salé déterministe permet
exactement ça : on peut comparer deux hashes pour savoir si c'est le
même appelant, mais on ne peut PAS remonter à l'IP source sans connaître
le salt (qui ne quitte jamais le backend).

Pourquoi pas un HMAC ?
----------------------
HMAC serait équivalent ici (clé == salt, message == IP), mais on garde
``hashlib.sha256(salt+ip)`` pour rester compréhensible par les non-
cryptographes qui auditent ce module. La sécurité est équivalente tant
que le salt fait >= 16 octets et n'est jamais loggé.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Final

from app.core.config import settings

# Nom de la variable d'environnement pour le salt du hash IP.
_SALT_ENV_VAR: Final = "OPENDATA_IP_HASH_SALT"

# Fallback : on dérive du JWT secret pour ne jamais stocker une IP en clair
# même si l'opérateur a oublié de définir le salt dédié. Le préfixe
# distingue l'usage (un attaquant qui obtient le hash IP ne peut pas
# l'utiliser pour casser un JWT, et inversement).
_FALLBACK_PREFIX: Final = b"opendata:ip-hash:v1:"


def _resolved_salt() -> bytes:
    """Renvoie le salt à utiliser (env var prioritaire, fallback JWT secret).

    Lu à chaque appel (et pas cache module-level) pour que les tests
    puissent monkeypatcher ``os.environ`` sans avoir à recharger le module.
    """
    explicit = os.environ.get(_SALT_ENV_VAR)
    if explicit:
        return explicit.encode("utf-8")
    return _FALLBACK_PREFIX + settings.jwt_secret.encode("utf-8")


def hash_ip(ip: str) -> str:
    """Renvoie le SHA-256 hex de ``salt || ip``.

    * Déterministe : ``hash_ip("1.2.3.4") == hash_ip("1.2.3.4")``.
    * Non réversible sans le salt : on ne peut PAS retrouver "1.2.3.4"
      depuis le hash.
    * Renvoie toujours une string hex de longueur 64.

    On normalise l'IP en supprimant les espaces et en la mettant en
    minuscules (pour IPv6 mixed-case) — sans ça, deux représentations
    de la même IP produisent des hashes différents.
    """
    if not isinstance(ip, str):
        raise TypeError(f"hash_ip attend str, reçu {type(ip).__name__}")
    normalized = ip.strip().lower().encode("utf-8")
    salted = _resolved_salt() + b":" + normalized
    return hashlib.sha256(salted).hexdigest()


# ---------------------------------------------------------------------------
# is_anonymous — garde-fou anti-PII
# ---------------------------------------------------------------------------
# Liste de patterns "interdits" dans les NOMS de champs. Volontairement
# stricte : il vaut mieux refuser à tort un champ légitime (et le renommer
# en agrégat) que laisser passer une fuite. Le but est de détecter une
# régression involontaire dans un dataset.
_PII_FIELD_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^id$", re.IGNORECASE),
    re.compile(r"^.*[Ii]d$"),  # studentId, schoolId, teacherId, regionId…
    re.compile(r"first.?name", re.IGNORECASE),
    re.compile(r"last.?name", re.IGNORECASE),
    re.compile(r"full.?name", re.IGNORECASE),
    re.compile(r"guardian", re.IGNORECASE),
    re.compile(r"phone", re.IGNORECASE),
    re.compile(r"email", re.IGNORECASE),
    re.compile(r"birth.?date", re.IGNORECASE),
    re.compile(r"address", re.IGNORECASE),
    re.compile(r"unique.?code", re.IGNORECASE),
    re.compile(r"^ip$", re.IGNORECASE),
)

# Whitelist : noms qui RESSEMBLENT à un PII mais qui sont en réalité des
# agrégats explicites (regionName est un attribut d'entité publique).
_WHITELIST_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "regionname",
        "schoolname",
        "districtname",
        "prefecturename",
        "subprefecturename",
        "diplomatype",
    }
)


def is_anonymous(record: dict) -> bool:
    """Renvoie ``True`` si le record ne contient aucun champ ressemblant à un PII.

    Concrètement : on parcourt les clés du dict et on rejette tout nom qui
    matche un pattern de :data:`_PII_FIELD_PATTERNS` (sauf si le nom est
    dans :data:`_WHITELIST_FIELD_NAMES`). Le but est de détecter une
    régression au stade du test, pas de prétendre faire une analyse
    sémantique exhaustive.
    """
    if not isinstance(record, dict):
        return False
    for key in record:
        key_lower = key.lower()
        if key_lower in _WHITELIST_FIELD_NAMES:
            continue
        for pattern in _PII_FIELD_PATTERNS:
            if pattern.search(key):
                return False
    return True


__all__ = ["hash_ip", "is_anonymous"]
