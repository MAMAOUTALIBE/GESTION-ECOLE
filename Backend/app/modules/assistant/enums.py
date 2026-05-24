"""Module 10 — Assistant LLM : enums dédiés."""
from enum import StrEnum


class AssistantMessageRole(StrEnum):
    """Trois rôles distincts dans une conversation.

    * ``user``      — message saisi par l'utilisateur.
    * ``assistant`` — réponse text-only produite par le LLM (ou par le
      fallback scripté).
    * ``tool``      — résultat d'un tool call exécuté par le backend.
      ``toolName`` / ``toolInput`` / ``toolOutput`` sont alors remplis.

    Les valeurs MINUSCULES sont volontaires : elles matchent les conventions
    de l'API Anthropic (``role: "user"`` / ``"assistant"``) pour faciliter
    la sérialisation directe vers ``client.messages.create``.
    """

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
