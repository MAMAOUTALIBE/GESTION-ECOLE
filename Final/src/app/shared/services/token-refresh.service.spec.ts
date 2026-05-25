import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { environment } from '../../../environments/environment';
import { AuthService, AuthSession, AuthUser } from './auth.service';
import { TokenRefreshService } from './token-refresh.service';

function makeUser(): AuthUser {
  return {
    id: 'u-1',
    email: 'jean@gestion-ee.local',
    fullName: 'Jean Test',
    role: 'TEACHER',
    region: null,
    prefecture: null,
    subPrefecture: null,
    school: null,
  };
}

function makeSession(refresh: string | null = 'rt-abc'): AuthSession {
  return {
    accessToken: 'old-access-token',
    user: makeUser(),
    ...(refresh ? { refreshToken: refresh } : {}),
  };
}

describe('TokenRefreshService', () => {
  let service: TokenRefreshService;
  let httpMock: HttpTestingController;
  let auth: AuthService;
  let routerNavigate: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
    routerNavigate = vi.fn().mockResolvedValue(true);

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: Router, useValue: { navigate: routerNavigate } },
      ],
    });

    auth = TestBed.inject(AuthService);
    httpMock = TestBed.inject(HttpTestingController);
    service = TestBed.inject(TokenRefreshService);
  });

  afterEach(() => {
    httpMock.verify();
    localStorage.clear();
  });

  it('appelle POST /api/auth/refresh avec le refresh token courant', async () => {
    // Stub : on positionne une session avec refresh token via le subject interne.
    (auth as unknown as { sessionSubject: { next: (s: AuthSession) => void } }).sessionSubject.next(
      makeSession('rt-abc'),
    );

    const promise = firstValueFrom(service.refreshToken());

    const req = httpMock.expectOne(`${environment.apiUrl}/auth/refresh`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ refreshToken: 'rt-abc' });

    req.flush({
      accessToken: 'new-access',
      refreshToken: 'new-refresh',
      user: makeUser(),
    });

    await promise;
  });

  it('met à jour AuthService.session avec les nouveaux tokens', async () => {
    (auth as unknown as { sessionSubject: { next: (s: AuthSession) => void } }).sessionSubject.next(
      makeSession('rt-old'),
    );

    const promise = firstValueFrom(service.refreshToken());
    const req = httpMock.expectOne(`${environment.apiUrl}/auth/refresh`);
    req.flush({
      accessToken: 'new-access-xyz',
      refreshToken: 'new-refresh-xyz',
      user: makeUser(),
    });

    await promise;

    expect(auth.token).toBe('new-access-xyz');
    expect(auth.refreshToken).toBe('new-refresh-xyz');
    expect(auth.currentUser?.id).toBe('u-1');
  });

  it('émet le nouveau access token comme valeur du Observable', async () => {
    (auth as unknown as { sessionSubject: { next: (s: AuthSession) => void } }).sessionSubject.next(
      makeSession('rt-1'),
    );

    const promise = firstValueFrom(service.refreshToken());
    const req = httpMock.expectOne(`${environment.apiUrl}/auth/refresh`);
    req.flush({
      accessToken: 'emitted-token',
      refreshToken: 'rt-2',
      user: makeUser(),
    });

    const emitted = await promise;
    expect(emitted).toBe('emitted-token');
  });

  it('mutualise deux appels concurrents en UN seul HTTP call', async () => {
    (auth as unknown as { sessionSubject: { next: (s: AuthSession) => void } }).sessionSubject.next(
      makeSession('rt-shared'),
    );

    const p1 = firstValueFrom(service.refreshToken());
    const p2 = firstValueFrom(service.refreshToken());

    // Un seul HTTP attendu malgré 2 appels concurrents.
    const req = httpMock.expectOne(`${environment.apiUrl}/auth/refresh`);
    expect(req.request.body).toEqual({ refreshToken: 'rt-shared' });
    req.flush({
      accessToken: 'shared-access',
      refreshToken: 'shared-refresh',
      user: makeUser(),
    });

    const [v1, v2] = await Promise.all([p1, p2]);
    expect(v1).toBe('shared-access');
    expect(v2).toBe('shared-access');
  });

  it('clear session + navigate /auth/login si le refresh échoue (401)', async () => {
    (auth as unknown as { sessionSubject: { next: (s: AuthSession) => void } }).sessionSubject.next(
      makeSession('rt-expired'),
    );

    const promise = firstValueFrom(service.refreshToken());
    const req = httpMock.expectOne(`${environment.apiUrl}/auth/refresh`);
    req.flush({ detail: 'expired' }, { status: 401, statusText: 'Unauthorized' });

    await expect(promise).rejects.toBeDefined();

    expect(auth.session).toBeNull();
    expect(auth.token).toBeNull();
    expect(routerNavigate).toHaveBeenCalledWith(['/auth/login']);
  });

  it('erreur immédiate sans refresh token stocké (pas de HTTP émis)', async () => {
    // Aucune session installée → pas de refresh token.
    expect(auth.refreshToken).toBeNull();

    await expect(firstValueFrom(service.refreshToken())).rejects.toThrow('NO_REFRESH_TOKEN');

    // Aucun HTTP ne doit avoir été émis : verify() dans afterEach le confirmera.
    httpMock.expectNone(`${environment.apiUrl}/auth/refresh`);
  });

  it('permet un second cycle de refresh après un succès', async () => {
    (auth as unknown as { sessionSubject: { next: (s: AuthSession) => void } }).sessionSubject.next(
      makeSession('rt-1'),
    );

    const p1 = firstValueFrom(service.refreshToken());
    const req1 = httpMock.expectOne(`${environment.apiUrl}/auth/refresh`);
    req1.flush({ accessToken: 'access-A', refreshToken: 'rt-2', user: makeUser() });
    await p1;

    // Deuxième refresh indépendant après que le premier soit complet.
    const p2 = firstValueFrom(service.refreshToken());
    const req2 = httpMock.expectOne(`${environment.apiUrl}/auth/refresh`);
    expect(req2.request.body).toEqual({ refreshToken: 'rt-2' });
    req2.flush({ accessToken: 'access-B', refreshToken: 'rt-3', user: makeUser() });
    const v2 = await p2;
    expect(v2).toBe('access-B');
    expect(auth.token).toBe('access-B');
  });
});
