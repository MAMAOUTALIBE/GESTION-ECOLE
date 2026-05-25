import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { environment } from '../../../../environments/environment';
import {
  ScenarioCreate,
  ScenarioRead,
  SimulatorApiService,
} from './simulator-api.service';

describe('SimulatorApiService', () => {
  let service: SimulatorApiService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        SimulatorApiService,
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    service = TestBed.inject(SimulatorApiService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  it('createScenario POST l\'URL et le body avec toutes les opérations', () => {
    const payload: ScenarioCreate = {
      name: 'Scénario test',
      description: 'pilote',
      baselineSchoolYearId: 'SY-2026',
      operations: [
        {
          type: 'CREATE_SCHOOL',
          name: 'Nouvelle école',
          lat: 9.5,
          lon: -13.7,
          capacity: 200,
          subPrefectureId: 'SP-1',
        },
        { type: 'CLOSE_SCHOOL', schoolId: 'SCH-1' },
      ],
    };
    service.createScenario(payload).subscribe((res: ScenarioRead) => {
      expect(res.id).toBe('SCN-1');
    });
    const req = httpMock.expectOne(
      `${environment.apiUrl}/simulator/scenarios`,
    );
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(payload);
    req.flush({
      id: 'SCN-1',
      name: payload.name,
      description: payload.description,
      status: 'DRAFT',
      createdAt: '2026-05-25T10:00:00Z',
      createdById: 'USR-1',
      baselineSchoolYearId: payload.baselineSchoolYearId,
      scenarioJson: { operations: payload.operations },
      impactJson: null,
      computedAt: null,
    } satisfies ScenarioRead);
  });

  it('compute POST avec un body vide sur la bonne URL', () => {
    service.compute('SCN-1').subscribe();
    const req = httpMock.expectOne(
      `${environment.apiUrl}/simulator/scenarios/SCN-1/compute`,
    );
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({});
    req.flush({
      coverage: { beforeCount: 100, afterCount: 101, deltaPct: '1.0' },
      saturation: {
        beforeAvg: '1.05',
        afterAvg: '0.98',
        criticalSchoolsBefore: 3,
        criticalSchoolsAfter: 1,
      },
      distance: {
        beforeKmMean: '4.2',
        afterKmMean: '3.8',
        deltaKm: '-0.4',
      },
      redistributedStudents: 250,
    });
  });

  it('listScenarios GET la racine /scenarios sans paramètres', () => {
    service.listScenarios().subscribe();
    const req = httpMock.expectOne(
      `${environment.apiUrl}/simulator/scenarios`,
    );
    expect(req.request.method).toBe('GET');
    req.flush([] as ScenarioRead[]);
  });

  it('getScenario GET /scenarios/{id} et renvoie l\'objet', () => {
    const expected: ScenarioRead = {
      id: 'SCN-42',
      name: 'Réorga Forécariah',
      description: null,
      status: 'COMPUTED',
      createdAt: '2026-05-24T08:00:00Z',
      createdById: 'USR-1',
      baselineSchoolYearId: 'SY-2026',
      scenarioJson: { operations: [] },
      impactJson: {
        coverage: { beforeCount: 80, afterCount: 81, deltaPct: '1.2' },
        saturation: {
          beforeAvg: '1.01',
          afterAvg: '0.95',
          criticalSchoolsBefore: 2,
          criticalSchoolsAfter: 0,
        },
        distance: {
          beforeKmMean: '3.0',
          afterKmMean: '2.8',
          deltaKm: '-0.2',
        },
        redistributedStudents: 120,
      },
      computedAt: '2026-05-24T09:00:00Z',
    };
    service.getScenario('SCN-42').subscribe((res) => {
      expect(res).toEqual(expected);
    });
    const req = httpMock.expectOne(
      `${environment.apiUrl}/simulator/scenarios/SCN-42`,
    );
    expect(req.request.method).toBe('GET');
    req.flush(expected);
  });

  it('archiveScenario POST /scenarios/{id}/archive avec body vide', () => {
    service.archiveScenario('SCN-7').subscribe();
    const req = httpMock.expectOne(
      `${environment.apiUrl}/simulator/scenarios/SCN-7/archive`,
    );
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({});
    req.flush({
      id: 'SCN-7',
      name: 'Archivé',
      description: null,
      status: 'ARCHIVED',
      createdAt: '2026-05-20T00:00:00Z',
      createdById: 'USR-1',
      baselineSchoolYearId: 'SY-2026',
      scenarioJson: { operations: [] },
      impactJson: null,
      computedAt: null,
    } satisfies ScenarioRead);
  });

  it('toNumber accepte string Decimal, number, null et chaîne invalide', () => {
    expect(SimulatorApiService.toNumber('1.25')).toBe(1.25);
    expect(SimulatorApiService.toNumber(0)).toBe(0);
    expect(SimulatorApiService.toNumber(null)).toBeNull();
    expect(SimulatorApiService.toNumber(undefined)).toBeNull();
    expect(SimulatorApiService.toNumber('')).toBeNull();
    expect(SimulatorApiService.toNumber('nope')).toBeNull();
  });
});
