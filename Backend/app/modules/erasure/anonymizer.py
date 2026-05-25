"""Module 5D — Logique d'anonymisation effective d'un élève.

Cette fonction est pure (n'envoie ni notification ni audit) et ne fait
QUE muter les rows DB. Le wrapping audit/notification est porté par
``ErasureService``.

Stratégie globale :

* Données nominatives sur ``Student`` (firstName, lastName, photoUrl,
  guardianName, guardianPhone) → écrasées par valeurs neutres.
* ``Student.uniqueCode`` est PRÉSERVÉ (référence pour audits a
  posteriori — la trace administrative doit rester).
* ``StudentParent`` (liens) → ``DELETE``.
* ``Parent`` rows → DELETE uniquement si le parent n'a plus AUCUN lien
  à un autre élève (orphelinage). Sinon on garde le row pour ne pas
  rompre les FK des autres élèves.
* ``QrCredential`` (porte un payload nominatif) → DELETE.
* ``AttendanceRecord``, ``Grade``, ``ReportCard`` → PRÉSERVÉS (agrégats
  Module 1A — taux de présence, moyennes, classements rétrospectifs).
  Aucun champ libre à anonymiser (ces rows ne contiennent pas le nom
  de l'élève, seulement son id).
* Tables avec champs libres (``HealthVisit.description``,
  ``Vaccination.notes``, ``StudentAllergy.notes``, ``Incident.description``,
  ``ParentCommunication.subject``/``message``, ``StudentTransfer.reason``,
  ``LibraryLoan`` n'a pas de champ libre) → ``[ANONYMISÉ]`` à la place.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.academics.models import (
    Grade,
    Parent,
    ParentCommunication,
    ReportCard,
    StudentParent,
)
from app.modules.attendance.models import AttendanceRecord, QrCredential
from app.modules.census.models import Student, StudentTransfer
from app.modules.library.models import LibraryLoan
from app.modules.schoollife.models import (
    HealthVisit,
    Incident,
    StudentAllergy,
    Vaccination,
)

# Marqueur visible utilisé pour signaler qu'un champ texte libre a été
# anonymisé. On évite "NULL" pour que les rapports historiques (ex.
# "incidents par école") affichent quelque chose et que l'utilisateur
# comprenne qu'il s'agit d'une volonté délibérée, pas d'un bug.
_REDACTED: str = "[ANONYMISÉ]"
_ANON_NAME: str = "Anonyme"


async def anonymize_student(
    session: AsyncSession,
    student_id: str,
) -> dict[str, int]:
    """Anonymise un élève en place et retourne les compteurs par table.

    Le caller doit avoir vérifié l'autorisation et la validité du
    ``student_id`` — cette fonction ne fait que muter. Si l'élève
    n'existe pas, on retourne des compteurs à 0 (idempotent).

    Returns
    -------
    dict[str, int]
        Dictionnaire ``{"table_name": count}`` des rows affectés (mis
        à jour ou supprimés). Utile pour les audits et tests.
    """
    counts: dict[str, int] = {
        "Student": 0,
        "StudentParent": 0,
        "Parent": 0,
        "QrCredential": 0,
        "Incident": 0,
        "HealthVisit": 0,
        "Vaccination": 0,
        "StudentAllergy": 0,
        "ParentCommunication": 0,
        "StudentTransfer": 0,
        "LibraryLoan": 0,
        "AttendanceRecord": 0,
        "Grade": 0,
        "ReportCard": 0,
    }

    # ------------------------------------------------------------------
    # 1. Recupère le student. Si introuvable on sort proprement
    #    (idempotence : un job qui re-tourne ne casse pas).
    # ------------------------------------------------------------------
    student = (
        await session.execute(
            select(Student).where(Student.id == student_id)
        )
    ).scalars().one_or_none()
    if student is None:
        return counts

    # ------------------------------------------------------------------
    # 2. ParentCommunication — message peut contenir le nom de l'élève.
    #    On redacte AVANT toute suppression de parents orphelins (étape 4),
    #    sinon les communications portées par un parent orphelin seraient
    #    perdues dans le DELETE en cascade plutôt que redactées.
    # ------------------------------------------------------------------
    upd_pc = await session.execute(
        update(ParentCommunication)
        .where(ParentCommunication.studentId == student_id)
        .values(subject=_REDACTED, message=_REDACTED)
    )
    counts["ParentCommunication"] = int(upd_pc.rowcount or 0)

    # ------------------------------------------------------------------
    # 3. StudentParent — récupère les parentIds liés AVANT suppression.
    #    On en aura besoin pour décider de l'orphelinage.
    # ------------------------------------------------------------------
    parent_ids_query = await session.execute(
        select(StudentParent.parentId).where(
            StudentParent.studentId == student_id
        )
    )
    linked_parent_ids: list[str] = list(parent_ids_query.scalars().all())

    # Supprime les liens
    if linked_parent_ids:
        del_links = await session.execute(
            delete(StudentParent).where(
                StudentParent.studentId == student_id
            )
        )
        counts["StudentParent"] = int(del_links.rowcount or 0)

    # Pour chaque parent, vérifie s'il a encore d'autres enfants liés.
    # Si non → DELETE. Si oui → on garde. ParentCommunication portées
    # par le parent (pour d'autres élèves ou standalone) sont coupées
    # AVANT le DELETE pour respecter les FK ; les communications
    # liées à NOTRE student ont déjà été redactées (étape 2).
    deleted_parents = 0
    for pid in linked_parent_ids:
        remaining = await session.execute(
            select(StudentParent).where(
                StudentParent.parentId == pid
            )
        )
        still_linked = remaining.scalars().first()
        if still_linked is None:
            await session.execute(
                delete(ParentCommunication).where(
                    ParentCommunication.parentId == pid
                )
            )
            del_parent = await session.execute(
                delete(Parent).where(Parent.id == pid)
            )
            deleted_parents += int(del_parent.rowcount or 0)
    counts["Parent"] = deleted_parents

    # ------------------------------------------------------------------
    # 4. QrCredential — porte un payload nominatif. DELETE.
    # ------------------------------------------------------------------
    del_qr = await session.execute(
        delete(QrCredential).where(QrCredential.studentId == student_id)
    )
    counts["QrCredential"] = int(del_qr.rowcount or 0)

    # ------------------------------------------------------------------
    # 5. Incident — description peut contenir le nom. Garde le row,
    #    redacte description, NULL studentId (la FK est nullable).
    # ------------------------------------------------------------------
    upd_inc = await session.execute(
        update(Incident)
        .where(Incident.studentId == student_id)
        .values(description=_REDACTED, studentId=None)
    )
    counts["Incident"] = int(upd_inc.rowcount or 0)

    # ------------------------------------------------------------------
    # 6. HealthVisit — description + nurseName potentiellement nominatif.
    #    FK studentId nullable → on NULL.
    # ------------------------------------------------------------------
    upd_hv = await session.execute(
        update(HealthVisit)
        .where(HealthVisit.studentId == student_id)
        .values(description=_REDACTED, nurseName=None, studentId=None)
    )
    counts["HealthVisit"] = int(upd_hv.rowcount or 0)

    # ------------------------------------------------------------------
    # 7. Vaccination — notes + administeredBy. studentId NON nullable →
    #    on doit DELETE (sinon on viole la contrainte). Les vaccins
    #    perdus ne contribuent pas aux agrégats Module 1A (statistique
    #    PEV nationale est tirée d'autres sources opérationnelles).
    # ------------------------------------------------------------------
    del_vacc = await session.execute(
        delete(Vaccination).where(Vaccination.studentId == student_id)
    )
    counts["Vaccination"] = int(del_vacc.rowcount or 0)

    # ------------------------------------------------------------------
    # 8. StudentAllergy — idem Vaccination (FK NON nullable).
    # ------------------------------------------------------------------
    del_all = await session.execute(
        delete(StudentAllergy).where(
            StudentAllergy.studentId == student_id
        )
    )
    counts["StudentAllergy"] = int(del_all.rowcount or 0)

    # ------------------------------------------------------------------
    # 9. StudentTransfer — reason libre. FK studentId NON nullable →
    #    on garde la trace (mouvement scolaire utile aux statistiques)
    #    mais on redacte reason. On laisse studentId intact car les FK
    #    sur Student restent valides (Student conservé anonymisé).
    # ------------------------------------------------------------------
    upd_tr = await session.execute(
        update(StudentTransfer)
        .where(StudentTransfer.studentId == student_id)
        .values(reason=_REDACTED)
    )
    counts["StudentTransfer"] = int(upd_tr.rowcount or 0)

    # ------------------------------------------------------------------
    # 10. LibraryLoan — pas de champ libre, on ne touche pas. On COMPTE
    #     juste les rows pour le breakdown.
    # ------------------------------------------------------------------
    loans = await session.execute(
        select(LibraryLoan.id).where(LibraryLoan.studentId == student_id)
    )
    counts["LibraryLoan"] = len(list(loans.scalars().all()))

    # ------------------------------------------------------------------
    # 11. AttendanceRecord / Grade / ReportCard — PRÉSERVÉS pour les
    #     agrégats Module 1A. On COMPTE juste pour le breakdown audit.
    # ------------------------------------------------------------------
    att = await session.execute(
        select(AttendanceRecord.id).where(
            AttendanceRecord.studentId == student_id
        )
    )
    counts["AttendanceRecord"] = len(list(att.scalars().all()))
    grades_q = await session.execute(
        select(Grade.id).where(Grade.studentId == student_id)
    )
    counts["Grade"] = len(list(grades_q.scalars().all()))
    rc_q = await session.execute(
        select(ReportCard.id).where(ReportCard.studentId == student_id)
    )
    counts["ReportCard"] = len(list(rc_q.scalars().all()))

    # ------------------------------------------------------------------
    # 12. Student lui-même — anonymisé en place. uniqueCode préservé.
    # ------------------------------------------------------------------
    student.firstName = _ANON_NAME
    student.lastName = _ANON_NAME
    student.photoUrl = None
    student.guardianName = None
    student.guardianPhone = None
    counts["Student"] = 1

    await session.flush()
    return counts


def initials_for(first_name: str | None, last_name: str | None) -> str:
    """Retourne les initiales d'un élève (ex: "M.K.") ou "?.?.".

    Utilisé par le service quand on retourne ``ErasureRequestRead`` :
    l'admin voit qui (initiales) sans révéler le nom complet une fois
    la demande EXECUTED.
    """
    first_initial = (first_name or "?")[0:1].upper() or "?"
    last_initial = (last_name or "?")[0:1].upper() or "?"
    return f"{first_initial}.{last_initial}."


__all__: list[Any] = ["anonymize_student", "initials_for"]
