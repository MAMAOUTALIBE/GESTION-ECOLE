import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { environment } from '../../../../environments/environment';
import { CartographyApiService } from './cartography-api.service';

describe('CartographyApiService', () => {
  let service: CartographyApiService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(CartographyApiService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  it('getGpiCriticalRegions appelle /layers/gpi-critical-regions avec schoolYearId optionnel', () => {
    service.getGpiCriticalRegions('SY-2025').subscribe();
    const req = httpMock.expectOne(
      (r) =>
        r.url === `${environment.apiUrl}/cartography/layers/gpi-critical-regions`,
    );
    expect(req.request.method).toBe('GET');
    expect(req.request.params.get('schoolYearId')).toBe('SY-2025');
    req.flush({ type: 'FeatureCollection', features: [] });
  });

  it('getCapacityCriticalSchools n\'ajoute pas baseSchoolYearId si null', () => {
    service.getCapacityCriticalSchools().subscribe();
    const req = httpMock.expectOne(
      (r) =>
        r.url ===
        `${environment.apiUrl}/cartography/layers/capacity-critical-schools`,
    );
    // Sans paramètre fourni, l'URL ne doit pas porter le param vide.
    expect(req.request.params.get('baseSchoolYearId')).toBeNull();
    req.flush({ type: 'FeatureCollection', features: [] });
  });

  it('getStaffingCriticalSchools propage schoolYearId', () => {
    service.getStaffingCriticalSchools('SY-2025').subscribe();
    const req = httpMock.expectOne(
      (r) =>
        r.url ===
        `${environment.apiUrl}/cartography/layers/staffing-critical-schools`,
    );
    expect(req.request.params.get('schoolYearId')).toBe('SY-2025');
    req.flush({ type: 'FeatureCollection', features: [] });
  });

  it('getInfrastructureGaps n\'envoie aucun paramètre', () => {
    service.getInfrastructureGaps().subscribe();
    const req = httpMock.expectOne(
      (r) =>
        r.url === `${environment.apiUrl}/cartography/layers/infrastructure-gaps`,
    );
    expect(req.request.method).toBe('GET');
    expect(req.request.params.keys().length).toBe(0);
    req.flush({ type: 'FeatureCollection', features: [] });
  });

  it('getZoneTypeLayer expose un GET sans paramètre', () => {
    service.getZoneTypeLayer().subscribe();
    const req = httpMock.expectOne(
      (r) => r.url === `${environment.apiUrl}/cartography/layers/zone-type`,
    );
    expect(req.request.method).toBe('GET');
    req.flush({ type: 'FeatureCollection', features: [] });
  });

  it('getWhiteZonesEnriched sérialise radius + populationThreshold', () => {
    service.getWhiteZonesEnriched(7.5, 1000).subscribe();
    const req = httpMock.expectOne(
      (r) =>
        r.url ===
        `${environment.apiUrl}/cartography/layers/white-zones-enriched`,
    );
    expect(req.request.params.get('radiusKm')).toBe('7.5');
    expect(req.request.params.get('populationThreshold')).toBe('1000');
    req.flush({ type: 'FeatureCollection', features: [] });
  });
});
