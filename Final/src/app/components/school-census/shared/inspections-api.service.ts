import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { environment } from '../../../../environments/environment';

// =====================================================================
// Types (miroir des schémas Pydantic backend — Phase 10)
// =====================================================================
export type InspectionStatus = 'PLANNED' | 'IN_PROGRESS' | 'COMPLETED' | 'CANCELLED';
export type InspectionCriterion =
  | 'GOVERNANCE' | 'PEDAGOGY' | 'INFRASTRUCTURE' | 'SAFETY'
  | 'HYGIENE' | 'EQUITY' | 'ATTENDANCE' | 'DOCUMENTS';
export type FindingSeverity = 'INFO' | 'MINOR' | 'MAJOR' | 'CRITICAL';
export type ActionItemStatus = 'OPEN' | 'IN_PROGRESS' | 'RESOLVED' | 'CANCELLED';

export interface SchoolBrief {
  id: string;
  name: string;
  code: string;
}

export interface InspectorBrief {
  id: string;
  fullName: string;
  email: string;
}

export interface FindingRead {
  id: string;
  criterion: InspectionCriterion;
  score: number;
  severity: FindingSeverity;
  comment: string | null;
  photoUrl: string | null;
  createdAt: string;
}

export interface ActionItemRead {
  id: string;
  description: string;
  dueDate: string;
  status: ActionItemStatus;
  resolvedAt: string | null;
  resolvedById: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface InspectionRead {
  id: string;
  schoolId: string;
  school: SchoolBrief | null;
  inspectorId: string;
  inspector: InspectorBrief | null;
  scheduledDate: string;
  performedDate: string | null;
  status: InspectionStatus;
  overallScore: number | null;
  notes: string | null;
  findings: FindingRead[];
  actionItems: ActionItemRead[];
  createdAt: string;
  updatedAt: string;
}

export interface InspectionListItem {
  id: string;
  schoolId: string;
  school: SchoolBrief | null;
  inspectorId: string;
  inspector: InspectorBrief | null;
  scheduledDate: string;
  performedDate: string | null;
  status: InspectionStatus;
  overallScore: number | null;
  findingsCount: number;
  actionItemsOpen: number;
}

export interface InspectionPage {
  rows: InspectionListItem[];
  total: number;
  page: number;
  pageSize: number;
}

export interface InspectionStats {
  total: number;
  planned: number;
  inProgress: number;
  completed: number;
  cancelled: number;
  averageScoreLast90Days: number | null;
  criticalFindingsLast90Days: number;
  overdueActions: number;
}

export interface CreateInspectionRequest {
  schoolId: string;
  inspectorId?: string | null;
  scheduledDate: string;
  notes?: string | null;
}

export interface UpdateInspectionRequest {
  status?: InspectionStatus;
  performedDate?: string | null;
  notes?: string | null;
}

export interface CreateFindingRequest {
  criterion: InspectionCriterion;
  score: number; // 0..5
  severity?: FindingSeverity;
  comment?: string | null;
  photoUrl?: string | null;
}

export interface CreateActionItemRequest {
  description: string;
  dueDate: string;
}

export interface UpdateActionItemRequest {
  status: ActionItemStatus;
  resolutionNote?: string | null;
}

export interface InspectionsListQuery {
  schoolId?: string;
  status?: InspectionStatus;
  page?: number;
  pageSize?: number;
}

@Injectable({ providedIn: 'root' })
export class InspectionsApiService {
  private http = inject(HttpClient);
  private baseUrl = `${environment.apiUrl}/inspections`;

  list(query: InspectionsListQuery = {}) {
    let params = new HttpParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        params = params.set(key, String(value));
      }
    });
    return this.http.get<InspectionPage>(this.baseUrl, { params });
  }

  stats() {
    return this.http.get<InspectionStats>(`${this.baseUrl}/stats`);
  }

  get(id: string) {
    return this.http.get<InspectionRead>(`${this.baseUrl}/${id}`);
  }

  create(payload: CreateInspectionRequest) {
    return this.http.post<InspectionRead>(this.baseUrl, payload);
  }

  update(id: string, payload: UpdateInspectionRequest) {
    return this.http.patch<InspectionRead>(`${this.baseUrl}/${id}`, payload);
  }

  addFinding(inspectionId: string, payload: CreateFindingRequest) {
    return this.http.post<FindingRead>(
      `${this.baseUrl}/${inspectionId}/findings`,
      payload,
    );
  }

  addAction(inspectionId: string, payload: CreateActionItemRequest) {
    return this.http.post<ActionItemRead>(
      `${this.baseUrl}/${inspectionId}/actions`,
      payload,
    );
  }

  updateAction(actionId: string, payload: UpdateActionItemRequest) {
    return this.http.patch<ActionItemRead>(
      `${this.baseUrl}/actions/${actionId}`,
      payload,
    );
  }
}
