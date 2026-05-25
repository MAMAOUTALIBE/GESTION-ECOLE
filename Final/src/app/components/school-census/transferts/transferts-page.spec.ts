import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it } from 'vitest';

import { environment } from '../../../../environments/environment';
import { TransfertsPage } from './transferts-page';

describe('TransfertsPage', () => {
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [TransfertsPage],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
      ],
    });
    httpMock = TestBed.inject(HttpTestingController);
  });

  function flushAll(): void {
    const pending = httpMock.match(() => true);
    for (const r of pending) {
      // Réponse minimaliste pour chaque endpoint :
      //  - liste plate ([]) pour les listings
      //  - objet pour la métadata census
      if (r.request.url.endsWith('/census/metadata')) {
        r.flush({
          regions: [],
          prefectures: [],
          subPrefectures: [],
          schools: [],
          roles: [],
        });
      } else if (r.request.url.endsWith('/academics/school-years')) {
        r.flush([]);
      } else {
        r.flush([]);
      }
    }
  }

  it('crée la page et déclenche les chargements initiaux', () => {
    const fixture = TestBed.createComponent(TransfertsPage);
    fixture.detectChanges();

    const metaReq = httpMock.expectOne(
      `${environment.apiUrl}/census/metadata`,
    );
    expect(metaReq.request.method).toBe('GET');
    metaReq.flush({
      regions: [],
      prefectures: [],
      subPrefectures: [],
      schools: [],
      roles: [],
    });

    const syReq = httpMock.expectOne(
      `${environment.apiUrl}/academics/school-years`,
    );
    expect(syReq.request.method).toBe('GET');
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

    // Suite : appels list staffing + list recommendations en parallèle.
    flushAll();

    expect(fixture.componentInstance.schoolYearId()).toBe('SY-2026');
    expect(fixture.componentInstance.loading()).toBe(false);
  });

  it('met à jour les KPIs en fonction des snapshots reçus', () => {
    const fixture = TestBed.createComponent(TransfertsPage);
    fixture.detectChanges();

    const metaReq = httpMock.expectOne(
      `${environment.apiUrl}/census/metadata`,
    );
    metaReq.flush({
      regions: [],
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

    // staffing : 2 critical + 1 over_staffed
    const staffingReqs = httpMock.match(
      (r) =>
        r.url === `${environment.apiUrl}/projections/staffing` &&
        r.method === 'GET',
    );
    expect(staffingReqs.length).toBeGreaterThanOrEqual(1);
    staffingReqs[0].flush([
      {
        id: 'SS-1',
        schoolYearId: 'SY-2026',
        schoolId: 'SCH-A',
        studentsCount: 200,
        teachersCount: 1,
        ratio: '200',
        severity: 'CRITICAL',
        expectedTeachers: 5,
        gap: 4,
        computedAt: '2026-05-01T00:00:00Z',
      },
      {
        id: 'SS-2',
        schoolYearId: 'SY-2026',
        schoolId: 'SCH-B',
        studentsCount: 100,
        teachersCount: 0,
        ratio: null,
        severity: 'CRITICAL',
        expectedTeachers: 3,
        gap: 3,
        computedAt: '2026-05-01T00:00:00Z',
      },
      {
        id: 'SS-3',
        schoolYearId: 'SY-2026',
        schoolId: 'SCH-C',
        studentsCount: 50,
        teachersCount: 4,
        ratio: '12.5',
        severity: 'OVER_STAFFED',
        expectedTeachers: 2,
        gap: -2,
        computedAt: '2026-05-01T00:00:00Z',
      },
    ]);

    const recoReqs = httpMock.match(
      (r) =>
        r.url === `${environment.apiUrl}/projections/recommendations` &&
        r.method === 'GET',
    );
    expect(recoReqs.length).toBeGreaterThanOrEqual(1);
    recoReqs[0].flush([
      {
        id: 'RC-1',
        schoolYearId: 'SY-2026',
        fromSchoolId: 'SCH-C',
        toSchoolId: 'SCH-A',
        prefectureId: null,
        regionId: 'REG-1',
        transfersSuggested: 2,
        priorityScore: '0.92',
        rationale: null,
        status: 'PENDING',
        createdAt: '2026-05-01T00:00:00Z',
        reviewedById: null,
        reviewedAt: null,
        reviewNote: null,
      },
    ]);

    expect(fixture.componentInstance.criticalCount()).toBe(2);
    expect(fixture.componentInstance.overStaffedCount()).toBe(1);
    expect(fixture.componentInstance.pendingRecoCount()).toBe(1);
    expect(fixture.componentInstance.executedRecoCount()).toBe(0);

    flushAll();
  });
});
