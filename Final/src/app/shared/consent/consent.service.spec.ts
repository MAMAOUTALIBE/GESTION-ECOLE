import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { environment } from '../../../environments/environment';
import { ConsentService, ConsentStatus } from './consent.service';

describe('ConsentService', () => {
  let service: ConsentService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(ConsentService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  it('getStatus appelle GET /api/consent/status et met a jour status$', () => {
    let latest: ConsentStatus | null = null;
    service.status$.subscribe((s) => (latest = s));

    service.getStatus().subscribe();
    const req = httpMock.expectOne(`${environment.apiUrl}/consent/status`);
    expect(req.request.method).toBe('GET');

    const payload: ConsentStatus = {
      version: null,
      acceptedAt: null,
      needsAcceptance: true,
      currentRequiredVersion: '2026-05-01',
    };
    req.flush(payload);

    expect(latest).toEqual(payload);
    expect(service.needsAcceptance).toBe(true);
    expect(service.requiredVersion).toBe('2026-05-01');
  });

  it('accept envoie POST /api/consent/accept avec la version', () => {
    service.accept('2026-05-01').subscribe();
    const req = httpMock.expectOne(`${environment.apiUrl}/consent/accept`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ consentVersion: '2026-05-01' });

    req.flush({
      version: '2026-05-01',
      acceptedAt: '2026-05-25T10:00:00Z',
      needsAcceptance: false,
      currentRequiredVersion: '2026-05-01',
    });

    expect(service.needsAcceptance).toBe(false);
  });

  it("accept met a jour status$ avec needsAcceptance=false", () => {
    let latest: ConsentStatus | null = null;
    service.status$.subscribe((s) => (latest = s));

    service.accept('2026-05-01').subscribe();
    const req = httpMock.expectOne(`${environment.apiUrl}/consent/accept`);
    req.flush({
      version: '2026-05-01',
      acceptedAt: '2026-05-25T10:00:00Z',
      needsAcceptance: false,
      currentRequiredVersion: '2026-05-01',
    });

    expect(latest).not.toBeNull();
    expect(latest!.needsAcceptance).toBe(false);
    expect(latest!.version).toBe('2026-05-01');
  });

  it('clear() remet le cache a null', () => {
    service.getStatus().subscribe();
    const req = httpMock.expectOne(`${environment.apiUrl}/consent/status`);
    req.flush({
      version: '2026-05-01',
      acceptedAt: '2026-05-25T10:00:00Z',
      needsAcceptance: false,
      currentRequiredVersion: '2026-05-01',
    });
    expect(service.needsAcceptance).toBe(false);

    service.clear();
    expect(service.requiredVersion).toBeNull();
    expect(service.needsAcceptance).toBe(false);
  });
});
