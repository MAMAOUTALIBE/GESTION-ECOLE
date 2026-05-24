"""i18n template engine for notifications.

Supports four languages spoken in Guinea:

* ``fr``  — French (canonical fallback)
* ``ff``  — Pular / Fula (ISO 639-1)
* ``sus`` — Soussou / Susu (ISO 639-3)
* ``man`` — Maninka (ISO 639-3)

Template lookup
---------------
:func:`render_template` resolves a template by the ``(key, language,
channel)`` tuple. If the requested language is missing, we fall back to
French — the rationale being that French is the official language of the
guinean administration and every account is guaranteed to understand it.

Variable substitution uses a deliberately tiny mustache subset
(``{{varName}}``). We do *not* implement conditionals, loops, or nested
sections — anything fancier belongs in the calling code, not in the
template.

Seeding
-------
:func:`seed_default_templates` is idempotent and called both from
the alembic migration (via raw SQL) and from the admin endpoint
``POST /api/notifications/templates/seed``. Translations for ``ff``,
``sus``, ``man`` are deliberately tagged with a language prefix
(``[ff] Bonjour…``) when we are not confident in a hand-validated
translation — the mechanism stays testable and auditable while we wait
for native-speaker review (see backlog 6.1).
"""
from __future__ import annotations

import re
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.models import NotificationTemplate

SUPPORTED_LANGUAGES: Final[tuple[str, ...]] = ("fr", "ff", "sus", "man")
FALLBACK_LANGUAGE: Final[str] = "fr"

# Channels used by the seed catalogue — must match the lowercase form
# stored in ``NotificationTemplate.channel``.
_SEED_CHANNELS: Final[tuple[str, ...]] = ("sms", "email", "in_app")
_SEED_KEYS: Final[tuple[str, ...]] = (
    "validation.created",
    "validation.approved",
    "validation.rejected",
    "validation.escalated",
    "attendance.daily_summary",
)


_MUSTACHE_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _substitute(template: str, variables: dict[str, object]) -> str:
    """Replace every ``{{name}}`` in ``template`` by ``variables[name]``.

    Missing variables fall through as an empty string — we never raise
    here because a notification with a blank placeholder is still more
    useful than no notification at all.
    """
    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(variables.get(name, ""))

    return _MUSTACHE_RE.sub(_replace, template)


class TemplateNotFoundError(LookupError):
    """Raised when no template exists for ``(key, language|fr, channel)``."""


