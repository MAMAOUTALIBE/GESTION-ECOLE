import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { TranslateLoader, TranslateModule } from '@ngx-translate/core';
import { Observable, of, BehaviorSubject } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthService, AuthSession } from '../services/auth.service';
import { ConsentModalComponent } from './consent-modal.component';
import { ConsentService, ConsentStatus } from './consent.service';

class FakeLoader implements TranslateLoader {
  getTranslation(_lang: string): Observable<Record<string, unknown>> {
    return of({
      consent: {
        title: 'Politique de confidentialite',
        intro: 'Bienvenue.',
        bullets: {
          data: 'Donnees.',
          purpose: 'Finalite.',
          retention: 'Retention.',
          access: 'Acces logs.',
          erasure: 'Oubli.',
          minors: 'Mineurs.',
          legal: 'Loi.',
          contact: 'DPO.',
        },
        readFull: 'Lire la politique.',
        versionLabel: 'Version',
        accept: "J'accepte",
        decline: 'Se deconnecter',
      },
      common: { loading: 'Chargement...' },
    });
  }
}

function buildConsentStub(initial: ConsentStatus): ConsentService {
  const subject = new BehaviorSubject<ConsentStatus | null>(initial);
  return {
    status$: subject.asObservable(),
    requiredVersion: initial.currentRequiredVersion,
    needsAcceptance: initial.needsAcceptance,
    getStatus: vi.fn(),
    accept: vi.fn().mockReturnValue(
      of({ ...initial, needsAcceptance: false, version: initial.currentRequiredVersion }),
    ),
    clear: vi.fn(),
  } as unknown as ConsentService;
}

function buildAuthStub(authenticated: boolean): AuthService {
  const session: AuthSession | null = authenticated
    ? ({
        accessToken: 'tok',
        user: {
          id: 'u1',
          email: 'u@test.local',
          fullName: 'U',
          role: 'TEACHER',
        },
      } as AuthSession)
    : null;
  const subject = new BehaviorSubject<AuthSession | null>(session);
  return {
    session$: subject.asObservable(),
    get isAuthenticated() {
      return authenticated;
    },
    logout: vi.fn(),
  } as unknown as AuthService;
}

describe('ConsentModalComponent', () => {
  let fixture: ComponentFixture<ConsentModalComponent>;

  function setup(opts: { needsAcceptance: boolean; authenticated: boolean }) {
    const status: ConsentStatus = {
      version: opts.needsAcceptance ? null : '2026-05-01',
      acceptedAt: null,
      needsAcceptance: opts.needsAcceptance,
      currentRequiredVersion: '2026-05-01',
    };
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [
        ConsentModalComponent,
        TranslateModule.forRoot({
          loader: { provide: TranslateLoader, useClass: FakeLoader },
          defaultLanguage: 'fr',
        }),
      ],
      providers: [
        provideRouter([]),
        { provide: ConsentService, useValue: buildConsentStub(status) },
        { provide: AuthService, useValue: buildAuthStub(opts.authenticated) },
      ],
    });
    fixture = TestBed.createComponent(ConsentModalComponent);
    fixture.detectChanges();
  }

  it("affiche le modal si needsAcceptance=true et l'utilisateur est connecte", () => {
    setup({ needsAcceptance: true, authenticated: true });
    const modal = fixture.nativeElement.querySelector('.ge-consent-modal');
    expect(modal).not.toBeNull();
    const title = fixture.nativeElement.querySelector('#ge-consent-title');
    expect(title.textContent).toContain('confidentialite');
  });

  it("cache le modal si l'utilisateur a deja consenti", () => {
    setup({ needsAcceptance: false, authenticated: true });
    const modal = fixture.nativeElement.querySelector('.ge-consent-modal');
    expect(modal).toBeNull();
  });

  it("cache le modal si l'utilisateur n'est pas connecte", () => {
    setup({ needsAcceptance: true, authenticated: false });
    const modal = fixture.nativeElement.querySelector('.ge-consent-modal');
    expect(modal).toBeNull();
  });
});
