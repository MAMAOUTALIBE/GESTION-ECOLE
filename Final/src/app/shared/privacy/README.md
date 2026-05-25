# Privacy — Module 5A

Anonymisation contextuelle des noms (élèves, parents, enseignants) côté
frontend Angular GESTION-EE.

## Base légale

- **Loi 037/AN/2016** (Guinée) — Protection des données à caractère personnel.
- **RGPD** — principe de minimisation, accès au moindre privilège.

L'idée : ne révéler l'identité (`Prénom Nom`) qu'aux rôles qui ont un
**besoin opérationnel légitime** de la connaître. Les autres voient des
**initiales** (`A. D.`).

## Règles d'affichage v1

| Rôle                                   | Vue par défaut                                     |
|----------------------------------------|----------------------------------------------------|
| NATIONAL_ADMIN, MINISTRY_ADMIN         | Nom complet partout                                |
| REGIONAL_ADMIN, INSPECTOR              | Nom complet si target.regionId == user.region.id   |
| PREFECTURE_ADMIN, SUB_PREFECTURE_ADMIN | Nom complet si target.regionId == user.region.id   |
| SCHOOL_DIRECTOR, TEACHER, CENSUS_AGENT | Nom complet UNIQUEMENT si target.schoolId == user.school.id |
| Aucun utilisateur connecté             | Initiales                                          |

Ces règles sont implémentées dans `PrivacyService.canSeeFullName(target)`.

## API publique

### `PrivacyService`

```ts
import { PrivacyService } from 'src/app/shared/privacy/privacy.service';

const privacy = inject(PrivacyService);

privacy.canSeeFullName({ schoolId, regionId });   // boolean
privacy.displayName({ firstName, lastName }, target); // "Aïssatou Diallo" ou "A. D."
privacy.initials('Aïssatou', 'Diallo');           // "A. D."
privacy.hasAnyRedaction();                        // true si l'utilisateur voit potentiellement des initiales
```

### `RedactedNamePipe`

```html
<app-something *ngFor="let s of students">
  {{ s | redactedName: { schoolId: s.school.id, regionId: s.school.region?.id } }}
</app-something>
```

Pipe **impur** (recalcule à chaque CD) pour réagir à un login/logout.

### `PrivacyBannerComponent`

Bannière inline à placer **en haut d'une liste** contenant des noms
potentiellement anonymisés :

```html
<app-privacy-banner></app-privacy-banner>
```

Elle ne s'affiche QUE si `PrivacyService.hasAnyRedaction()` retourne `true`.

## Écrans intégrés en Module 5A

- `students.html` — colonne « Élève » + entête QR + transfert + exports CSV/Excel/PDF.
- `parents.html`  — colonne « Parent » + contacts conditionnels + liste élèves liés + recherche.
- `teachers.html` — colonne « Enseignant » + entête QR + exports.
- `equite-critical-schools-table.html` — vérifié, ne contient pas de noms individuels (agrégat par école).

## i18n

Clés ajoutées dans `fr.json`, `en.json`, `ff.json`, `sus.json` :

- `privacy.banner.title`
- `privacy.banner.message`
- `privacy.redacted.label`

## Limite v1 / dette

- La règle « PREFECTURE_ADMIN sur prefecture précise » est approximée
  via regionId (le mapping school→prefecture n'est pas toujours dans le payload).
- Les exports CSV/Excel/PDF sont anonymisés selon les mêmes règles.
- La vraie protection reste **backend** (audit PII étendu Module 5C,
  droit à l'oubli Module 5D).

## Tests

- `privacy.service.spec.ts` (≥ 6 tests, vitest)
- `redacted-name.pipe.spec.ts` (≥ 3 tests, vitest)

Lancer : `npm test` ou `ng test --watch=false`.
