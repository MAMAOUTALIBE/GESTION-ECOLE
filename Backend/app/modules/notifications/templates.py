"""Pre-built message templates (French) for common parent communications.

Each helper returns ``(subject, message)``. Templates are deliberately short
to keep SMS at 1 segment (160 chars) where possible.
"""
from __future__ import annotations


def bulletin_available(
    student_name: str, period_label: str, verify_url: str
) -> tuple[str, str]:
    return (
        "Bulletin disponible",
        (
            f"Bonjour, le bulletin de {student_name} pour {period_label} "
            f"est disponible. Vérifier : {verify_url}"
        ),
    )


def attendance_absent(student_name: str, date_iso: str) -> tuple[str, str]:
    return (
        "Absence signalée",
        (
            f"Bonjour, {student_name} a été noté(e) absent(e) le {date_iso}. "
            "Merci de contacter l'école si nécessaire."
        ),
    )


def attendance_late(student_name: str, date_iso: str) -> tuple[str, str]:
    return (
        "Retard signalé",
        f"Bonjour, {student_name} est arrivé(e) en retard le {date_iso}.",
    )


def validation_approved(entity_label: str) -> tuple[str, str]:
    return (
        "Validation approuvée",
        f"Votre demande pour « {entity_label} » a été approuvée.",
    )


def validation_rejected(entity_label: str, reason: str | None) -> tuple[str, str]:
    base = f"Votre demande pour « {entity_label} » a été rejetée."
    if reason:
        base += f" Motif : {reason}"
    return ("Validation rejetée", base)


def custom(subject: str | None, message: str) -> tuple[str, str]:
    """Pass-through for ad-hoc messages composed in the UI."""
    return (subject or "Message", message)
