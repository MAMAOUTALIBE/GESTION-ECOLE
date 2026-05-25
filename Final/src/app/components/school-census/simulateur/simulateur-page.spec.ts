import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it } from 'vitest';

import { environment } from '../../../../environments/environment';
import { ScenarioRead } from '../shared/simulator-api.service';
import { SimulateurPage } from './simulateur-page';

describe('SimulateurPage', () => {
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [SimulateurPage],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
      ],
    });
    httpMock = TestBed.inject(HttpTestingController);
  });

  function answerInitialLoads(scenarios: ScenarioRead[] = []): void {
    const metaReq = httpMock.expectOne(
      `${environment.apiUrl}/census/metadata`,
    );
    metaReq.flush({
      regions: [],
      prefectures: [],
      subPrefectures: [],
      schools: [
        {
          id: 'SCH-1',
          name: 'École Alpha',
          code: 'AAA',
          regionId: 'R-1',
          latitude: 9.5,
          longitude: -13.7,
        },
      ],
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

    const listReq = httpMock.expectOne(
      `${environment.apiUrl}/simulator/scenarios`,
    );
    listReq.flush(scenarios);
  }

  it('charge metadata, années et scénarios au montage', () => {
    const fixture = TestBed.createComponent(SimulateurPage);
    fixture.detectChanges();

    answerInitialLoads([
      {
        id: 'SCN-1',
        name: 'Pilote Conakry',
        description: null,
        status: 'DRAFT',
        createdAt: '2026-05-01T08:00:00Z',
        createdById: 'USR-1',
        baselineSchoolYearId: 'SY-2026',
        scenarioJson: { operations: [] },
        impactJson: null,
        computedAt: null,
      },
    ]);

    expect(fixture.componentInstance.schoolYearId()).toBe('SY-2026');
    expect(fixture.componentInstance.schools().length).toBe(1);
    expect(fixture.componentInstance.scenarios().length).toBe(1);
    expect(fixture.componentInstance.loading()).toBe(false);
  });

  it('startNewScenario réinitialise les opérations et l\'impact', () => {
    const fixture = TestBed.createComponent(SimulateurPage);
    fixture.detectChanges();
    answerInitialLoads([]);

    // Simule l'ajout d'une opération + un impact présent.
    fixture.componentInstance.onOpAdded({
      type: 'CLOSE_SCHOOL',
      schoolId: 'SCH-1',
    });
    expect(fixture.componentInstance.operations().length).toBe(1);

    fixture.componentInstance.startNewScenario();
    expect(fixture.componentInstance.operations().length).toBe(0);
    expect(fixture.componentInstance.currentScenarioId()).toBeNull();
    expect(fixture.componentInstance.impact()).toBeNull();
    expect(fixture.componentInstance.mode()).toBe('view');
  });
});
