"""Module 5D — Droit à l'oubli (anonymisation post-sortie d'élève).

Quand un élève quitte définitivement le système éducatif guinéen
(déménagement à l'étranger, décès, exclusion), le ministère doit pouvoir
retirer ses données nominatives sous 2 ans (loi 037/AN/2016 + RGPD
Art. 17). Ce module fournit le workflow :

1. ``POST /api/erasure/requests`` — un admin national crée une demande
   pour un élève ; la demande passe en ``GRACE_PERIOD`` pendant 30
   jours (réversibilité en cas d'erreur).
2. Pendant la grace period, ``POST /api/erasure/requests/{id}/cancel``
   permet d'annuler.
3. Le worker quotidien (04:00 UTC) ``execute_pending_erasures_task``
   scanne les demandes dont ``gracePeriodUntil < now`` et applique
   l'anonymisation effective : ``firstName``/``lastName`` → "Anonyme",
   ``photoUrl``/``guardianName``/``guardianPhone`` → NULL, liens
   ``StudentParent`` supprimés, parents orphelins supprimés, QR
   credential supprimé, champs libres (notes, descriptions) →
   "[ANONYMISÉ]".

PRÉSERVÉ pour les agrégats Module 1A :

* ``AttendanceRecord`` (partition par RANGE — ne pas casser le RANGE).
* ``Grade``, ``ReportCard`` (indicateurs IIPE rétrospectifs).
* ``Enrollment.count`` — l'élève contribue déjà à l'agrégat école/année.

Chaque opération est auditée dans ``PiiAccessLog`` (entityType=STUDENT,
accessType=EXPORT — la valeur EXPORT est utilisée comme proxy pour
"extraction / suppression contrôlée" sans modifier l'enum 5C).
"""