async def render_template(
    session: AsyncSession,
    key: str,
    language: str,
    channel: str,
    variables: dict[str, object] | None = None,
) -> tuple[str | None, str]:
    """Render the ``(key, language, channel)`` template.

    Falls back to ``fr`` if the requested language is unavailable, and
    raises :class:`TemplateNotFoundError` if no template exists even in
    French. Returns ``(subject, body)`` — both already variable-substituted.
    """
    variables = variables or {}
    normalized_lang = language if language in SUPPORTED_LANGUAGES else FALLBACK_LANGUAGE

    stmt = select(NotificationTemplate).where(
        NotificationTemplate.key == key,
        NotificationTemplate.channel == channel,
        NotificationTemplate.language == normalized_lang,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()

    if row is None and normalized_lang != FALLBACK_LANGUAGE:
        # Fallback to French
        fallback_stmt = select(NotificationTemplate).where(
            NotificationTemplate.key == key,
            NotificationTemplate.channel == channel,
            NotificationTemplate.language == FALLBACK_LANGUAGE,
        )
        row = (await session.execute(fallback_stmt)).scalar_one_or_none()

    if row is None:
        raise TemplateNotFoundError(
            f"No template found for key={key!r} channel={channel!r} "
            f"(language={language!r}, fallback={FALLBACK_LANGUAGE!r})"
        )

    rendered_subject = _substitute(row.subject, variables) if row.subject else None
    rendered_body = _substitute(row.body, variables)
    return rendered_subject, rendered_body


# ---------------------------------------------------------------------------
# Seed catalogue
# ---------------------------------------------------------------------------
# Each entry is keyed by (key, language, channel). The seed is intentionally
# verbose: native-speaker review is part of Module 6.1, so we mark
# placeholder translations with a ``[ff]/[sus]/[man]`` prefix to make
# rebroadcasts trivial to grep.
_SEED_CATALOGUE: Final[dict[tuple[str, str, str], tuple[str | None, str]]] = {
    # ---------------- validation.created ----------------
    ("validation.created", "fr", "sms"): (
        None,
        "Nouvelle demande de validation pour {{entityLabel}}.",
    ),
    ("validation.created", "fr", "email"): (
        "Nouvelle demande de validation",
        (
            "Bonjour {{recipientName}}, une nouvelle demande de validation "
            "({{entityLabel}}) attend votre revue."
        ),
    ),
    ("validation.created", "fr", "in_app"): (
        "Nouvelle demande",
        "Demande de validation à traiter : {{entityLabel}}.",
    ),
    ("validation.created", "ff", "sms"): (
        None,
        "[ff] Wiɗto kesso ngam {{entityLabel}}.",
    ),
    ("validation.created", "ff", "email"): (
        "[ff] Wiɗto kesso",
        "[ff] Wiɗto kesso ngam {{entityLabel}} no fadda mo {{recipientName}}.",
    ),
    ("validation.created", "ff", "in_app"): (
        "[ff] Wiɗto kesso",
        "[ff] Wiɗto kesso : {{entityLabel}}.",
    ),
    ("validation.created", "sus", "sms"): (
        None,
        "[sus] Maɲɛrɛli nɛnɛ : {{entityLabel}}.",
    ),
    ("validation.created", "sus", "email"): (
        "[sus] Maɲɛrɛli nɛnɛ",
        "[sus] {{recipientName}}, maɲɛrɛli nɛnɛ ({{entityLabel}}) na qaqɔn.",
    ),
    ("validation.created", "sus", "in_app"): (
        "[sus] Maɲɛrɛli",
        "[sus] Maɲɛrɛli nɛnɛ : {{entityLabel}}.",
    ),
    ("validation.created", "man", "sms"): (
        None,
        "[man] Daɲinin kura : {{entityLabel}}.",
    ),
    ("validation.created", "man", "email"): (
        "[man] Daɲinin kura",
        "[man] {{recipientName}}, daɲinin kura ({{entityLabel}}) ye sigi.",
    ),
    ("validation.created", "man", "in_app"): (
        "[man] Daɲinin kura",
        "[man] Daɲinin kura : {{entityLabel}}.",
    ),
    # ---------------- validation.approved ----------------
    ("validation.approved", "fr", "sms"): (
        None,
        "Votre demande pour {{entityLabel}} a été approuvée.",
    ),
    ("validation.approved", "fr", "email"): (
        "Demande approuvée",
        (
            "Bonjour {{recipientName}}, votre demande pour {{entityLabel}} a été "
            "approuvée par {{reviewerName}}."
        ),
    ),
    ("validation.approved", "fr", "in_app"): (
        "Demande approuvée",
        "Votre demande {{entityLabel}} a été approuvée.",
    ),
    ("validation.approved", "ff", "sms"): (
        None,
        "[ff] Wiɗto maa ngam {{entityLabel}} jaɓaama.",
    ),
    ("validation.approved", "ff", "email"): (
        "[ff] Wiɗto jaɓaama",
        "[ff] {{recipientName}}, wiɗto maa ngam {{entityLabel}} jaɓaama.",
    ),
    ("validation.approved", "ff", "in_app"): (
        "[ff] Wiɗto jaɓaama",
        "[ff] Wiɗto maa {{entityLabel}} jaɓaama.",
    ),
    ("validation.approved", "sus", "sms"): (
        None,
        "[sus] I la maɲɛrɛli {{entityLabel}} sɔɔnɔ.",
    ),
    ("validation.approved", "sus", "email"): (
        "[sus] Maɲɛrɛli sɔɔnɔ",
        "[sus] {{recipientName}}, i la maɲɛrɛli {{entityLabel}} sɔɔnɔ.",
    ),
    ("validation.approved", "sus", "in_app"): (
        "[sus] Sɔɔnɔ",
        "[sus] I la maɲɛrɛli {{entityLabel}} sɔɔnɔ.",
    ),
    ("validation.approved", "man", "sms"): (
        None,
        "[man] I ka daɲinin {{entityLabel}} sɔnna.",
    ),
    ("validation.approved", "man", "email"): (
        "[man] Daɲinin sɔnna",
        "[man] {{recipientName}}, i ka daɲinin {{entityLabel}} sɔnna.",
    ),
    ("validation.approved", "man", "in_app"): (
        "[man] Sɔnna",
        "[man] I ka daɲinin {{entityLabel}} sɔnna.",
    ),
    # ---------------- validation.rejected ----------------
    ("validation.rejected", "fr", "sms"): (
        None,
        "Votre demande pour {{entityLabel}} a été rejetée. Motif : {{reason}}",
    ),
    ("validation.rejected", "fr", "email"): (
        "Demande rejetée",
        (
            "Bonjour {{recipientName}}, votre demande pour {{entityLabel}} a été "
            "rejetée par {{reviewerName}}. Motif : {{reason}}"
        ),
    ),
    ("validation.rejected", "fr", "in_app"): (
        "Demande rejetée",
        "Demande {{entityLabel}} rejetée. Motif : {{reason}}",
    ),
    ("validation.rejected", "ff", "sms"): (
        None,
        "[ff] Wiɗto maa ngam {{entityLabel}} salaama. Sabu : {{reason}}",
    ),
    ("validation.rejected", "ff", "email"): (
        "[ff] Wiɗto salaama",
        "[ff] {{recipientName}}, wiɗto maa {{entityLabel}} salaama. Sabu : {{reason}}",
    ),
    ("validation.rejected", "ff", "in_app"): (
        "[ff] Salaama",
        "[ff] Wiɗto {{entityLabel}} salaama. Sabu : {{reason}}",
    ),
    ("validation.rejected", "sus", "sms"): (
        None,
        "[sus] I la maɲɛrɛli {{entityLabel}} mu sɔɔnxi. Daliilu : {{reason}}",
    ),
    ("validation.rejected", "sus", "email"): (
        "[sus] Maɲɛrɛli sɔɔnxi",
        "[sus] {{recipientName}}, maɲɛrɛli {{entityLabel}} mu sɔɔnxi. Daliilu : {{reason}}",
    ),
    ("validation.rejected", "sus", "in_app"): (
        "[sus] Maɲɛrɛli sɔɔnxi",
        "[sus] Maɲɛrɛli {{entityLabel}} sɔɔnxi. Daliilu : {{reason}}",
    ),
    ("validation.rejected", "man", "sms"): (
        None,
        "[man] I ka daɲinin {{entityLabel}} banna. Kun : {{reason}}",
    ),
    ("validation.rejected", "man", "email"): (
        "[man] Daɲinin banna",
        "[man] {{recipientName}}, daɲinin {{entityLabel}} banna. Kun : {{reason}}",
    ),
    ("validation.rejected", "man", "in_app"): (
        "[man] Banna",
        "[man] Daɲinin {{entityLabel}} banna. Kun : {{reason}}",
    ),
    # ---------------- validation.escalated ----------------
    ("validation.escalated", "fr", "sms"): (
        None,
        "Rappel : demande {{entityLabel}} en attente (niveau {{level}}).",
    ),
    ("validation.escalated", "fr", "email"): (
        "Rappel : demande en attente",
        (
            "Bonjour {{recipientName}}, la demande {{entityLabel}} attend toujours "
            "votre revue (escalade niveau {{level}})."
        ),
    ),
    ("validation.escalated", "fr", "in_app"): (
        "Demande en retard",
        "Demande {{entityLabel}} en attente — escalade niveau {{level}}.",
    ),
    ("validation.escalated", "ff", "sms"): (
        None,
        "[ff] Janngubol : wiɗto {{entityLabel}} no fadda (tolno {{level}}).",
    ),
    ("validation.escalated", "ff", "email"): (
        "[ff] Janngubol",
        "[ff] {{recipientName}}, wiɗto {{entityLabel}} no fadda (tolno {{level}}).",
    ),
    ("validation.escalated", "ff", "in_app"): (
        "[ff] Janngubol",
        "[ff] Wiɗto {{entityLabel}} fadda (tolno {{level}}).",
    ),
    ("validation.escalated", "sus", "sms"): (
        None,
        "[sus] Maɲɛrɛli {{entityLabel}} mu qabaxi (tagi {{level}}).",
    ),
    ("validation.escalated", "sus", "email"): (
        "[sus] Maɲɛrɛli mu qabaxi",
        "[sus] {{recipientName}}, maɲɛrɛli {{entityLabel}} mu qabaxi (tagi {{level}}).",
    ),
    ("validation.escalated", "sus", "in_app"): (
        "[sus] Qabaxi",
        "[sus] Maɲɛrɛli {{entityLabel}} mu qabaxi (tagi {{level}}).",
    ),
    ("validation.escalated", "man", "sms"): (
        None,
        "[man] Daɲinin {{entityLabel}} ye makɔnɔn (hakɛ {{level}}).",
    ),
    ("validation.escalated", "man", "email"): (
        "[man] Daɲinin makɔnɔn",
        "[man] {{recipientName}}, daɲinin {{entityLabel}} ye makɔnɔn (hakɛ {{level}}).",
    ),
    ("validation.escalated", "man", "in_app"): (
        "[man] Makɔnɔn",
        "[man] Daɲinin {{entityLabel}} makɔnɔn (hakɛ {{level}}).",
    ),
    # ---------------- attendance.daily_summary ----------------
    ("attendance.daily_summary", "fr", "sms"): (
        None,
        "Résumé du {{date}} : {{presentCount}} présents / {{absentCount}} absents.",
    ),
    ("attendance.daily_summary", "fr", "email"): (
        "Résumé de présence du {{date}}",
        (
            "Bonjour, résumé de présence du {{date}} pour {{schoolName}} : "
            "{{presentCount}} présents, {{absentCount}} absents, {{lateCount}} retards."
        ),
    ),
    ("attendance.daily_summary", "fr", "in_app"): (
        "Résumé du jour",
        "{{date}} : {{presentCount}}P / {{absentCount}}A / {{lateCount}}R.",
    ),
    ("attendance.daily_summary", "ff", "sms"): (
        None,
        "[ff] Hakkille {{date}} : {{presentCount}} no ɗoo / {{absentCount}} alaa.",
    ),
    ("attendance.daily_summary", "ff", "email"): (
        "[ff] Hakkille {{date}}",
        (
            "[ff] Hakkille {{date}} {{schoolName}} : {{presentCount}} no ɗoo, "
            "{{absentCount}} alaa, {{lateCount}} ñawni."
        ),
    ),
    ("attendance.daily_summary", "ff", "in_app"): (
        "[ff] Hakkille ñalnde",
        "[ff] {{date}} : {{presentCount}}P / {{absentCount}}A / {{lateCount}}R.",
    ),
    ("attendance.daily_summary", "sus", "sms"): (
        None,
        "[sus] {{date}} hakkili : {{presentCount}} bara / {{absentCount}} mu na.",
    ),
    ("attendance.daily_summary", "sus", "email"): (
        "[sus] {{date}} hakkili",
        (
            "[sus] {{schoolName}} {{date}} hakkili : {{presentCount}} bara, "
            "{{absentCount}} mu na, {{lateCount}} naxan na qoroma."
        ),
    ),
    ("attendance.daily_summary", "sus", "in_app"): (
        "[sus] Hakkili",
        "[sus] {{date}} : {{presentCount}}P / {{absentCount}}A / {{lateCount}}R.",
    ),
    ("attendance.daily_summary", "man", "sms"): (
        None,
        "[man] {{date}} jaabili : {{presentCount}} sigilen / {{absentCount}} taara.",
    ),
    ("attendance.daily_summary", "man", "email"): (
        "[man] {{date}} jaabili",
        (
            "[man] {{schoolName}} {{date}} jaabili : {{presentCount}} sigilen, "
            "{{absentCount}} taara, {{lateCount}} kɔfɛla."
        ),
    ),
    ("attendance.daily_summary", "man", "in_app"): (
        "[man] Jaabili",
        "[man] {{date}} : {{presentCount}}P / {{absentCount}}A / {{lateCount}}R.",
    ),
}


async def seed_default_templates(session: AsyncSession) -> int:
    """Insert every row in :data:`_SEED_CATALOGUE` that is not yet present.

    Returns the number of rows actually inserted (zero on subsequent calls
    because the unique key prevents duplicates).
    """
    inserted = 0
    for (key, language, channel), (subject, body) in _SEED_CATALOGUE.items():
        existing = (
            await session.execute(
                select(NotificationTemplate.id).where(
                    NotificationTemplate.key == key,
                    NotificationTemplate.language == language,
                    NotificationTemplate.channel == channel,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        session.add(
            NotificationTemplate(
                key=key,
                language=language,
                channel=channel,
                subject=subject,
                body=body,
                variables=_extract_variables(body) + (
                    _extract_variables(subject) if subject else []
                ),
            )
        )
        inserted += 1
    if inserted:
        await session.flush()
    return inserted


def _extract_variables(text: str) -> list[str]:
    """Return the ordered, deduplicated list of mustache variables in ``text``."""
    seen: list[str] = []
    for match in _MUSTACHE_RE.finditer(text):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def expected_seed_count() -> int:
    """Number of rows :func:`seed_default_templates` inserts on a clean DB."""
    return len(_SEED_CATALOGUE)
