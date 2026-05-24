import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../../environments/environment';

// =====================================================================
// Module 3A — Frontend des couches cartographiques (réorganisation réseau)
// =====================================================================
// Types miroir des dicts GeoJSON Backend (cf. Backend/app/modules/cartography/layers.py).
// Le contrat reste **GeoJSON RFC 7946** : tout client cartographique (Leaflet,
// MapLibre, Mapbox) peut consommer ces FeatureCollection sans transformation.

export type GeoJsonPointGeometry = {
  type: 'Point';
  coordinates: [number, number]; // [lon, lat]
};

export interface GeoJsonFeature<P extends Record<string, unknown> = Record<string, unknown>> {
  type: 'Feature';
  id?: string;
  geometry: GeoJsonPointGeometry;
  properties: P;
}

export interface GeoJsonFeatureCollection<P extends Record<string, unknown> = Record<string, unknown>> {
  type: 'FeatureCollection';
  features: GeoJsonFeature<P>[];
  meta?: Record<string, unknown>;
}

// ---- Property types par couche (utiles côté UI pour typer les tooltips) ----
// Chaque interface étend Record<string, unknown> pour rester assignable à
// l'index signature de GeoJsonFeatureCollection<Record<string, unknown>> —
// indispensable pour les signals typés du composant page.
export interface GpiCriticalRegionProps extends Record<string, unknown> {
  regionId: string;
  regionName: string;
  gpi: number | null;
  severity: 'CRITICAL_GIRLS' | 'WARNING_GIRLS' | 'NORMAL' | 'WARNING_BOYS';
  girlsCount: number;
  boysCount: number;
}

export interface CapacityCriticalSchoolProps extends Record<string, unknown> {
  schoolId: string;
  name: string;
  code: string;
  capacity: number;
  demand: number;
  gap: number;
  saturationPct: number | null;
  projectedYear: number;
  severity: 'CRITICAL';
}

export interface StaffingCriticalSchoolProps extends Record<string, unknown> {
  schoolId: string;
  name: string;
  code: string;
  studentsCount: number;
  teachersCount: number;
  ratio: number | null;
  gap: number;
  severity: 'UNDER_STAFFED' | 'CRITICAL';
}

export interface InfrastructureGapProps extends Record<string, unknown> {
  schoolId: string;
  name: string;
  code: string;
  regionId: string;
  prefectureId: string | null;
  missingWater: boolean;
  missingElectricity: boolean;
  missingToilets: boolean;
  missingInternet: boolean;
  gaps: ('water' | 'electricity' | 'toilets' | 'internet')[];
  gapCount: number;
}

export interface ZoneTypeProps extends Record<string, unknown> {
  subPrefectureId: string;
  subPrefectureName: string;
  regionId: string;
  prefectureId: string;
  zoneType: 'URBAN' | 'RURAL' | 'PERI_URBAN';
  schoolCount: number;
}

export interface WhiteZoneProps extends Record<string, unknown> {
  subPrefectureId: string;
  subPrefectureName: string;
  regionId: string;
  prefectureId: string;
  nearestSchoolKm: number;
  estimatedPopulation: number;
  radiusKm: number;
}

/**
 * Service Angular dédié aux couches cartographiques Module 3A.
 *
 * Choix d'architecture :
 * - Pas de cache local (Redis backend suffit à 5 min — re-render = re-fetch).
 * - On accepte tous les paramètres optionnels en signature ; on les omet de
 *   l'URL via HttpParams si null/undefined pour rester idempotent côté cache.
 * - Retourne toujours `Observable<GeoJsonFeatureCollection>` — pas de Promise.
 */
@Injectable({ providedIn: 'root' })
export class CartographyApiService {
  private http = inject(HttpClient);
  private base = `${environment.apiUrl}/cartography/layers`;

  /** Couche 1 — Régions GPI critique. */
  getGpiCriticalRegions(
    schoolYearId?: string | null,
  ): Observable<GeoJsonFeatureCollection<GpiCriticalRegionProps>> {
    let params = new HttpParams();
    if (schoolYearId) {
      params = params.set('schoolYearId', schoolYearId);
    }
    return this.http.get<GeoJsonFeatureCollection<GpiCriticalRegionProps>>(
      `${this.base}/gpi-critical-regions`,
      { params },
    );
  }

  /** Couche 2 — Écoles CAPACITY CRITICAL. */
  getCapacityCriticalSchools(
    baseSchoolYearId?: string | null,
  ): Observable<GeoJsonFeatureCollection<CapacityCriticalSchoolProps>> {
    let params = new HttpParams();
    if (baseSchoolYearId) {
      params = params.set('baseSchoolYearId', baseSchoolYearId);
    }
    return this.http.get<GeoJsonFeatureCollection<CapacityCriticalSchoolProps>>(
      `${this.base}/capacity-critical-schools`,
      { params },
    );
  }

  /** Couche 3 — Écoles sous-dotées enseignants. */
  getStaffingCriticalSchools(
    schoolYearId?: string | null,
  ): Observable<GeoJsonFeatureCollection<StaffingCriticalSchoolProps>> {
    let params = new HttpParams();
    if (schoolYearId) {
      params = params.set('schoolYearId', schoolYearId);
    }
    return this.http.get<GeoJsonFeatureCollection<StaffingCriticalSchoolProps>>(
      `${this.base}/staffing-critical-schools`,
      { params },
    );
  }

  /** Couche 4 — Écoles à infrastructure incomplète. */
  getInfrastructureGaps(): Observable<
    GeoJsonFeatureCollection<InfrastructureGapProps>
  > {
    return this.http.get<GeoJsonFeatureCollection<InfrastructureGapProps>>(
      `${this.base}/infrastructure-gaps`,
    );
  }

  /** Couche 5 — Choroplèthe urbain/rural par sous-préfecture. */
  getZoneTypeLayer(): Observable<GeoJsonFeatureCollection<ZoneTypeProps>> {
    return this.http.get<GeoJsonFeatureCollection<ZoneTypeProps>>(
      `${this.base}/zone-type`,
    );
  }

  /** Couche 6 — Zones blanches enrichies (radius + estim. population). */
  getWhiteZonesEnriched(
    radiusKm = 5.0,
    populationThreshold = 500,
  ): Observable<GeoJsonFeatureCollection<WhiteZoneProps>> {
    let params = new HttpParams();
    params = params.set('radiusKm', String(radiusKm));
    params = params.set('populationThreshold', String(populationThreshold));
    return this.http.get<GeoJsonFeatureCollection<WhiteZoneProps>>(
      `${this.base}/white-zones-enriched`,
      { params },
    );
  }
}
