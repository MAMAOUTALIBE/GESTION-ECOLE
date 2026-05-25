import { HttpClient, HttpErrorResponse, provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { firstValueFrom, of } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { environment } from '../../../environments/environment';
import { AuthService, AuthSession } from '../services/auth.service';
import { TokenRefreshService } from '../services/token-refresh.service';
import { refreshInterceptor } from './refresh.interceptor';

function makeSession(refresh = 'rt-1'): AuthSession {
  return {
    accessToken: 'access-old',
    refreshToken: refresh,
    user: {
      id: 'u-1',
      email: 'jean@test.local',
      fullName: 'Jean Test',
      role: 'TEACHER',
      region: null,
      prefecture: null,
      subPrefecture: null,
      school: null,
    },
  };
}

describe('refreshInterceptor', () => {
  let http: HttpClient;
  let httpMock: HttpTestingController;
  let auth: AuthService;
  let tokenRefresh: TokenRefreshService;
  let routerNavigate: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
    routerNavigate = vi.fn().mockResolvedValue(true);

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([refreshInterceptor])),
        provideHttpClientTesting(),
        { provide: Router, useValue: { navigate: routerNavigate } },
      ],
    });

    http = TestBed.inject(HttpClient);
    httpMock = TestBed.inject(HttpTestingController);
    auth = TestBed.inject(AuthService);
    tokenRefresh = TestBed.inject(TokenRefreshService);

    // Installer une session par défaut avec refresh token.
    (auth as unknown as { sessionSubject: { next: (s: AuthSession) => void } }).sessionSubject.next(
      makeSession('rt-1'),
    );
  });

  afterEach(() => {
    httpMock.verify();
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('requête 200 → pass-through, aucun refresh déclenché', async () => {
    const spy = vi.spyOn(tokenRefresh, 'refreshToken');
    const promise = firstValueFrom(http.get(`${environment.apiUrl}/schools`));
    const req = httpMock.expectOne(`${environment.apiUrl}/schools`);
    req.flush({ data: 'ok' });

    await promise;
    expect(spy).not.toHaveBeenCalled();
  });

  it('requête 401 sur endpoint API → refresh + retry → 200', async () => {
    vi.spyOn(tokenRefresh, 'refreshToken').mockReturnValue(of('access-new'));

    const promise = firstValueFrom(http.get(`${environment.apiUrl}/schools`));

    // Premier appel : 401.
    const first = httpMock.expectOne(`${environment.apiUrl}/schools`);
    first.flush({ detail: 'expired' }, { status: 401, statusText: 'Unauthorized' });

    // L'intercepteur retry avec le nouveau token.
    const retry = httpMock.expectOne(`${environment.apiUrl}/schools`);
    expect(retry.request.headers.get('Authorization')).toBe('Bearer access-new');
    retry.flush({ data: 'ok-retried' });

    const result = await promise;
    expect(result).toEqual({ data: 'ok-retried' });
  });

  it('requête 401 sur /auth/login → pas d\'interception (login a échoué)', async () => {
    const spy = vi.spyOn(tokenRefresh, 'refreshToken');

    const promise = firstValueFrom(
      http.post(`${environment.apiUrl}/auth/login`, { email: 'x', password: 'y' }),
    );
    const req = httpMock.expectOne(`${environment.apiUrl}/auth/login`);
    req.flush({ detail: 'invalid_credentials' }, { status: 401, statusText: 'Unauthorized' });

    await expect(promise).rejects.toBeInstanceOf(HttpErrorResponse);
    expect(spy).not.toHaveBeenCalled();
    // Pas de retry attendu.
    httpMock.expectNone(`${environment.apiUrl}/auth/login`);
  });

  it('requête 401 sur /auth/refresh → pas d\'interception (évite loop)', async () => {
    const spy = vi.spyOn(tokenRefresh, 'refreshToken');

    const promise = firstValueFrom(
      http.post(`${environment.apiUrl}/auth/refresh`, { refreshToken: 'rt-1' }),
    );
    const req = httpMock.expectOne(`${environment.apiUrl}/auth/refresh`);
    req.flush({ detail: 'expired' }, { status: 401, statusText: 'Unauthorized' });

    await expect(promise).rejects.toBeInstanceOf(HttpErrorResponse);
    expect(spy).not.toHaveBeenCalled();
    httpMock.expectNone(`${environment.apiUrl}/auth/refresh`);
  });

  it('401 + refresh échoue → erreur originale propagée + navigate /auth/login', async () => {
    // Le vrai TokenRefreshService est appelé : son HTTP /auth/refresh renverra 401.
    const promise = firstValueFrom(http.get(`${environment.apiUrl}/schools`));

    const first = httpMock.expectOne(`${environment.apiUrl}/schools`);
    first.flush({ detail: 'expired' }, { status: 401, statusText: 'Unauthorized' });

    // Le refresh est déclenché et échoue lui aussi.
    const refreshReq = httpMock.expectOne(`${environment.apiUrl}/auth/refresh`);
    refreshReq.flush(
      { detail: 'refresh_expired' },
      { status: 401, statusText: 'Unauthorized' },
    );

    await expect(promise).rejects.toBeInstanceOf(HttpErrorResponse);

    expect(routerNavigate).toHaveBeenCalledWith(['/auth/login']);
    expect(auth.session).toBeNull();
  });

  it('requête 500 → pas de refresh (n\'intervient que sur 401)', async () => {
    const spy = vi.spyOn(tokenRefresh, 'refreshToken');
    const promise = firstValueFrom(http.get(`${environment.apiUrl}/schools`));
    const req = httpMock.expectOne(`${environment.apiUrl}/schools`);
    req.flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });

    await expect(promise).rejects.toBeInstanceOf(HttpErrorResponse);
    expect(spy).not.toHaveBeenCalled();
  });
});
