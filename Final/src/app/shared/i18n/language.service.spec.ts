import { HttpClient } from '@angular/common/http';
import { provideHttpClient } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { TranslateLoader, TranslateModule } from '@ngx-translate/core';
import { Observable, of } from 'rxjs';
import { beforeEach, describe, expect, it } from 'vitest';

import { LanguageService } from './language.service';

class FakeLoader implements TranslateLoader {
  getTranslation(lang: string): Observable<Record<string, unknown>> {
    return of({
      app: { title: lang === 'fr' ? 'GESTION-EE' : 'GESTION-EE' },
      common: { save: lang === 'en' ? 'Save' : 'Enregistrer' },
    });
  }
}

describe('LanguageService', () => {
  beforeEach(() => {
    try {
      window.localStorage.removeItem('ge.lang');
    } catch {
      // ignore
    }
    document.documentElement.removeAttribute('lang');
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [
        TranslateModule.forRoot({
          loader: { provide: TranslateLoader, useClass: FakeLoader },
        }),
      ],
      providers: [provideHttpClient()],
    });
    // satisfait l'inject token
    void TestBed.inject(HttpClient);
  });

  it('defaults to French', () => {
    const svc = TestBed.inject(LanguageService);
    expect(svc.currentLang()).toBe('fr');
  });

  it('persists chosen language in localStorage', () => {
    const svc = TestBed.inject(LanguageService);
    svc.setLang('en');
    expect(window.localStorage.getItem('ge.lang')).toBe('en');
    expect(svc.currentLang()).toBe('en');
  });

  it('sets <html lang> attribute when language changes', () => {
    const svc = TestBed.inject(LanguageService);
    svc.setLang('ff');
    expect(document.documentElement.getAttribute('lang')).toBe('ff');
  });

  it('ignores unsupported languages', () => {
    const svc = TestBed.inject(LanguageService);
    svc.setLang('en');
    // @ts-expect-error — valeur non listée
    svc.setLang('xx');
    expect(svc.currentLang()).toBe('en');
  });

  it('exposes the 4 supported languages', () => {
    const svc = TestBed.inject(LanguageService);
    const codes = svc.available().map((l) => l.code);
    expect(codes).toEqual(['fr', 'en', 'ff', 'sus']);
  });
});
