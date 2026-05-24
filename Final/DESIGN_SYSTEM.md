# GE-Design — Design system GESTION-EE (Module 20)

GE-Design est la couche additive qui pose les fondations du design system
national de GESTION-EE. Elle vit **à côté** du template Spruko : aucun fichier
Spruko n'est modifié, tout est préfixé `ge-*` (tokens, classes, composants,
clés localStorage).

---

## Sommaire

1. Structure
2. Tokens
3. Mode sombre (`ThemeService`)
4. Internationalisation (`LanguageService`)
5. Composants vitrine `Ge*`
6. Comment ajouter un nouveau composant
7. Comment ajouter une nouvelle clé i18n
8. Comment ajouter une nouvelle langue

---

## 1. Structure

```
Final/
├── src/
│   ├── styles/
│   │   ├── ge-tokens.scss          ← variables CSS (light + dark)
│   │   └── _ge-helpers.scss        ← classes utilitaires (.ge-card, .ge-button…)
│   ├── styles.scss                 ← @forward "styles/ge-tokens" en tête
│   ├── assets/i18n/
│   │   ├── fr.json                 ← langue par défaut
│   │   ├── en.json
│   │   ├── ff.json                 ← Pular
│   │   └── sus.json                ← Soussou
│   └── app/
│       ├── design-system/
│       │   ├── ge-button.component.ts
│       │   ├── ge-card.component.ts
│       │   ├── ge-badge.component.ts
│       │   └── design-system-demo.component.ts  ← /design-system
│       └── shared/
│           ├── theme/theme.service.ts
│           ├── i18n/i18n.providers.ts
│           ├── i18n/language.service.ts
│           └── components/
│               ├── theme-toggle/
│               └── language-switcher/
```

Page vitrine accessible à l'URL `/design-system` une fois connecté.

---

## 2. Tokens

Tous les tokens sont des **variables CSS** déclarées sous `:root` dans
`styles/ge-tokens.scss`. Le passage en dark mode redéfinit le sous-ensemble
nécessaire sous `[data-theme="dark"]`.

| Famille     | Variables principales                                                                       |
|-------------|---------------------------------------------------------------------------------------------|
| Couleurs    | `--ge-color-primary`, `--ge-color-secondary`, `--ge-color-accent`, `--ge-color-surface`     |
| Sémantique  | `--ge-color-success`, `--ge-color-warning`, `--ge-color-danger`, `--ge-color-info`          |
| Texte       | `--ge-color-text`, `--ge-color-text-muted`, `--ge-color-text-inverse`                       |
| Spacing     | `--ge-space-1` (4 px) ... `--ge-space-12` (48 px)                                           |
| Typographie | `--ge-font-sans`, `--ge-font-display`, `--ge-font-size-{xs,sm,md,lg,xl,2xl}`                |
| Radius      | `--ge-radius-sm`, `--ge-radius-md`, `--ge-radius-lg`, `--ge-radius-pill`                    |
| Ombres      | `--ge-shadow-0` ... `--ge-shadow-3`                                                         |
| Transitions | `--ge-transition-fast`, `--ge-transition-base`                                              |

**Règle d'or :** un composant `Ge*` ne référence JAMAIS une variable Spruko
(`--primary-color`, `--body-bg`, etc.). Inversement, on ne réassigne jamais
une variable Spruko depuis GE-Design.

### Palette africaine premium

- Ocre Sahel `#c8784a` — terre cuite, latérite (primaire)
- Vert savane `#2f6b3f` — végétation, acacia (secondaire)
- Indigo nuit `#1f2c4d` — boubou, ciel nocturne (accent)
- Blanc kaolin `#f7f4ee` — argile blanche (surface light)
- Noir charbon `#14181f` — fond sombre (surface dark)

---

## 3. Mode sombre — `ThemeService`

```ts
import { ThemeService } from 'app/shared/theme/theme.service';

const theme = inject(ThemeService);
theme.setTheme('dark');     // 'light' | 'dark' | 'auto'
theme.cycle();              // light → dark → auto → light
theme.currentTheme();       // signal<'light' | 'dark'>
```

