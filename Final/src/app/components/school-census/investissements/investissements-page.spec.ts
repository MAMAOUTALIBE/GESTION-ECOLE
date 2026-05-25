import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it } from 'vitest';

import { environment } from '../../../../environments/environment';
import { InvestmentScoreRead } from '../shared/investment-api.service';
import { InvestissementsPage } from './investissements-page';

describe('InvestissementsPage', () => {
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [InvestissementsPage],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
      ],
    });
    httpMock = TestBed.inject(HttpTestingController);
  });

  function flushAllPending(): void {
    const pending = httpMock.match(() => true);
    for (const r of pending) {
      if (r.request.url.endsWith('/census/metadata')) {
        r.flush({
          regions: [],
          prefectures: [],
          subPrefectures: [],
          schools: [],
          roles: [],
        });
      } else {
        r.flush([]);
      }
    }
  }

  it('charge metadata + school-years + top-priorities et calcule les KPIs', () => {
    const fixture = TestBed.createComponent(InvestissementsPage);
    fixture.detectChanges();

    const metaReq = httpMock.expectOne(
      `${environment.apiUrl}/census/metadata`,
    );
    metaReq.flush({
      regions: [{ id: 'REG-1', name: 'Conakry', code: 'CKY' }],
      prefectures: [],
      subPrefectures: [],
      schools: [],
      roles: [],
    });

    const syReq = httpMock.expectOne(
      `${environment.apiUrl}/academics/school-years`,
    );
    syReq.flush([
      {
        id: 'SY-2026',
        name: '2025-2026',
        startDate: '2025-09-01',
        endDate: '2026-07-31',
        periodType: 'TRIMESTER',
        isActive: true,
        periods: [],
        createdAt: '',
        updatedAt: '',
      },
    ]);

    const topReq = httpMock.expectOne(
      (r) =>
        r.url === `${environment.apiUrl}/investment/top-priorities` &&
        r.method === 'GET',
    );
    expect(topReq.request.params.get('limit')).toBe('100');
    const sample: InvestmentScoreRead[] = [
      {
        schoolId: 'SCH-A',
        schoolName: 'École A',
        regionId: 'REG-1',
        regionName: 'Conakry',
        baseSchoolYearId: 'SY-2026',
        infrastructureScore: 30,
        saturationScore: 25,
        equityScore: 25,
        accessibilityScore: 15,
        totalScore: 95,
        priorityCategory: 'TRES_HAUTE',
        computedAt: '2026-05-25T10:00:00Z',
      },
      {
        schoolId: 'SCH-B',
        schoolName: 'École B',
        regionId: 'REG-1',
        regionName: 'Conakry',
        baseSchoolYearId: 'SY-2026',
        infrastructureScore: 20,
        saturationScore: 15,
        equityScore: 15,
        accessibilityScore: 10,
        totalScore: 60,
        priorityCategory: 'HAUTE',
        computedAt: '2026-05-25T10:00:00Z',
      },
      {
        schoolId: 'SCH-C',
        schoolName: 'École C',
        regionId: 'REG-1',
        regionName: 'Conakry',
        baseSchoolYearId: 'SY-2026',
        infrastructureScore: 10,
        saturationScore: 10,
        equityScore: 5,
        accessibilityScore: 10,
        totalScore: 35,
        priorityCategory: 'MOYENNE',
        computedAt: '2026-05-25T10:00:00Z',
      },
    ];
    topReq.flush(sample);

    expect(fixture.componentInstance.loading()).toBe(false);
    expect(fixture.componentInstance.schoolYearId()).toBe('SY-2026');
    expect(fixture.componentInstance.regions().length).toBe(1);
    expect(fixture.componentInstance.tresHauteCount()).toBe(1);
    expect(fixture.componentInstance.hauteCount()).toBe(1);
    expect(fixture.componentInstance.moyenneCount()).toBe(1);
    expect(fixture.componentInstance.basseCount()).toBe(0);
    expect(fixture.componentInstance.filteredScores().length).toBe(3);

    flushAllPending();
  });

  it('filtre la table par catégorie et par région via les signals', () => {
    const fixture = TestBed.createComponent(InvestissementsPage);
    fixture.detectChanges();

    httpMock
      .expectOne(`${environment.apiUrl}/census/metadata`)
      .flush({
        regions: [
          { id: 'REG-1', name: 'Conakry', code: 'CKY' },
          { id: 'REG-2', name: 'Kindia', code: 'KND' },
        ],
        prefectures: [],
        subPrefectures: [],
        schools: [],
        roles: [],
      });
    httpMock
      .expectOne(`${environment.apiUrl}/academics/school-years`)
      .flush([
        {
          id: 'SY-2026',
          name: '2025-2026',
          startDate: '2025-09-01',
          endDate: '2026-07-31',
          periodType: 'TRIMESTER',
          isActive: true,
          periods: [],
          createdAt: '',
          updatedAt: '',
        },
      ]);
    httpMock
      .expectOne(
        (r) =>
          r.url === `${environment.apiUrl}/investment/top-priorities` &&
          r.method === 'GET',
      )
      .flush([
        {
          schoolId: 'SCH-A',
          schoolName: 'École A',
          regionId: 'REG-1',
          regionName: 'Conakry',
          baseSchoolYearId: 'SY-2026',
          infrastructureScore: 30,
          saturationScore: 25,
          equityScore: 25,
          accessibilityScore: 15,
          totalScore: 95,
          priorityCategory: 'TRES_HAUTE',
          computedAt: '2026-05-25T10:00:00Z',
        },
        {
          schoolId: 'SCH-B',
          schoolName: 'École B',
          regionId: 'REG-2',
          regionName: 'Kindia',
          baseSchoolYearId: 'SY-2026',
          infrastructureScore: 20,
          saturationScore: 15,
          equityScore: 15,
          accessibilityScore: 10,
          totalScore: 60,
          priorityCategory: 'HAUTE',
          computedAt: '2026-05-25T10:00:00Z',
        },
      ]);

    expect(fixture.componentInstance.filteredScores().length).toBe(2);

    // Filtre catégorie TRES_HAUTE -> 1 école
    fixture.componentInstance.onCategoryToggle('TRES_HAUTE');
    expect(fixture.componentInstance.filteredScores().length).toBe(1);
    expect(fixture.componentInstance.filteredScores()[0].schoolId).toBe(
      'SCH-A',
    );

    // Toggle off -> retourne 2
    fixture.componentInstance.onCategoryToggle('TRES_HAUTE');
    expect(fixture.componentInstance.filteredScores().length).toBe(2);

    // Filtre région REG-2 -> 1 école
    fixture.componentInstance.onRegionChange('REG-2');
    expect(fixture.componentInstance.filteredScores().length).toBe(1);
    expect(fixture.componentInstance.filteredScores()[0].schoolId).toBe(
      'SCH-B',
    );

    // Reset
    fixture.componentInstance.resetFilters();
    expect(fixture.componentInstance.filteredScores().length).toBe(2);

    flushAllPending();
  });
});
