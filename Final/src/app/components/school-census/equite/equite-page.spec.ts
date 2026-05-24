import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it } from 'vitest';

import { environment } from '../../../../environments/environment';
import { EquitePage } from './equite-page';

describe('EquitePage', () => {
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [EquitePage],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
      ],
    });
    httpMock = TestBed.inject(HttpTestingController);
  });

  it('crée la page et déclenche un appel /census/metadata au montage', () => {
    const fixture = TestBed.createComponent(EquitePage);
    fixture.detectChanges();
    const req = httpMock.expectOne(`${environment.apiUrl}/census/metadata`);
    expect(req.request.method).toBe('GET');
    req.flush({
      regions: [],
      schools: [],
      prefectures: [],
      subPrefectures: [],
      roles: [],
    });
    // Sans région connue, les autres endpoints non bloquants peuvent tomber :
    // on demande quand-même les KPI globaux.
    const nationalReqs = httpMock.match(
      (r) => r.url === `${environment.apiUrl}/enrollment/gpi`,
    );
    expect(nationalReqs.length).toBeGreaterThanOrEqual(1);
    for (const r of nationalReqs) {
      r.flush({
        scope: 'NATIONAL',
        entityId: null,
        schoolYearId: 'SY-2025',
        girlsCount: 0,
        boysCount: 0,
        gpi: null,
        severity: 'NORMAL',
        computedAt: '2026-05-24T08:00:00Z',
      });
    }
    // La carte Leaflet charge un GeoJSON depuis /assets — on draine sans
    // l'imposer (la map est gracieuse en cas d'absence de fichier).
    const pending = httpMock.match(() => true);
    for (const r of pending) {
      r.flush({ type: 'FeatureCollection', features: [] });
    }
    httpMock.verify();
    expect(fixture.componentInstance).toBeTruthy();
  });

  it('expose un signal loading initialisé à true', () => {
    const fixture = TestBed.createComponent(EquitePage);
    // Avant détection des changements, ngOnInit pas exécuté → reste true par défaut.
    expect(fixture.componentInstance.loading()).toBe(true);
    fixture.detectChanges();
    // Toujours en cours de chargement tant qu'on n'a pas flush les requêtes.
    expect(fixture.componentInstance.loading()).toBe(true);
    // Drainer la requête metadata pour ne pas laisser de pendings.
    const meta = httpMock.expectOne(`${environment.apiUrl}/census/metadata`);
    meta.flush({
      regions: [],
      schools: [],
      prefectures: [],
      subPrefectures: [],
      roles: [],
    });
    const remaining = httpMock.match(() => true);
    for (const r of remaining) {
      r.flush(null);
    }
    httpMock.verify();
  });
});
