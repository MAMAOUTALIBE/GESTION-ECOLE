# Projet : Carte scolaire nationale (Guinée)

Application de planification et de gestion du système éducatif guinéen.
Ce n'est PAS un simple annuaire d'écoles : c'est un outil d'aide à la décision
(SIGE / EMIS) qui collecte des données (recensement scolaire), les agrège, les
analyse et produit des indicateurs pour orienter les décisions publiques.

## Objectifs métier (à garder en tête à chaque développement)
- Optimiser la répartition des enseignants et des ressources matérielles.
- Orienter les investissements en infrastructures (où construire/réhabiliter).
- Identifier et corriger les disparités territoriales (urbain vs rural).
- Améliorer l'équité, notamment la scolarisation des filles.
- Fournir des données fiables, désagrégées (sexe, niveau, zone géographique).

## Stack technique — À DÉTECTER, ne rien présupposer
Avant tout développement, détecte la stack réelle du projet :
- Lis `package.json` (dépendances, scripts), `angular.json`, et tout fichier de
  config présent (tsconfig, fichiers backend, docker, etc.).
- Identifie : framework frontend et sa version, backend éventuel et son langage,
  base de données, et surtout les **outils de test déjà installés** (Jasmine/
  Karma, Jest, Vitest, Cypress, Playwright…).
- Identifie les **commandes de test et de lint réelles** dans la section
  `scripts` du `package.json` (ex. `npm test`, `ng test`, `npm run e2e`).
- Si AUCUN outil de test n'est installé : choisis l'outil le plus standard pour
  la stack détectée, propose-le, installe-le, et documente ce choix.
- Consigne la stack détectée en tête de ton premier rapport, pour que tous les
  rôles s'y réfèrent ensuite.
Connu à ce stade : frontend Angular, route racine du recensement /school-census.
Tout le reste doit être confirmé par lecture du code, pas supposé.

---

# RÈGLE ABSOLUE : aucun module validé sans tests positifs

Cette règle prime sur toutes les autres. Elle n'est jamais contournée, même
sous pression de rapidité.

## Workflow obligatoire pour CHAQUE module ou fonctionnalité

1. **DÉVELOPPER** — endosser le rôle du skill `developpeur`. Écrire le code de
   la fonctionnalité.
2. **TESTER** — endosser le rôle du skill `testeur`. Écrire les tests, puis les
   exécuter réellement (lancer la commande de test, ne jamais supposer le
   résultat).
3. **VÉRIFIER** — lire la sortie réelle des tests.
   - Si TOUS les tests passent → le module est **VALIDÉ**, on peut passer au suivant.
   - Si UN SEUL test échoue → le module est **REJETÉ**. Retour à l'étape 1 pour
     corriger. Recommencer le cycle jusqu'à ce que tous les tests passent.
4. **NE JAMAIS** déclarer un module terminé, ni démarrer le module suivant, tant
   que les tests du module courant ne sont pas tous au vert.

## Définition de « terminé » (Definition of Done)
Un module n'est considéré comme terminé QUE si TOUTES ces conditions sont vraies :
- [ ] Le code compile sans erreur (`ng build` / build backend).
- [ ] Des tests unitaires existent et couvrent les cas nominaux ET les cas limites.
- [ ] Tous les tests unitaires passent (sortie de test affichée et lue).
- [ ] Le linter ne remonte aucune erreur (`ng lint`).
- [ ] La revue (skill `reviewer`) n'a relevé aucun problème bloquant.
- [ ] Pour toute donnée personnelle (élèves mineurs, parents) : minimisation et
      contrôle d'accès vérifiés.

## Comment annoncer le résultat
À la fin de chaque cycle, annoncer clairement, par exemple :
`MODULE "saisie-effectifs" : VALIDÉ ✅ (12/12 tests passés)`
ou
`MODULE "saisie-effectifs" : REJETÉ ❌ (2/12 tests en échec) — correction en cours`

## Discipline de commits
- Un commit par module validé, jamais de commit avec des tests rouges.
- Message de commit clair décrivant la fonctionnalité et le statut des tests.

---

# Skills disponibles
- `architecte` : cadre l'architecture, le modèle de données et l'ordre des
  modules ; détecte la stack ; garant de la cohérence métier (carte scolaire).
- `designer` : UI/UX, accessibilité, ergonomie de saisie terrain.
- `developpeur` : conception et écriture du code (front + back).
- `testeur` : écriture et exécution des tests ; prononce VALIDÉ / REJETÉ.
- `reviewer` : revue de code, sécurité, qualité des données, données personnelles.

Ordre de travail logique : architecte (cadre) → designer (conçoit l'écran) →
developpeur (code) → testeur (valide/rejette) → reviewer (dernière barrière).
L'architecte n'intervient pas à chaque module, mais au début et à chaque décision
structurante. Toujours nommer explicitement le rôle endossé au début de chaque étape.
