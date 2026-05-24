"""Module 3C — Score composite de priorité d'investissement par école.

Donne un classement 0-100 par école basé sur 4 dimensions pondérées :

* Infrastructure (35%) — eau, élec, latrines, état bâtiment, ratio salles
  utilisables, internet.
* Saturation projetée (25%) — sévérité ``CapacityDemandSnapshot`` à
  horizon +1 an.
* Équité (25%) — GPI de l'école (calculé depuis Enrollment).
* Accessibilité (15%) — ``zoneType`` effective + bonus distance école-élève.

Output : table ``InvestmentPriorityScore`` (un row par école) + endpoints
de listing / top N / détail breakdown.
"""
