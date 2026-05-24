"""Module 3B — Simulateur "what-if" de réorganisation du réseau scolaire.

L'objectif IIPE étape 3 est de permettre au planificateur de tester des
hypothèses (création / fermeture / fusion d'écoles) sans toucher aux
données réelles. Ce module expose :

* un modèle ``SimulationScenario`` (persisté pour auditabilité) ;
* une logique pure ``simulator.py`` (read-only, calcul en mémoire) ;
* un service async + un router HTTP.

La table ``School`` n'est jamais modifiée : c'est une garantie forte du
module — un planificateur peut jouer 100 scénarios sans risque pour la
photo officielle du réseau.
"""
from app.modules.simulator import models

__all__ = ["models"]