- Persistance : `localStorage['ge.theme']`.
- Détection auto : `window.matchMedia('(prefers-color-scheme: dark)')`.
- Application : pose `data-theme="dark"` sur `<html>` quand nécessaire.

Le bouton `<app-theme-toggle />` (header) cycle les 3 modes.

---

## 4. Internationalisation — `LanguageService`

Basée sur `@ngx-translate/core` + `@ngx-translate/http-loader` qui charge les
fichiers JSON depuis `/assets/i18n/{lang}.json`.

```ts
import { LanguageService } from 'app/shared/i18n/language.service';

const lang = inject(LanguageService);
lang.setLang('en');         // 'fr' | 'en' | 'ff' | 'sus'
lang.currentLang();         // signal<GeLang>
```

- Défaut : `fr` (Français).
- Persistance : `localStorage['ge.lang']`.
- L'attribut `<html lang>` est tenu à jour automatiquement.

Dans un template :

```html
{{ 'common.save' | translate }}
```

Le select `<app-language-switcher />` (header) expose les 4 langues.

---

## 5. Composants vitrine

| Composant                | Sélecteur               | Props clés                                                 |
|--------------------------|-------------------------|------------------------------------------------------------|
| `GeButtonComponent`      | `<ge-button>`           | `variant: primary\|secondary\|ghost`, `size: sm\|md\|lg`   |
| `GeCardComponent`        | `<ge-card>`             | `elevation: 0\|1\|2\|3`                                    |
| `GeBadgeComponent`       | `<ge-badge>`            | `variant: success\|warning\|danger\|info`                  |

Exemple :

```html
<ge-card [elevation]="2">
  <h3>Validation requise</h3>
  <p>3 dossiers en attente.</p>
  <ge-button variant="primary">Ouvrir</ge-button>
  <ge-badge variant="warning">En attente</ge-badge>
</ge-card>
```

---

## 6. Ajouter un nouveau composant GE

1. Créer le fichier `Final/src/app/design-system/ge-xxx.component.ts`.
2. Le marquer `standalone: true`, `changeDetection: OnPush`.
3. N'utiliser QUE les variables `--ge-*` (jamais Spruko).
4. Si une classe utilitaire est nécessaire, l'ajouter dans
   `Final/src/styles/_ge-helpers.scss` sous la forme `.ge-xxx`.
5. Référencer le composant dans `design-system-demo.component.ts`.
6. Ajouter au moins un `*.spec.ts` (modèle dans `theme-toggle.component.spec.ts`).

---

## 7. Ajouter une nouvelle clé i18n

1. Ajouter la clé dans **tous** les fichiers `Final/src/assets/i18n/*.json`
   (fr, en, ff, sus). Utiliser un chemin pointé : `module.section.label`.
2. Dans le template, utiliser le pipe : `{{ 'module.section.label' | translate }}`.
3. Si on traduit dynamiquement en TS : `inject(TranslateService).instant('…')`.

Convention : pas plus de 3 niveaux d'imbrication, kebab-case dans la clé.

---

## 8. Ajouter une nouvelle langue

1. Créer `Final/src/assets/i18n/{code}.json` avec la même structure que `fr.json`.
2. Ajouter une entrée dans la constante `GE_LANGS` de
   `Final/src/app/shared/i18n/language.service.ts` :

   ```ts
   { code: 'xx', label: 'NomFr', nativeLabel: 'NomNatif' }
   ```

3. Étendre le type `GeLang` (`'fr' | 'en' | 'ff' | 'sus' | 'xx'`).
4. Rebuild — le `<app-language-switcher />` exposera la nouvelle option.

---

## Tests

```bash
cd Final
$HOME/.nvm/versions/node/v20.19.5/bin/node ./node_modules/.bin/ng test
```

Couvre :
- `ThemeService` — défaut, persistance, cycle, valeurs invalides.
- `LanguageService` — défaut FR, persistance, `<html lang>`, valeurs invalides.
- `ThemeToggleComponent` — rendu, cycle au clic.
