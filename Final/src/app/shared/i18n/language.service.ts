import { DOCUMENT, Injectable, computed, inject, signal } from '@angular/core';
import { TranslateService } from '@ngx-translate/core';

/**
 * Langues supportées par GESTION-EE (Module 20).
 * - `fr` Français — langue officielle de la Guinée, défaut.
 * - `en` Anglais — langue internationale.
 * - `ff` Pular  — langue nationale.
 * - `sus` Soussou — langue nationale.
 */
export type GeLang = 'fr' | 'en' | 'ff' | 'sus';

export interface GeLangOption {
  code: GeLang;
  label: string;
  nativeLabel: string;
}

export const GE_LANGS: ReadonlyArray<GeLangOption> = [
  { code: 'fr',  label: 'Français', nativeLabel: 'Français' },
  { code: 'en',  label: 'English',  nativeLabel: 'English' },
  { code: 'ff',  label: 'Pular',    nativeLabel: 'Pular' },
  { code: 'sus', label: 'Soussou',  nativeLabel: 'Sosoxui' },
];

const STORAGE_KEY = 'ge.lang';
const DEFAULT_LANG: GeLang = 'fr';
const ALLOWED: ReadonlyArray<GeLang> = GE_LANGS.map((g) => g.code);

/**
 * LanguageService — Module 20
 *
 * Wrapper léger autour de `@ngx-translate` pour :
 * - Persister la langue dans `localStorage` (`ge.lang`).
 * - Exposer un `signal currentLang` consommable par les composants.
 * - Mettre à jour l'attribut `<html lang="…">`.
 *
 * Note : la configuration `ngx-translate` (provider + loader HTTP) est faite
 * dans `app.config.ts`. Ce service se contente d'orchestrer les choix.
 */
@Injectable({ providedIn: 'root' })
export class LanguageService {
  private readonly translate = inject(TranslateService);
  private readonly document = inject(DOCUMENT);

  private readonly _currentLang = signal<GeLang>(this.resolveInitialLang());
  readonly currentLang = this._currentLang.asReadonly();

  readonly available = computed(() => GE_LANGS);

  constructor() {
    this.translate.addLangs(ALLOWED as string[]);
    this.translate.setDefaultLang(DEFAULT_LANG);
    this.applyLang(this._currentLang());
  }

  /** Change la langue active, persiste et déclenche la traduction. */
  setLang(lang: GeLang): void {
    if (!ALLOWED.includes(lang)) {
      return;
    }
    this._currentLang.set(lang);
    try {
      this.document.defaultView?.localStorage?.setItem(STORAGE_KEY, lang);
    } catch {
      // localStorage indisponible : silencieux.
    }
    this.applyLang(lang);
  }

  private applyLang(lang: GeLang): void {
    this.translate.use(lang);
    this.document.documentElement.setAttribute('lang', lang);
  }

  private resolveInitialLang(): GeLang {
    try {
      const stored = this.document.defaultView?.localStorage?.getItem(STORAGE_KEY);
      if (stored && (ALLOWED as ReadonlyArray<string>).includes(stored)) {
        return stored as GeLang;
      }
    } catch {
      // ignore
    }
    return DEFAULT_LANG;
  }
}
