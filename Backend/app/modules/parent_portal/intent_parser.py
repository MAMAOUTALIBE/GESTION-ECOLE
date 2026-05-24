"""Module 18 — Parser d'intentions pour les messages WhatsApp libres.

Le parent écrit ce qu'il veut ("moyenne", "MOYENNE", "MoYeNnE", "Présence",
"presence svp", "bulletin trimestre 1"). On normalise (accents enlevés +
lowercase) puis on cherche un mot-clé connu. À défaut, fallback vers
:data:`ParentIntent.AIDE` qui déclenche une réponse d'aide listant les
intentions reconnues.

Pourquoi pas de NLP ?
---------------------
Le MVP doit fonctionner offline et en zone faible bande passante. Un parser
à base de keywords (≈ 50 lignes) est :
* Déterministe et testable.
* Compatible toutes langues (on ajoute des keywords par langue au besoin).
* Zéro dépendance externe.

Extension future (backlog 18.1) : ajouter des phrases entières ("quand est
la rentrée"), regex sur dates, multilingue (ff, sus, man).
"""
from __future__ import annotations

import unicodedata
from typing import Final

from app.modules.parent_portal.enums import ParentIntent

# Chaque intent est mappé à un ensemble de mots-clés. La détection se fait
# en sous-chaîne après normalisation (lowercase + suppression d'accents),
# donc "MOYENNE", "moyenne svp", "ma moyenne ?" matchent tous le même.
# Ordre important côté itération : on garde un fallback raisonnable
# (les keywords plus spécifiques avant les plus généraux).
_INTENT_KEYWORDS: Final[dict[ParentIntent, tuple[str, ...]]] = {
    ParentIntent.MOYENNE: ("moyenne", "note", "notes", "moy"),
    ParentIntent.PRESENCE: ("presence", "absence", "absent", "absences", "retard"),
    ParentIntent.BULLETIN: ("bulletin", "bulletins", "rapport"),
    ParentIntent.EVENEMENT: (
        "evenement", "evenements", "agenda",
        "rentree", "reunion", "ceremonie",
    ),
    ParentIntent.AIDE: ("aide", "help", "menu", "?", "info", "infos"),
}


def _normalize(text: str) -> str:
    """Lowercase + supprime les diacritiques (é → e, à → a, etc.)."""
    if not text:
        return ""
    folded = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in folded if not unicodedata.combining(c))
    return stripped.lower().strip()


def parse_intent(text: str) -> ParentIntent:
    """Renvoie l'intent reconnu dans ``text``, ou :data:`ParentIntent.AIDE`.

    Algorithme : on normalise le texte, puis on teste chaque mot-clé en
    sous-chaîne. La première intention dont un mot-clé matche est
    renvoyée. L'ordre d'itération suit ``_INTENT_KEYWORDS`` — on a placé
    AIDE en dernier de sorte qu'un message contenant "menu" ne capture
    pas indûment un autre intent.
    """
    normalized = _normalize(text)
    if not normalized:
        return ParentIntent.AIDE

    for intent, keywords in _INTENT_KEYWORDS.items():
        for kw in keywords:
            if kw in normalized:
                return intent
    return ParentIntent.AIDE
