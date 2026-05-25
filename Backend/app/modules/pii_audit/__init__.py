"""Module 5C — Audit des accès en lecture sur les données personnelles (PII).

La loi guinéenne 037/AN/2016 et les bonnes pratiques RGPD imposent de
tracer TOUTE consultation (et pas uniquement les modifications) de
données personnelles d'enfants mineurs et de leurs représentants
légaux. Cette table — ``PiiAccessLog`` — est append-only, indexée pour
répondre à deux questions opérationnelles :

* "Qui a consulté la fiche de mon enfant et quand ?"
* "Quels élèves ont été consultés par tel agent ?"

Rétention 3 ans (1095 jours) — purge mensuelle via Celery beat.
"""
