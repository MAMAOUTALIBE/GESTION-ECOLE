import { DOCUMENT, Injectable, computed, inject, signal } from '@angular/core';

/**
 * Trois modes acceptés :
 * - `light` : force le thème clair.
 * - `dark`  : force le thème sombre.
 * - `auto`  : suit `prefers-color-scheme` du système.
 */
export type GeTheme = 'light' | 'dark' | 'auto';

const STORAGE_KEY = 'ge.theme';
const ALLOWED: ReadonlyArray<GeTheme> = ['light', 'dark', 'auto'];

/**
 * ThemeService — Module 20
 *
 * Gère le mode clair / sombre via l'attribut `data-theme` posé sur `<html>`.
 * - Persiste le choix utilisateur dans `localStorage` sous la clé `ge.theme`.
 * - Détecte `prefers-color-scheme: dark` au démarrage et lorsqu'il change
 *   (uniquement quand le mode courant est `auto`).
 * - N'écrase pas l'attribut Spruko `data-theme-mode` (qui pilote le template
 *   Spruko). Le toggle GE est complémentaire et n'affecte que les composants
 *   qui consomment les variables `--ge-*`.
 */
@Injectable({ providedIn: 'root' })
export class ThemeService {
  private readonly document = inject(DOCUMENT);

  /** Choix utilisateur — `light`, `dark` ou `auto`. */
  private readonly _theme = signal<GeTheme>(this.readStoredTheme());

  /** Préférence système (utile seulement en mode `auto`). */
  private readonly _systemPrefersDark = signal<boolean>(this.detectSystemDark());

  /** Theme effectivement appliqué (résout `auto` -> `light`/`dark`). */
  readonly currentTheme = computed<'light' | 'dark'>(() => {
    const t = this._theme();
    if (t === 'auto') {
      return this._systemPrefersDark() ? 'dark' : 'light';
    }
    return t;
  });

  /** Mode brut (incluant `auto`) pour le composant toggle. */
  readonly mode = this._theme.asReadonly();

  constructor() {
    this.applyTheme();
    this.watchSystemPreference();
  }

  /**
   * Met à jour le thème, persiste la valeur et applique l'attribut HTML.
   * Toute valeur inconnue est ignorée (no-op) pour éviter de corrompre l'état.
   */
  setTheme(value: GeTheme): void {
    if (!ALLOWED.includes(value)) {
      return;
    }
    this._theme.set(value);
    try {
      this.document.defaultView?.localStorage?.setItem(STORAGE_KEY, value);
    } catch {
      // localStorage indisponible (mode privé strict) : silencieux.
    }
    this.applyTheme();
  }

  /** Cycle light -> dark -> auto -> light (utilisé par le bouton toggle). */
  cycle(): void {
    const order: GeTheme[] = ['light', 'dark', 'auto'];
    const idx = order.indexOf(this._theme());
    const next = order[(idx + 1) % order.length];
    this.setTheme(next);
  }

  /** Applique l'attribut `data-theme` sur la racine HTML. */
  private applyTheme(): void {
    const effective = this.currentTheme();
    const root = this.document.documentElement;
    if (effective === 'dark') {
      root.setAttribute('data-theme', 'dark');
    } else {
      root.removeAttribute('data-theme');
    }
  }

  private readStoredTheme(): GeTheme {
    try {
      const win = this.document.defaultView;
      const stored = win?.localStorage?.getItem(STORAGE_KEY);
      if (stored && (ALLOWED as ReadonlyArray<string>).includes(stored)) {
        return stored as GeTheme;
      }
    } catch {
      // Ignore.
    }
    return 'auto';
  }

  private detectSystemDark(): boolean {
    const win = this.document.defaultView;
    if (!win || typeof win.matchMedia !== 'function') {
      return false;
    }
    return win.matchMedia('(prefers-color-scheme: dark)').matches;
  }

  private watchSystemPreference(): void {
    const win = this.document.defaultView;
    if (!win || typeof win.matchMedia !== 'function') {
      return;
    }
    const mql = win.matchMedia('(prefers-color-scheme: dark)');
    const listener = (e: MediaQueryListEvent) => {
      this._systemPrefersDark.set(e.matches);
      if (this._theme() === 'auto') {
        this.applyTheme();
      }
    };
    // Compatible navigateurs modernes & anciens.
    if (typeof mql.addEventListener === 'function') {
      mql.addEventListener('change', listener);
    } else if (typeof (mql as MediaQueryList & { addListener?: (l: (e: MediaQueryListEvent) => void) => void }).addListener === 'function') {
      (mql as MediaQueryList & { addListener: (l: (e: MediaQueryListEvent) => void) => void }).addListener(listener);
    }
  }
}
