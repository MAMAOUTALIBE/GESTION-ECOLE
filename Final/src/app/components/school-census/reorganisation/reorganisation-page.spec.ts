import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it } from 'vitest';

import { environment } from '../../../../environments/environment';
import { ReorganisationPage } from './reorganisation-page';

describe('ReorganisationPage', () => {
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [ReorganisationPage],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
      ],
    });
    httpMock = TestBed.inject(HttpTestingController);
  });

  function flushAllLayerRequests(): void {
    // Drain les 6 appels GET /cartography/layers/* (+ tile/Guinea boundary).
    const pending = httpMock.match(() => true);
    for (const r of pending) {
      // Réponse minimaliste : FeatureCollection vide. Pour le GeoJSON
      // de frontière (assets/guinea.geojson), même réponse vide convient.
      r.flush({ type: 'FeatureCollection', features: [] });
    }
  }

  it('crée la page et déclenche les 6 appels de couches au montage', () => {
    const fixture = TestBed.createComponent(ReorganisationPage);
    fixture.detectChanges();

    // Vérifie que les 6 endpoints ont bien été appelés (ordre indifférent).
    const expectedUrls = [
      '/cartography/layers/gpi-critical-regions',
      '/cartography/layers/capacity-critical-schools',
      '/cartography/layers/staffing-critical-schools',
      '/cartography/layers/infrastructure-gaps',
      '/cartography/layers/zone-type',
      '/cartography/layers/white-zones-enriched',
    ];
    for (const path of expectedUrls) {
      const reqs = httpMock.match(
        (r) => r.url === `${environment.apiUrl}${path}`,
      );
      expect(reqs.length).toBeGreaterThanOrEqual(1);
      for (const r of reqs) {
        r.flush({ type: 'FeatureCollection', features: [] });
      }
    }

    // Vidange du reste (eg. assets/guinea.geojson)
    flushAllLayerRequests();
  });

  it('toggleLayer ajoute / retire l\'id du Set actif', () => {
    const fixture = TestBed.createComponent(ReorganisationPage);
    fixture.detectChanges();
    flushAllLayerRequests();

    const component = fixture.componentInstance;
    // État initial : 2 couches actives par défaut.
    const initialSize = component.activeIds().size;
    expect(initialSize).toBeGreaterThanOrEqual(1);

    // Toggle ON : "zone-type" (couche non-active par défaut)
    component.toggleLayer('zone-type');
    expect(component.activeIds().has('zone-type')).toBe(true);

    // Toggle OFF : la même.
    component.toggleLayer('zone-type');
    expect(component.activeIds().has('zone-type')).toBe(false);
  });
});
