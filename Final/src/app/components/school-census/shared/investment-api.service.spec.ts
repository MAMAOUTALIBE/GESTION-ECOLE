import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { environment } from '../../../../environments/environment';
import {
  ComputeScoresResponse,
  InvestmentApiService,
  InvestmentScoreRead,
} from './investment-api.service';

describe('InvestmentApiService', () => {
  let service: InvestmentApiService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        InvestmentApiService,
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    service = TestBed.inject(InvestmentApiService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  it('computeScores POST /compute-scores avec baseSchoolYearId dans le body', () => {
    const expected: ComputeScoresResponse = {
      scoresComputed: 42,
      byCategory: { TRES_HAUTE: 5, HAUTE: 12, MOYENNE: 15, BASSE: 10 },
      baseSchoolYearId: 'SY-2026',
      computedAt: '2026-05-25T10:00:00Z',
    };
    service.computeScores('SY-2026').subscribe((res) => {
      expect(res).toEqual(expected);
    });
    const req = httpMock.expectOne(
      `${environment.apiUrl}/investment/compute-scores`,
    );
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ baseSchoolYearId: 'SY-2026' });
    req.flush(expected);
  });

  it('listPriorities GET /priorities sérialise tous les filtres en query params', () => {
    service
      .listPriorities({
        category: 'TRES_HAUTE',
        regionId: 'REG-1',
        baseSchoolYearId: 'SY-2026',
        limit: 50,
        offset: 100,
      })
      .subscribe();
    const req = httpMock.expectOne(
      (r) =>
        r.url === `${environment.apiUrl}/investment/priorities` &&
        r.method === 'GET',
    );
    expect(req.request.params.get('category')).toBe('TRES_HAUTE');
    expect(req.request.params.get('regionId')).toBe('REG-1');
    expect(req.request.params.get('baseSchoolYearId')).toBe('SY-2026');
    expect(req.request.params.get('limit')).toBe('50');
    expect(req.request.params.get('offset')).toBe('100');
    req.flush([]);
  });

  it('listPriorities omet les filtres null/undefined', () => {
    service.listPriorities({}).subscribe();
    const req = httpMock.expectOne(
      (r) =>
        r.url === `${environment.apiUrl}/investment/priorities` &&
        r.method === 'GET',
    );
    expect(req.request.params.keys()).toEqual([]);
    req.flush([]);
  });

  it('topPriorities GET /top-priorities avec limit par défaut 100', () => {
    service.topPriorities().subscribe();
    const req = httpMock.expectOne(
      (r) =>
        r.url === `${environment.apiUrl}/investment/top-priorities` &&
        r.method === 'GET',
    );
    expect(req.request.params.get('limit')).toBe('100');
    expect(req.request.params.has('baseSchoolYearId')).toBe(false);
    req.flush([]);
  });

  it('topPriorities accepte limit et baseSchoolYearId explicites', () => {
    service.topPriorities(25, 'SY-2026').subscribe();
    const req = httpMock.expectOne(
      (r) =>
        r.url === `${environment.apiUrl}/investment/top-priorities` &&
        r.method === 'GET',
    );
    expect(req.request.params.get('limit')).toBe('25');
    expect(req.request.params.get('baseSchoolYearId')).toBe('SY-2026');
    req.flush([]);
  });

  it('getSchoolPriority GET /schools/{id} et renvoie le détail', () => {
    const expected: InvestmentScoreRead = {
      schoolId: 'SCH-1',
      schoolName: 'École Test',
      regionId: 'REG-1',
      regionName: 'Conakry',
      baseSchoolYearId: 'SY-2026',
      infrastructureScore: 30,
      saturationScore: 20,
      equityScore: 15,
      accessibilityScore: 15,
      totalScore: 80,
      priorityCategory: 'TRES_HAUTE',
      computedAt: '2026-05-25T10:00:00Z',
      breakdownJson: {
        infrastructure: { missingWater: true, missingElectricity: false },
        saturation: { severity: 'CRITICAL', score: 25 },
        equity: { gpi: 0.7, severity: 'CRITICAL', score: 25 },
        accessibility: { zoneType: 'RURAL', zonePoints: 15, score: 15 },
      },
    };
    service.getSchoolPriority('SCH-1').subscribe((res) => {
      expect(res).toEqual(expected);
    });
    const req = httpMock.expectOne(
      `${environment.apiUrl}/investment/schools/SCH-1`,
    );
    expect(req.request.method).toBe('GET');
    req.flush(expected);
  });

  it('categoryClass et categoryLabel mappent chaque catégorie', () => {
    expect(InvestmentApiService.categoryClass('TRES_HAUTE')).toContain(
      'text-danger',
    );
    expect(InvestmentApiService.categoryClass('HAUTE')).toContain(
      'text-warning',
    );
    expect(InvestmentApiService.categoryClass('MOYENNE')).toContain(
      'text-info',
    );
    expect(InvestmentApiService.categoryClass('BASSE')).toContain(
      'text-success',
    );
    expect(InvestmentApiService.categoryLabel('TRES_HAUTE')).toBe('Très haute');
    expect(InvestmentApiService.categoryLabel('BASSE')).toBe('Basse');
  });
});
