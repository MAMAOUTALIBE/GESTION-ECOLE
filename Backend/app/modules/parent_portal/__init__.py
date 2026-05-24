"""Module 18 — Portail parent multi-canal.

Trois canaux d'entrée :

* **WhatsApp Business** : webhook entrant, parsing intention, réponse
  formatée vers le parent (HMAC sur le webhook).
* **USSD enrichi** : extension du menu Module 14 (bulletins / événement
  à venir) — branchée via :func:`service.enrich_ussd_menu`.
* **Page publique HTML légère** : `/api/parent-portal/parent/{phone_hash}`
  pour consultation rapide sans login (données ANONYMISÉES : initiales,
  classe, dernière moyenne).

Le module ne contient que de la logique de présentation : la donnée
réelle est lue depuis ``Student`` (Module 2) et ``ReportCard``
(Module 4). On ne réplique rien.
"""
