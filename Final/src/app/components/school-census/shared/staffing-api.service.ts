import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../../environments/environment';

// ===========================================================================
// Module 2D UI — Service Angular Recommandations transferts enseignants
// ===========================================================================
// Mirror des schémas Pydantic Backend (Backend/app/modules/projections/schemas.py
// + Backend/app/modules/projections/enums.py). On garde strictement les mêmes
// noms de champs (camelCase) pour que les tests d'intégration restent simples.

/**
 * Niveau de dotation enseignants d'une école.
 * Seuils du ratio élèves/enseignant :
 *  - OVER_STAFFED  : ratio < 25
 *  - ADEQUATE      : 25 ≤ ratio ≤ 50
 *  - UNDER_STAFFED : 50 < ratio ≤ 70
 *  - CRITICAL      : ratio > 70 ou 0 enseignant
 */
export type StaffingSeverity =
  | 'OVER_STAFFED'
  | 'ADEQUATE'
  | 'UNDER_STAFFED'
  | 'CRITICAL';

/** Workflow de revue d'une recommandation de transfert d'enseignants. */
export type RecommendationStatus =
  | 'PENDING'
  | 'REVIEWED'
  | 'ACCEPTED'
  | 'REJECTED'
  | 'EXECUTED';

/** Lecture d'un snapshot staffing école — un par école pour une année donnée. */
export interface TeacherStaffingSnapshot {
  id: string;
  schoolYearId: string;
  schoolId: string;
  studentsCount: number;
  teachersCount: number;
  /** Ratio élèves/enseignant. Decimal serialisé en string ou number par FastAPI. */
  ratio: number | string | null;
  severity: StaffingSeverity;
  expectedTeachers: number;
  gap: number;
  computedAt: string;
}

/** Lecture d'une recommandation de transfert. */
export interface TeacherTransferRecommendation {
  id: string;
  schoolYearId: string;
  fromSchoolId: string;
  toSchoolId: string;
  prefectureId: string | null;
  regionId: string;
  transfersSuggested: number;
  /** Score de priorité (Decimal côté backend — peut arriver en string). */
  priorityScore: number | string;
  rationale: string | null;
  status: RecommendationStatus;
  createdAt: string;
  reviewedById: string | null;
  reviewedAt: string | null;
  reviewNote: string | null;
}

/** Body POST /staffing/compute et /recommendations/generate. */
export interface ComputeStaffingRequest {
  schoolYearId: string;
}

/** Body PATCH /recommendations/{id}/review. */
export interface ReviewRecommendationRequest {
  status: Exclude<RecommendationStatus, 'PENDING'>;
  reviewNote?: string | null;
}

/** Retour des POST /staffing/compute et /recommendations/generate. */
export interface ComputeStaffingResponse {
  snapshots?: number;
  recommendations?: number;
}

/** Filtres pour la liste staffing. */
export interface ListStaffingFilters {
  schoolYearId?: string | null;
  schoolId?: string | null;
  severity?: StaffingSeverity | null;
  limit?: number;
  offset?: number;
}

/** Filtres pour la liste recommandations. */
export interface ListRecommendationsFilters {
  schoolYearId?: string | null;
  regionId?: string | null;
  prefectureId?: string | null;
  status?: RecommendationStatus | null;
  limit?: number;
  offset?: number;
}

/**
 * Service Angular dédié au pilotage des transferts enseignants (Module 2D UI).
 *
 * Conventions :
 *  - Toutes les méthodes retournent un Observable.
 *  - Les filtres null/undefined sont omis de l'URL pour rester idempotent
 *    côté cache backend.
 *  - Le service ne déclenche pas de notification globale ; c'est l'appelant
 *    (page) qui gère le toast / sweetalert.
 */
@Injectable({ providedIn: 'root' })
export class StaffingApiService {
  private http = inject(HttpClient);
  private base = `${environment.apiUrl}/projections`;

  /** POST /staffing/compute — recalcule les snapshots pour une année. */
  computeStaffing(schoolYearId: string): Observable<ComputeStaffingResponse> {
    const body: ComputeStaffingRequest = { schoolYearId };
    return this.http.post<ComputeStaffingResponse>(
      `${this.base}/staffing/compute`,
      body,
    );
  }

  /** POST /recommendations/generate — relance la génération auto. */
  generateRecommendations(
    schoolYearId: string,
  ): Observable<ComputeStaffingResponse> {
    const body: ComputeStaffingRequest = { schoolYearId };
    return this.http.post<ComputeStaffingResponse>(
      `${this.base}/recommendations/generate`,
      body,
    );
  }

  /** GET /staffing — liste paginée des snapshots. */
  listStaffing(
    filters: ListStaffingFilters = {},
  ): Observable<TeacherStaffingSnapshot[]> {
    let params = new HttpParams();
    if (filters.schoolYearId) {
      params = params.set('schoolYearId', filters.schoolYearId);
    }
    if (filters.schoolId) {
      params = params.set('schoolId', filters.schoolId);
    }
    if (filters.severity) {
      params = params.set('severity', filters.severity);
    }
    if (filters.limit !== undefined) {
      params = params.set('limit', String(filters.limit));
    }
    if (filters.offset !== undefined) {
      params = params.set('offset', String(filters.offset));
    }
    return this.http.get<TeacherStaffingSnapshot[]>(`${this.base}/staffing`, {
      params,
    });
  }

  /** GET /recommendations — liste paginée des recommandations. */
  listRecommendations(
    filters: ListRecommendationsFilters = {},
  ): Observable<TeacherTransferRecommendation[]> {
    let params = new HttpParams();
    if (filters.schoolYearId) {
      params = params.set('schoolYearId', filters.schoolYearId);
    }
    if (filters.regionId) {
      params = params.set('regionId', filters.regionId);
    }
    if (filters.prefectureId) {
      params = params.set('prefectureId', filters.prefectureId);
    }
    if (filters.status) {
      params = params.set('status', filters.status);
    }
    if (filters.limit !== undefined) {
      params = params.set('limit', String(filters.limit));
    }
    if (filters.offset !== undefined) {
      params = params.set('offset', String(filters.offset));
    }
    return this.http.get<TeacherTransferRecommendation[]>(
      `${this.base}/recommendations`,
      { params },
    );
  }

  /** PATCH /recommendations/{id}/review — avance la recommandation dans le workflow. */
  reviewRecommendation(
    id: string,
    dto: ReviewRecommendationRequest,
  ): Observable<TeacherTransferRecommendation> {
    return this.http.patch<TeacherTransferRecommendation>(
      `${this.base}/recommendations/${id}/review`,
      dto,
    );
  }

  /**
   * Helpers de coercion : le backend serialise les Decimal en string suivant
   * le JSON Pydantic — on garde un helper local pour éviter de répéter
   * `Number(x ?? 0)` dans les composants.
   */
  static toNumber(value: number | string | null | undefined): number | null {
    if (value === null || value === undefined || value === '') {
      return null;
    }
    const n = typeof value === 'number' ? value : Number(value);
    return Number.isFinite(n) ? n : null;
  }
}
