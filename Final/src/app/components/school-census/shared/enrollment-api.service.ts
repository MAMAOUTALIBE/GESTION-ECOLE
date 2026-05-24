import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../../environments/environment';

// =====================================================================
// Module 1D — Frontend des modules 1A (Enrollment), 1B (GPI), 1C (zoneType)
// =====================================================================
// Types miroir des schémas Pydantic Backend.

export type GpiScope = 'NATIONAL' | 'REGIONAL' | 'PREFECTURE' | 'SCHOOL';

/**
 * Classification UNESCO/IIPE d'une valeur GPI.
 * Sert à colorer les cartes/cards et déclencher les alertes "filles".
 */
export type GpiSeverity =
  | 'NORMAL'
  | 'WARNING_GIRLS'
  | 'CRITICAL_GIRLS'
  | 'WARNING_BOYS';

export interface GpiResult {
  scope: GpiScope;
  entityId: string | null;
  schoolYearId: string;
  girlsCount: number;
  boysCount: number;
  /** Decimal sérialisé en string par Pydantic — on garde la valeur brute. */
  gpi: number | string | null;
  severity: GpiSeverity;
  computedAt: string;
  /** Métadonnée injectée côté UI pour afficher un libellé (école, région…). */
  entityName?: string | null;
}

export interface GpiEvolutionPoint {
  schoolYearId: string;
  schoolYearName: string | null;
  gpi: number | string | null;
  severity: GpiSeverity;
  girlsCount: number;
  boysCount: number;
  computedAt: string;
}

export interface ZoneAggregate {
  zoneType: 'URBAN' | 'RURAL' | 'PERI_URBAN';
  girlsCount: number;
  boysCount: number;
  total: number;
  gpi: number | string | null;
}

export interface UrbanRuralGap {
  schoolYearId: string;
  urbanGpi: number | string | null;
  ruralGpi: number | string | null;
  periUrbanGpi: number | string | null;
  deltaGpi: number | string | null;
  urbanGirlsCount: number;
  urbanBoysCount: number;
  ruralGirlsCount: number;
  ruralBoysCount: number;
  periUrbanGirlsCount: number;
  periUrbanBoysCount: number;
  urbanCount: number;
  ruralCount: number;
  periUrbanCount: number;
  generatedAt: string;
  cached: boolean;
}

export interface EnrollmentAggregateCell {
  level?: string | null;
  gender?: 'MALE' | 'FEMALE' | null;
  count: number;
  gpi: number | string | null;
}

export interface AggregateResponse {
  scope: string;
  schoolYearId: string;
  total: number;
  byLevel: EnrollmentAggregateCell[];
  byGender: EnrollmentAggregateCell[];
  breakdown: EnrollmentAggregateCell[];
  byZoneType: ZoneAggregate[];
}

/** Alias plus parlant côté UI. */
export type CriticalSchool = GpiResult;

@Injectable({ providedIn: 'root' })
export class EnrollmentApiService {
  private http = inject(HttpClient);
  private baseUrl = `${environment.apiUrl}/enrollment`;
  private cockpitUrl = `${environment.apiUrl}/cockpit`;

  /** GET /api/enrollment/gpi?scope=NATIONAL (cache backend ~5 min). */
  getNationalGpi(schoolYearId?: string): Observable<GpiResult> {
    let params = new HttpParams().set('scope', 'NATIONAL');
    if (schoolYearId) {
      params = params.set('schoolYearId', schoolYearId);
    }
    return this.http.get<GpiResult>(`${this.baseUrl}/gpi`, { params });
  }

  /** GET /api/enrollment/gpi?scope=REGIONAL&entityId=... */
  getRegionalGpi(regionId: string, schoolYearId?: string): Observable<GpiResult> {
    let params = new HttpParams()
      .set('scope', 'REGIONAL')
      .set('entityId', regionId);
    if (schoolYearId) {
      params = params.set('schoolYearId', schoolYearId);
    }
    return this.http.get<GpiResult>(`${this.baseUrl}/gpi`, { params });
  }

  /** GET /api/enrollment/gpi/critical-schools — top écoles GPI < 0.85. */
  getCriticalSchools(
    schoolYearId: string,
    limit = 10,
  ): Observable<CriticalSchool[]> {
    const params = new HttpParams()
      .set('schoolYearId', schoolYearId)
      .set('limit', String(limit));
    return this.http.get<CriticalSchool[]>(
      `${this.baseUrl}/gpi/critical-schools`,
      { params },
    );
  }

  /**
   * GET /api/enrollment/aggregate?byZoneType=true — agrégat national désagrégé
   * par niveau × genre + breakdown par zone (urban/rural/peri_urban).
   */
  getAggregateByZone(schoolYearId: string): Observable<AggregateResponse> {
    const params = new HttpParams()
      .set('schoolYearId', schoolYearId)
      .set('scope', 'NATIONAL')
      .set('byZoneType', 'true');
    return this.http.get<AggregateResponse>(`${this.baseUrl}/aggregate`, {
      params,
    });
  }

  /** GET /api/cockpit/kpis/urban-rural-gap. */
  getUrbanRuralGap(schoolYearId: string): Observable<UrbanRuralGap> {
    const params = new HttpParams().set('schoolYearId', schoolYearId);
    return this.http.get<UrbanRuralGap>(`${this.cockpitUrl}/kpis/urban-rural-gap`, {
      params,
    });
  }

  /** GET /api/enrollment/gpi/evolution — série temporelle multi-années. */
  getEvolution(
    scope: GpiScope,
    entityId: string | null,
    schoolYears: string[],
  ): Observable<GpiEvolutionPoint[]> {
    let params = new HttpParams().set('scope', scope);
    if (entityId) {
      params = params.set('entityId', entityId);
    }
    for (const sy of schoolYears) {
      params = params.append('schoolYears', sy);
    }
    return this.http.get<GpiEvolutionPoint[]>(`${this.baseUrl}/gpi/evolution`, {
      params,
    });
  }

  /**
   * Normalise une valeur GPI numérique (le backend renvoie Decimal sérialisé
   * en string OU number selon la version JSON ; on garde une seule API côté UI).
   */
  static toNumber(value: number | string | null | undefined): number | null {
    if (value === null || value === undefined || value === '') {
      return null;
    }
    const n = typeof value === 'number' ? value : Number(value);
    return Number.isFinite(n) ? n : null;
  }
}
