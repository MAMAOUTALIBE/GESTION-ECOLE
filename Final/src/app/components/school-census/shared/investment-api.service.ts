import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../../environments/environment';

// ===========================================================================
// Module 3C UI — Service Angular Score d'investissement par école
// ===========================================================================
// Miroir strict des schémas Pydantic Backend
// (Backend/app/modules/investment). camelCase identique au backend
// (FastAPI sérialise les champs Pydantic tels quels, pas d'aliasing).

/** Catégorie finale de priorité (cf. `PriorityCategory` côté backend). */
export type PriorityCategory = 'TRES_HAUTE' | 'HAUTE' | 'MOYENNE' | 'BASSE';

/**
 * Détails d'audit par dimension (stockés dans `breakdownJson`).
 *
 * Chaque dimension est typée comme un dict ouvert : la spec backend
 * peut évoluer (ajout de sous-champs) sans casser le contrat TS.
 */
export interface ScoreBreakdown {
  infrastructure?: InfrastructureBreakdown | Record<string, unknown>;
  saturation?: SaturationBreakdown | Record<string, unknown>;
  equity?: EquityBreakdown | Record<string, unknown>;
  accessibility?: AccessibilityBreakdown | Record<string, unknown>;
}

export interface InfrastructureBreakdown {
  missingWater?: boolean;
  missingElectricity?: boolean;
  missingToilets?: boolean;
  buildingCondition?: string | null;
  buildingPoints?: number;
  classroomsRatio?: number | null;
  classroomsRatioCritical?: boolean;
  missingInternet?: boolean;
  rawPoints?: number;
  score?: number;
}

export interface SaturationBreakdown {
  severity?: 'CRITICAL' | 'WARNING' | 'OK' | null;
  score?: number;
}

export interface EquityBreakdown {
  gpi?: number | null;
  severity?: 'CRITICAL' | 'WARNING' | 'NORMAL' | 'UNKNOWN';
  score?: number;
}

export interface AccessibilityBreakdown {
  zoneType?: string;
  zonePoints?: number;
  avgDistanceKm?: number | null;
  distanceBonus?: number;
  score?: number;
}

/**
 * Sortie `GET /api/investment/priorities/{schoolId}` et `top-priorities`.
 *
 * Le champ `breakdownJson` est nullable : tant que `compute-scores` n'a
 * pas été lancé, il peut rester `null` côté backend.
 */
export interface InvestmentScoreRead {
  /** id technique du row (présent côté backend Module 3C). */
  id?: string;
  schoolId: string;
  schoolName?: string | null;
  regionId?: string | null;
  regionName?: string | null;
  baseSchoolYearId: string;
  infrastructureScore: number;
  saturationScore: number;
  equityScore: number;
  accessibilityScore: number;
  totalScore: number;
  priorityCategory: PriorityCategory;
  computedAt: string;
  breakdownJson?: ScoreBreakdown | null;
}

/** Body de `POST /api/investment/compute-scores`. */
export interface ComputeScoresRequest {
  baseSchoolYearId: string;
}

/** Réponse de `POST /api/investment/compute-scores`. */
export interface ComputeScoresResponse {
  scoresComputed: number;
  byCategory: Partial<Record<PriorityCategory, number>>;
  baseSchoolYearId: string;
  computedAt: string;
}

/** Filtres optionnels pour `listPriorities`. */
export interface ListPrioritiesFilters {
  category?: PriorityCategory | null;
  regionId?: string | null;
  baseSchoolYearId?: string | null;
  limit?: number;
  offset?: number;
}

/**
 * Service Angular dédié au scoring d'investissement (Module 3C UI).
 *
 * Wrapper HTTP pur — pas d'état interne, facile à mocker. Toutes les
 * méthodes retournent un Observable (la page parente gère les toasts
 * via `catchError`).
 */
@Injectable({ providedIn: 'root' })
export class InvestmentApiService {
  private http = inject(HttpClient);
  private base = `${environment.apiUrl}/investment`;

  /** POST /compute-scores — recalcul global (NATIONAL/MINISTRY). */
  computeScores(baseSchoolYearId: string): Observable<ComputeScoresResponse> {
    const body: ComputeScoresRequest = { baseSchoolYearId };
    return this.http.post<ComputeScoresResponse>(
      `${this.base}/compute-scores`,
      body,
    );
  }

  /**
   * GET /priorities — liste filtrée + paginée. Les paramètres `null`
   * ou `undefined` ne sont pas envoyés (HttpParams omet les valeurs
   * vides automatiquement).
   */
  listPriorities(
    filters: ListPrioritiesFilters = {},
  ): Observable<InvestmentScoreRead[]> {
    let params = new HttpParams();
    if (filters.category) params = params.set('category', filters.category);
    if (filters.regionId) params = params.set('regionId', filters.regionId);
    if (filters.baseSchoolYearId)
      params = params.set('baseSchoolYearId', filters.baseSchoolYearId);
    if (filters.limit !== undefined)
      params = params.set('limit', String(filters.limit));
    if (filters.offset !== undefined)
      params = params.set('offset', String(filters.offset));
    return this.http.get<InvestmentScoreRead[]>(`${this.base}/priorities`, {
      params,
    });
  }

  /**
   * GET /top-priorities — top N (défaut 100). `baseSchoolYearId` est
   * accepté côté backend pour cibler une année donnée si besoin.
   */
  topPriorities(
    limit: number = 100,
    baseSchoolYearId?: string | null,
  ): Observable<InvestmentScoreRead[]> {
    let params = new HttpParams().set('limit', String(limit));
    if (baseSchoolYearId)
      params = params.set('baseSchoolYearId', baseSchoolYearId);
    return this.http.get<InvestmentScoreRead[]>(
      `${this.base}/top-priorities`,
      { params },
    );
  }

  /** GET /schools/{schoolId} — détail avec breakdownJson. */
  getSchoolPriority(schoolId: string): Observable<InvestmentScoreRead> {
    return this.http.get<InvestmentScoreRead>(
      `${this.base}/schools/${encodeURIComponent(schoolId)}`,
    );
  }

  /** Couleur Bootstrap d'une catégorie (réutilisée par KPI et table). */
  static categoryClass(category: PriorityCategory): string {
    switch (category) {
      case 'TRES_HAUTE':
        return 'bg-danger-transparent text-danger';
      case 'HAUTE':
        return 'bg-warning-transparent text-warning';
      case 'MOYENNE':
        return 'bg-info-transparent text-info';
      case 'BASSE':
        return 'bg-success-transparent text-success';
    }
  }

  /** Libellé i18n par défaut (FR) — la vraie i18n se fait via TranslateService. */
  static categoryLabel(category: PriorityCategory): string {
    switch (category) {
      case 'TRES_HAUTE':
        return 'Très haute';
      case 'HAUTE':
        return 'Haute';
      case 'MOYENNE':
        return 'Moyenne';
      case 'BASSE':
        return 'Basse';
    }
  }
}
