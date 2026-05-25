# Module 3C UI — Priorités d'investissement

Écran `/school-census/investissements` — classement national des écoles à
investir en priorité pour orienter le budget ministériel.

## Composants standalone

| Composant                          | Rôle                                                                                  |
|------------------------------------|---------------------------------------------------------------------------------------|
| `InvestissementsPage`              | Page principale — orchestrateur, signals, KPIs, filtres, table, panneau détail.       |
| `InvestmentKpiCard`                | Card cliquable par catégorie de priorité (TRES_HAUTE/HAUTE/MOYENNE/BASSE).            |
| `InvestmentTable`                  | Table top 100 triée score↓, colonnes Rang/École/Région/Score/Catégorie/4 sparkbars.   |
| `InvestmentDetailPanel`            | Slide-in droite — radar ApexCharts + breakdown texte + recommandations heuristiques.  |
| `InvestmentApiService` (`shared/`) | Wrapper HTTP miroir des schémas backend Pydantic.                                     |

## Endpoints backend consommés

| Méthode | Chemin                                              | Usage                                              | Rôles                            |
|---------|-----------------------------------------------------|----------------------------------------------------|----------------------------------|
| POST    | `/api/investment/compute-scores`                    | Recalcul global (bouton "Recalculer")              | NATIONAL_ADMIN, MINISTRY_ADMIN   |
| GET     | `/api/investment/top-priorities?limit=100&...`      | Chargement initial + filtre année                  | NATIONAL/MINISTRY/REGIONAL/INSPECTOR |
| GET     | `/api/investment/priorities?category=&regionId=...` | (Disponible côté service, non utilisé dans la page) | NATIONAL/MINISTRY/REGIONAL/INSPECTOR |
| GET     | `/api/investment/schools/{schoolId}`                | Détail breakdown (utilisé en fallback service)     | NATIONAL/MINISTRY/REGIONAL/INSPECTOR |

Le top-priorities retourne déjà le `breakdownJson` pour les 100 lignes ;
le panneau détail consomme directement la ligne sélectionnée — pas
d'appel HTTP supplémentaire lors d'un click. `getSchoolPriority` reste
disponible si besoin de rafraîchir un score précis.

## RBAC

* Route ouverte à `NATIONAL_ADMIN`, `MINISTRY_ADMIN`, `REGIONAL_ADMIN`,
  `INSPECTOR` (cf. `school-census.routes.ts`).
* Bouton "Recalculer" affiché uniquement quand
  `auth.hasAnyRole(NATIONAL_SCOPE_ROLES)` (NATIONAL_ADMIN ou
  MINISTRY_ADMIN). L'INSPECTOR consulte sans pouvoir déclencher de
  recalcul.

## Dimensions et pondérations

Le score (0..100) agrège quatre dimensions normalisées :

| Dimension       | Pondération | Source                                         |
|-----------------|-------------|------------------------------------------------|
| Infrastructure  | 35 %        | Eau, électricité, latrines, état bâtiment, salles utilisables, internet |
| Saturation      | 25 %        | `CapacitySeverity` (Module 2C)                 |
| Équité          | 25 %        | GPI école (filles/garçons)                     |
| Accessibilité   | 15 %        | `ZoneType` (RURAL/PERI_URBAN/URBAN) + distance |

Catégorisation : ≥70 → TRES_HAUTE · 50–69 → HAUTE · 30–49 → MOYENNE ·
<30 → BASSE.

## Patterns réutilisés

* `staffing-kpi-card` (Module 2D) — pattern card KPI + couleur dérivée.
* `equite-region-chart` (Module 1D) — wrapper `SpkApexcharts` (cf.
  `@spk/charts/spk-apexcharts`) pour le radar.
* `simulateur-page` (Module 3B) — orchestrateur `forkJoin` initial +
  signals + `catchError` per call + toast.
* Variables CSS Spruko (`--primary-color`) pour cohérence ; pas de
  surcharge du design system.

## Tests vitest

| Fichier                                  | Nombre |
|------------------------------------------|--------|
| `investment-api.service.spec.ts`         | 7      |
| `investment-kpi-card.spec.ts`            | 2      |
| `investment-detail-panel.spec.ts`        | 4      |
| `investissements-page.spec.ts`           | 2      |
| **Total Module 3C UI**                   | **15** |

## Accessibilité

* KPI card : `role="button"`, `tabindex="0"`, `aria-pressed` reflète
  l'état de filtre.
* Lignes de table : `role="button"` + `keyup.enter` pour activation
  clavier ; `aria-label` mentionne le nom de l'école.
* Sparkbars : `aria-label` global synthétisant les 4 dimensions et leurs
  scores.
* Panneau détail : `aria-label` sur sections, focus piégé au composant
  enfant (à venir si besoin terrain).

## Données personnelles

Le module ne manipule **aucune donnée personnelle d'élève ou de parent**
— uniquement des agrégats école (compteurs, indicateurs infrastructure,
GPI agrégé). Pas d'enjeu RGPD / loi 037/AN/2016 spécifique à ce niveau.
