import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../../environments/environment';

// ===========================================================================
// Module 3B UI — Service Angular Simulateur what-if (réseau scolaire)
// ===========================================================================
// Mirror strict des schémas Pydantic Backend (Backend/app/modules/simulator).
// Conventions camelCase identiques au backend (FastAPI serialise les champs
// du modèle Pydantic tels quels — pas d'aliasing snake_case).

/** Workflow d'un scénario what-if. */
export type ScenarioStatus = 'DRAFT' | 'COMPUTED' | 'ARCHIVED';

/** Type d'opération applicable au réseau scolaire dans un scénario. */
export type OperationType =
  | 'CREATE_SCHOOL'
  | 'CLOSE_SCHOOL'
  | 'MERGE_SCHOOLS';

/** Crée une école fictive (lat/lon/capacity). */
export interface CreateSchoolOp {
  type: 'CREATE_SCHOOL';
  name: string;
  lat: number;
  lon: number;
  capacity: number;
  subPrefectureId?: string | null;
}

/** Ferme une école réelle existante (par id). */
export interface CloseSchoolOp {
  type: 'CLOSE_SCHOOL';
  schoolId: string;
}

/** Fusionne >=2 écoles en une nouvelle école fictive. */
export interface MergeSchoolsOp {
  type: 'MERGE_SCHOOLS';
  sourceSchoolIds: string[];
  targetName: string;
  lat: number;
  lon: number;
  subPrefectureId?: string | null;
}

/** Discriminated union sur le champ `type` — type-narrowing TS natif. */
export type Operation = CreateSchoolOp | CloseSchoolOp | MergeSchoolsOp;

/** Body POST /api/simulator/scenarios. */
export interface ScenarioCreate {
  name: string;
  description?: string | null;
  baselineSchoolYearId: string;
  operations: Operation[];
}

/** Couverture du réseau avant/après. */
export interface CoverageImpact {
  beforeCount: number;
  afterCount: number;
  /** Decimal côté backend — arrive en string ou en number selon Pydantic. */
  deltaPct: number | string;
}

/** Saturation moyenne et nb d'écoles critiques. */
export interface SaturationImpact {
  beforeAvg: number | string | null;
  afterAvg: number | string | null;
  criticalSchoolsBefore: number;
  criticalSchoolsAfter: number;
}

/** Distance moyenne école-élève (km). */
export interface DistanceImpact {
  beforeKmMean: number | string | null;
  afterKmMean: number | string | null;
  deltaKm: number | string | null;
}

/** Rapport d'impact d'un scénario. */
export interface ImpactReport {
  coverage: CoverageImpact;
  saturation: SaturationImpact;
  distance: DistanceImpact;
  redistributedStudents: number;
}

/**
 * Sortie GET /api/simulator/scenarios/{id}.
 *
 * - `scenarioJson` : payload tel que stocké (souvent { operations: [...] }).
 * - `impactJson`   : null tant que /compute n'a pas été appelé, sinon le
 *   même contenu que ImpactReport.
 */
export interface ScenarioRead {
  id: string;
  name: string;
  description?: string | null;
  status: ScenarioStatus;
  createdAt: string;
  createdById: string;
  baselineSchoolYearId: string;
  scenarioJson: unknown;
  impactJson: ImpactReport | null;
  computedAt: string | null;
}

/**
 * Service Angular dédié au simulateur what-if (Module 3B UI).
 *
 * - Toutes les méthodes retournent un Observable (pas de Promise).
 * - L'erreur HTTP est laissée remonter ; la page parente gère le toast.
 * - Pas d'état interne : c'est un wrapper HTTP pur, facile à mocker.
 */
@Injectable({ providedIn: 'root' })
export class SimulatorApiService {
  private http = inject(HttpClient);
  private base = `${environment.apiUrl}/simulator`;

  /** POST /scenarios — crée un scénario DRAFT. */
  createScenario(payload: ScenarioCreate): Observable<ScenarioRead> {
    return this.http.post<ScenarioRead>(`${this.base}/scenarios`, payload);
  }

  /** POST /scenarios/{id}/compute — calcule l'impact et bascule en COMPUTED. */
  compute(id: string): Observable<ImpactReport> {
    return this.http.post<ImpactReport>(
      `${this.base}/scenarios/${id}/compute`,
      {},
    );
  }

  /** GET /scenarios — liste visible (RBAC appliqué côté service backend). */
  listScenarios(): Observable<ScenarioRead[]> {
    return this.http.get<ScenarioRead[]>(`${this.base}/scenarios`);
  }

  /** GET /scenarios/{id} — détail d'un scénario. */
  getScenario(id: string): Observable<ScenarioRead> {
    return this.http.get<ScenarioRead>(`${this.base}/scenarios/${id}`);
  }

  /** POST /scenarios/{id}/archive — archive (statut ARCHIVED, masqué). */
  archiveScenario(id: string): Observable<ScenarioRead> {
    return this.http.post<ScenarioRead>(
      `${this.base}/scenarios/${id}/archive`,
      {},
    );
  }

  /**
   * Helper de coercion : le backend serialise les Decimal en string suivant
   * le JSON Pydantic — on garde un helper pour transformer en number
   * sans répéter `Number(x ?? 0)` partout dans les composants.
   */
  static toNumber(
    value: number | string | null | undefined,
  ): number | null {
    if (value === null || value === undefined || value === '') {
      return null;
    }
    const n = typeof value === 'number' ? value : Number(value);
    return Number.isFinite(n) ? n : null;
  }
}
