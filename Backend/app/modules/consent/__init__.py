"""Module 5B — Consentement utilisateur + mentions légales.

Loi 037/AN/2016 (Guinée) + RGPD imposent un consentement explicite
recueilli à la 1ère connexion (et redemandé à chaque révision majeure
de la politique de confidentialité).

Endpoints
---------
* ``GET  /api/consent/status``  — toute personne authentifiée. Retourne
  la version requise du contrat + la dernière version acceptée par
  l'utilisateur courant + ``needsAcceptance`` (booléen).
* ``POST /api/consent/accept``  — toute personne authentifiée. Persiste
  l'acceptation (upsert sur userId) avec IP + user-agent à des fins de
  preuve, met à jour ``User.consentVersion``.

Audit : chaque acceptation est tracée dans ``PiiAccessLog``
(entityType=USER, accessType=EXPORT — comme proxy pour "opération
sensible utilisateur"). En cas d'échec d'audit, le flux principal
continue (best-effort).
"""
