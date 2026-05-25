import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { environment } from '../../../../environments/environment';
import {
  StaffingApiService,
  TeacherStaffingSnapshot,
  TeacherTransferRecommendation,
} from './staffing-api.service';

describe('StaffingApiService', () => {
  let service: StaffingApiService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        StaffingApiService,
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    service = TestBed.inject(StaffingApiService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  it('computeStaffing POST le bon body et la bonne URL', () => {
    service.computeStaffing('SY-2026').subscribe((resp) => {
      expect(resp.snapshots).toBe(42);
    });
    const req = httpMock.expectOne(
      `${environment.apiUrl}/projections/staffing/compute`,
    );
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ schoolYearId: 'SY-2026' });
    req.flush({ snapshots: 42, recommendations: 0 });
  });

  it('generateRecommendations POST le schoolYearId fourni', () => {
    service.generateRecommendations('SY-2026').subscribe((resp) => {
      expect(resp.recommendations).toBe(7);
    });
    const req = httpMock.expectOne(
      `${environment.apiUrl}/projections/recommendations/generate`,
    );
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ schoolYearId: 'SY-2026' });
    req.flush({ snapshots: 0, recommendations: 7 });
  });

  it('listStaffing sérialise les filtres en query params', () => {
    service
      .listStaffing({
        schoolYearId: 'SY-2026',
        schoolId: 'SCH-1',
        severity: 'CRITICAL',
        limit: 50,
      })
      .subscribe();
    const req = httpMock.expectOne(
      (r) =>
        r.url === `${environment.apiUrl}/projections/staffing` &&
        r.params.get('schoolYearId') === 'SY-2026' &&
        r.params.get('schoolId') === 'SCH-1' &&
        r.params.get('severity') === 'CRITICAL' &&
        r.params.get('limit') === '50',
    );
    expect(req.request.method).toBe('GET');
    req.flush([] as TeacherStaffingSnapshot[]);
  });

  it('listStaffing omet les filtres null/undefined', () => {
    service
      .listStaffing({ schoolYearId: null, severity: null })
      .subscribe();
    const req = httpMock.expectOne(
      `${environment.apiUrl}/projections/staffing`,
    );
    expect(req.request.params.has('schoolYearId')).toBe(false);
    expect(req.request.params.has('severity')).toBe(false);
    req.flush([] as TeacherStaffingSnapshot[]);
  });

  it('listRecommendations passe le status et le regionId', () => {
    service
      .listRecommendations({
        status: 'PENDING',
        regionId: 'REG-3',
      })
      .subscribe();
    const req = httpMock.expectOne(
      (r) =>
        r.url === `${environment.apiUrl}/projections/recommendations` &&
        r.params.get('status') === 'PENDING' &&
        r.params.get('regionId') === 'REG-3',
    );
    expect(req.request.method).toBe('GET');
    req.flush([] as TeacherTransferRecommendation[]);
  });

  it('reviewRecommendation PATCH le bon body et la bonne URL', () => {
    service
      .reviewRecommendation('REC-1', {
        status: 'ACCEPTED',
        reviewNote: 'OK pour signature',
      })
      .subscribe();
    const req = httpMock.expectOne(
      `${environment.apiUrl}/projections/recommendations/REC-1/review`,
    );
    expect(req.request.method).toBe('PATCH');
    expect(req.request.body).toEqual({
      status: 'ACCEPTED',
      reviewNote: 'OK pour signature',
    });
    req.flush({
      id: 'REC-1',
      schoolYearId: 'SY-2026',
      fromSchoolId: 'SCH-A',
      toSchoolId: 'SCH-B',
      prefectureId: null,
      regionId: 'REG-1',
      transfersSuggested: 2,
      priorityScore: '0.85',
      rationale: null,
      status: 'ACCEPTED',
      createdAt: '2026-05-01T00:00:00Z',
      reviewedById: 'USR-1',
      reviewedAt: '2026-05-25T00:00:00Z',
      reviewNote: 'OK pour signature',
    });
  });

  it('toNumber convertit Decimal string en number', () => {
    expect(StaffingApiService.toNumber('0.85')).toBe(0.85);
    expect(StaffingApiService.toNumber(42)).toBe(42);
    expect(StaffingApiService.toNumber(null)).toBeNull();
    expect(StaffingApiService.toNumber('not-a-number')).toBeNull();
    expect(StaffingApiService.toNumber('')).toBeNull();
  });
});
