# Module 1A — Enrollment désagrégé

Fondation Phase 1 carte scolaire IIPE. Stocke les effectifs déclarés
annuellement par établissement, désagrégés par **niveau scolaire × genre**.

## Contexte métier

Sans désagrégation, impossible de produire les indicateurs qui orientent
les décisions publiques :

- **Indice de parité fille / garçon (GPI)** — Module 1B.
- **Dashboard équité** par région / niveau — Module 1D.
- **Projection cohorte** (flux d'élèves CP1 → CM2 sur 6 ans) — Phase 2.

Le déclaratif officiel (recensement annuel) reste la **source de vérité**
pour le pilotage cabinet : la table `Student` est partielle (la fiche
peut prendre des semaines à arriver depuis la brousse) et destinée à
être anonymisée / purgée pour respecter le RGPD enfants.

## Modèle

### Table `Enrollment`

| Colonne          | Type                       | Notes                                    |
|------------------|----------------------------|------------------------------------------|
| `id`             | cuid (PK)                  |                                          |
| `schoolYearId`   | FK SchoolYear              |                                          |
| `schoolId`       | FK School                  |                                          |
| `classLevel`     | enum EnrollmentClassLevel  | maternelle 1/2/3 + CP1..CM2              |
| `gender`         | enum Gender                | FEMALE / MALE / OTHER                    |
| `count`          | INTEGER ≥ 0                | validé Pydantic + service                |
| `source`         | enum EnrollmentSource      | CENSUS_DECLARED / COMPUTED / IMPORT      |
| `recordedAt`     | timestamptz                | horloge serveur, pas client              |
| `recordedById`   | FK User (nullable)         | nullable = imports historiques            |
| `notes`          | text (≤ 500)               |                                          |
| `createdAt`      | timestamptz                | TimestampMixin                            |
| `updatedAt`      | timestamptz                | TimestampMixin                            |

### Contraintes

- **Unique** `(schoolYearId, schoolId, classLevel, gender, source)` — autorise
  la coexistence d'une `CENSUS_DECLARED` et d'une `COMPUTED_FROM_STUDENTS`
  pour la même cellule (cross-check data quality).
- Indexes : `(schoolYearId, schoolId)` (UI saisie), `(schoolYearId, classLevel, gender)`
  (agrégats nationaux).

## Endpoints

| Méthode | Route                                   | RBAC                                                |
|---------|------------------------------------------|-----------------------------------------------------|
| POST    | `/api/enrollment`                       | `ENROLLMENT_WRITE_ROLES` (admins + CENSUS_AGENT + SCHOOL_DIRECTOR) |
| POST    | `/api/enrollment/bulk`                  | idem                                                 |
| GET     | `/api/enrollment/school/{school_id}`    | authentifié (scope RBAC automatique)                 |
| GET     | `/api/enrollment/aggregate`             | authentifié (scope RBAC automatique)                 |
| POST    | `/api/enrollment/compute-from-students` | `NATIONAL_ADMIN` ou `MINISTRY_ADMIN`                 |

- `TEACHER` et `INSPECTOR` ne peuvent pas écrire (pas leur métier).
- `bulk_record` plafonné à 200 items pour éviter de bloquer la transaction.
- `aggregate` parallélise 3 sous-requêtes (`asyncio.gather`) : agrégat par
  niveau, par genre, breakdown niveau × genre.

## RBAC territorial

Mêmes patterns que le module `census` :

- `NATIONAL_ADMIN` / `MINISTRY_ADMIN` : portée pays.
- `REGIONAL_ADMIN` / `INSPECTOR` : portée région (via `user.regionId`).
- `PREFECTURE_ADMIN` : portée préfecture.
- `SUB_PREFECTURE_ADMIN` : portée sous-préfecture.
- `SCHOOL_DIRECTOR` / `TEACHER` / `CENSUS_AGENT` : portée école (via `user.schoolId`).

Lecture comme écriture appliquent ces filtres : un `SCHOOL_DIRECTOR` ne
voit que les rows de son école même via `/aggregate`.

## Conflit déclaratif vs calculé

| Source                     | Origine                                      | Confiance               |
|----------------------------|----------------------------------------------|-------------------------|
| `CENSUS_DECLARED`          | Saisie agent recensement / SCHOOL_DIRECTOR   | Vérité officielle       |
| `COMPUTED_FROM_STUDENTS`   | Recalcul depuis `Student` (admin central)    | Signal data quality     |
| `IMPORT`                   | Bulk historique avant migration logiciel     | Variable (notes)        |

Le `/aggregate` filtre par `source` (default = `CENSUS_DECLARED`). On peut
demander explicitement `source=COMPUTED_FROM_STUDENTS` pour comparer.

## GPI (Gender Parity Index)

Calculé côté agrégat par niveau : `gpi = girls / boys` (None si boys = 0).
Une valeur > 1 signifie plus de filles que de garçons à ce niveau.
L'OMS / UNESCO considère [0.97, 1.03] comme parité acceptable.

## Migration

`0023_enrollment` crée les 2 enums Postgres (`EnrollmentClassLevel`,
`EnrollmentSource`), la table `Enrollment`, ses 2 indexes et sa contrainte
d'unicité. Downgrade complet (drop table + drop enums).

## Backlog

| Issue   | Description                                                                |
|---------|----------------------------------------------------------------------------|
| —       | Aucun pour Module 1A (voir Module 1B pour GPI complet, 1D pour dashboard). |
