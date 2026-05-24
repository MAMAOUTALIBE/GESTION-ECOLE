import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { environment } from '../../../../environments/environment';
import { EnrollmentApiService } from './enrollment-api.service';

describe('EnrollmentApiService', () => {
  let service: EnrollmentApiService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(EnrollmentApiService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  it('getNationalGpi appelle /enrollment/gpi avec scope=NATIONAL', () => {
    service.getNationalGpi().subscribe();
    const req = httpMock.expectOne(
      (r) => r.url === `${environment.apiUrl}/enrollment/gpi`,
    );
    expect(req.request.method).toBe('GET');
    expect(req.request.params.get('scope')).toBe('NATIONAL');
    expect(req.request.params.get('schoolYearId')).toBeNull();
    req.flush({
      scope: 'NATIONAL',
      entityId: null,
      schoolYearId: 'SY-2025',
      girlsCount: 100,
      boysCount: 110,
      gpi: '0.9091',
      severity: 'NORMAL',
      computedAt: '2026-05-24T08:00:00Z',
    });
  });

  it('getRegionalGpi inclut entityId + schoolYearId', () => {
    service.getRegionalGpi('REG-001', 'SY-2025').subscribe();
    const req = httpMock.expectOne(
      (r) => r.url === `${environment.apiUrl}/enrollment/gpi`,
    );
    expect(req.request.params.get('scope')).toBe('REGIONAL');
    expect(req.request.params.get('entityId')).toBe('REG-001');
    expect(req.request.params.get('schoolYearId')).toBe('SY-2025');
    req.flush({
      scope: 'REGIONAL',
      entityId: 'REG-001',
      schoolYearId: 'SY-2025',
      girlsCount: 50,
      boysCount: 60,
      gpi: 0.8333,
      severity: 'WARNING_GIRLS',
      computedAt: '2026-05-24T08:00:00Z',
    });
  });

  it('getCriticalSchools propage le paramètre limit', () => {
    service.getCriticalSchools('SY-2025', 5).subscribe();
    const req = httpMock.expectOne(
      (r) => r.url === `${environment.apiUrl}/enrollment/gpi/critical-schools`,
    );
    expect(req.request.params.get('schoolYearId')).toBe('SY-2025');
    expect(req.request.params.get('limit')).toBe('5');
    req.flush([]);
  });

  it('getAggregateByZone force byZoneType=true', () => {
    service.getAggregateByZone('SY-2025').subscribe();
    const req = httpMock.expectOne(
      (r) => r.url === `${environment.apiUrl}/enrollment/aggregate`,
    );
    expect(req.request.params.get('byZoneType')).toBe('true');
    expect(req.request.params.get('scope')).toBe('NATIONAL');
    expect(req.request.params.get('schoolYearId')).toBe('SY-2025');
    req.flush({
      scope: 'NATIONAL',
      schoolYearId: 'SY-2025',
      total: 0,
      byLevel: [],
      byGender: [],
      breakdown: [],
      byZoneType: [],
    });
  });

  it('getUrbanRuralGap tape l\'endpoint cockpit', () => {
    service.getUrbanRuralGap('SY-2025').subscribe();
    const req = httpMock.expectOne(
      (r) => r.url === `${environment.apiUrl}/cockpit/kpis/urban-rural-gap`,
    );
    expect(req.request.params.get('schoolYearId')).toBe('SY-2025');
    req.flush({
      schoolYearId: 'SY-2025',
      urbanGpi: '1.01',
      ruralGpi: '0.82',
      periUrbanGpi: null,
      deltaGpi: '0.19',
      urbanGirlsCount: 0,
      urbanBoysCount: 0,
      ruralGirlsCount: 0,
      ruralBoysCount: 0,
      periUrbanGirlsCount: 0,
      periUrbanBoysCount: 0,
      urbanCount: 0,
      ruralCount: 0,
      periUrbanCount: 0,
      generatedAt: '2026-05-24T08:00:00Z',
      cached: false,
    });
  });

  it('getEvolution sérialise schoolYears en plusieurs paramètres', () => {
    service.getEvolution('NATIONAL', null, ['SY-2024', 'SY-2025']).subscribe();
    const req = httpMock.expectOne(
      (r) => r.url === `${environment.apiUrl}/enrollment/gpi/evolution`,
    );
    expect(req.request.params.getAll('schoolYears')).toEqual([
      'SY-2024',
      'SY-2025',
    ]);
    expect(req.request.params.get('scope')).toBe('NATIONAL');
    req.flush([]);
  });

  it('toNumber convertit Decimal string en number, null sinon', () => {
    expect(EnrollmentApiService.toNumber('0.97')).toBe(0.97);
    expect(EnrollmentApiService.toNumber(1.03)).toBe(1.03);
    expect(EnrollmentApiService.toNumber(null)).toBeNull();
    expect(EnrollmentApiService.toNumber(undefined)).toBeNull();
    expect(EnrollmentApiService.toNumber('not-a-number')).toBeNull();
    expect(EnrollmentApiService.toNumber('')).toBeNull();
  });
});
