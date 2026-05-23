# Module Census — Dédoublonnage & Normalisation (Module 2)

## Architecture

```
app/modules/census/
├── models.py          # Student, Teacher, StudentTransfer (DB)
├── schemas.py         # Pydantic I/O — inclut field_validator pour normalize
├── normalization.py   # pur — noms, téléphones, birthdate (Module 2)
├── duplicates.py      # pur — scoring composite (Module 2)
├── service.py         # CensusService — orchestration DB + scoring
└── router.py          # FastAPI endpoints
```

Le module reste cohérent avec la convention NestJS d'origine (mêmes shapes de
réponse pour `mapStudent` / `mapTeacher`).

## Règles de dédoublonnage

Le scoring combine 6 features pondérées (cf. `duplicates.py` pour le rationale
complet) :

| Feature        | Poids | Métrique                                    |
|----------------|-------|---------------------------------------------|
| `lastName`     | 0.30  | Jaro-Winkler normalisé                      |
| `firstName`    | 0.20  | Jaro-Winkler normalisé                      |
| `birthDate`    | 0.25  | exact=1.0 / ±1j=0.8 / ±30j=0.4 / sinon=0.0  |
| `guardianPhone`| 0.15  | exact (après E.164 Guinée) = 1.0            |
| `gender`       | 0.05  | exact = 1.0                                 |
| `schoolId`     | 0.05  | exact = 1.0                                 |

### Seuils
- **HIGH ≥ 0.85** → blocage à la création (409 sauf si `?force=true`)
- **MEDIUM ≥ 0.65** → matchings rendus à l'UI pour avertissement
- **LOW < 0.65** → non-doublon, ignoré

### Pipeline de candidats
1. Filtre SQL via `pg_trgm` : `similarity(lower(lastName), lower(:lastName)) > 0.3`
2. Top 20 candidats (ordonné par similarity)
3. Scoring Python-side via `compute_similarity_score`
4. Top 5 retourné, classés par score décroissant

Pas de fusion automatique : c'est de l'inscription d'enfants, une décision
qui doit rester humaine et auditable.

## Format E.164 Guinée

`normalize_phone_guinea(raw)` accepte :

- `622123456` (local 9 chiffres)
- `+224622123456` (E.164)
- `00224622123456` (international avec 00)
- `224622123456` (international sans +)
- `+224 622 12 34 56` (espacé, idem avec tirets/parenthèses)

Retourne `+224XXXXXXXXX` (13 caractères) ou raise `ValueError`. Rejet :
- Codes pays ≠ +224
- Préfixe local ≠ 6 (les fixes ne sont pas pris en charge pour les tuteurs)
- Longueur ≠ 9 chiffres après le code pays
- Caractères non numériques

## Birthdate vs niveau scolaire

`validate_birthdate_for_classroom(birthdate, level)` calcule l'âge à la
**rentrée scolaire (1er octobre)** et vérifie la cohérence :

| Niveau | Âge attendu |
|--------|-------------|
| Maternelle | 3-5 ans   |
| CP1/CP2  | 5-8 ans (avec tolérance) |
| CE1      | 6-8 ans   |
| CE2      | 7-9 ans   |
| CM1      | 8-10 ans  |
| CM2      | 9-11 ans  |

Sans niveau : plage globale 3-16 ans.

## Comportement `/merge`

`POST /api/census/students/{source_id}/merge` avec body `{targetId}` :

1. **RBAC** : ≥ `REGIONAL_ADMIN` (`MERGE_STUDENTS_ROLES`)
2. **Transaction atomique** : déplace tous les enregistrements dépendants
   - `AttendanceRecord`, `Grade`, `ReportCard`, `LibraryLoan`
   - `ParentCommunication`, `StudentParent`, `StudentTransfer`
   - Les `QrCredential` du source sont supprimés
3. **Gestion des contraintes uniques** : pour `Grade` (`assessmentId, studentId`)
   et `ReportCard` (`studentId, periodId`), le **target gagne** — les rows
   conflictuelles du source sont supprimées avant la bascule.
4. **AuditLog** : ligne `MERGE_STUDENTS` avec `{source_id, target_id, transferred: {...counts...}}`
5. **Idempotent** : si `source_id` n'existe plus (404), on retourne le target
   tel quel — permet aux clients de rejouer un appel interrompu.

## Endpoints (Module 2 — nouveaux)

| Méthode | Path                                            | RBAC                       |
|---------|-------------------------------------------------|----------------------------|
| POST    | `/api/census/students/check-duplicates`        | tout user authentifié      |
| POST    | `/api/census/students/{id}/merge`              | ≥ REGIONAL_ADMIN           |
| POST    | `/api/census/students?force=true`              | CENSUS_WRITE_ROLES         |
| POST    | `/api/census/teachers?force=true`              | CENSUS_WRITE_ROLES         |

## Métriques Prometheus

- `gestionee_census_duplicate_check_total{entity}` — appels au moteur
- `gestionee_census_duplicate_blocked_total{entity,level}` — créations bloquées
- `gestionee_census_merge_total{entity,result}` — fusions (ok/not_found/forbidden)

Alertes recommandées :
- `rate(census_duplicate_blocked_total{level="HIGH"}[5m]) > 0.5` : surge inhabituelle de tentatives de doublons (peut signaler un import malformé)
- `rate(census_merge_total{result="forbidden"}[15m]) > 0` : tentatives d'élévation de privilèges
