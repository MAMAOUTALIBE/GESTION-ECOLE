import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { environment } from '../../../../environments/environment';
import {
  AcademicPeriodType,
  AcademicValidationStatus,
  Assessment,
  AssessmentType,
  Grade,
  ParentContact,
  ParentRelationType,
  ReportCard,
  SchoolYear,
  Subject,
} from './school-census.models';

export interface ParentPayload {
  firstName: string;
  lastName: string;
  phone: string;
  email?: string | null;
  profession?: string | null;
  address?: string | null;
  preferredLanguage?: string | null;
  links: Array<{
    studentId: string;
    relation: ParentRelationType;
    isPrimary?: boolean;
    isEmergencyContact?: boolean;
  }>;
}

export interface SchoolYearPayload {
  name: string;
  startDate: string;
  endDate: string;
  periodType?: AcademicPeriodType;
  isActive?: boolean;
}

export interface SubjectPayload {
  code: string;
  name: string;
  level?: string | null;
  coefficient?: number | null;
}

export interface AssessmentPayload {
  title: string;
  type: AssessmentType;
  coefficient?: number | null;
  maxScore?: number | null;
  assessedAt?: string | null;
  schoolYearId: string;
  periodId: string;
  subjectId: string;
  classRoomId: string;
  teacherId?: string | null;
}

export interface SaveGradesPayload {
  assessmentId: string;
  grades: Array<{
    studentId: string;
    score: number;
    appreciation?: string | null;
  }>;
}

@Injectable({ providedIn: 'root' })
export class AcademicsApiService {
  private http = inject(HttpClient);
  private baseUrl = `${environment.apiUrl}/academics`;

  listParents() {
    return this.http.get<ParentContact[]>(`${this.baseUrl}/parents`);
  }

  createParent(payload: ParentPayload) {
    return this.http.post<ParentContact>(`${this.baseUrl}/parents`, payload);
  }

  updateParent(id: string, payload: Partial<Omit<ParentPayload, 'links'>>) {
    return this.http.patch<ParentContact>(`${this.baseUrl}/parents/${id}`, payload);
  }

  deleteParent(id: string) {
    return this.http.delete<{ deleted: boolean }>(`${this.baseUrl}/parents/${id}`);
  }

  listSchoolYears() {
    return this.http.get<SchoolYear[]>(`${this.baseUrl}/school-years`);
  }

  createSchoolYear(payload: SchoolYearPayload) {
    return this.http.post<SchoolYear>(`${this.baseUrl}/school-years`, payload);
  }

  listSubjects() {
    return this.http.get<Subject[]>(`${this.baseUrl}/subjects`);
  }

  createSubject(payload: SubjectPayload) {
    return this.http.post<Subject>(`${this.baseUrl}/subjects`, payload);
  }

  listAssessments() {
    return this.http.get<Assessment[]>(`${this.baseUrl}/assessments`);
  }

  createAssessment(payload: AssessmentPayload) {
    return this.http.post<Assessment>(`${this.baseUrl}/assessments`, payload);
  }

  updateAssessmentStatus(id: string, status: AcademicValidationStatus) {
    return this.http.patch<Assessment>(`${this.baseUrl}/assessments/${id}/status`, { status });
  }

  listGrades(assessmentId?: string) {
    const suffix = assessmentId ? `?assessmentId=${encodeURIComponent(assessmentId)}` : '';
    return this.http.get<Grade[]>(`${this.baseUrl}/grades${suffix}`);
  }

  saveGrades(payload: SaveGradesPayload) {
    return this.http.post<Grade[]>(`${this.baseUrl}/grades/bulk`, payload);
  }

  listReportCards() {
    return this.http.get<ReportCard[]>(`${this.baseUrl}/report-cards`);
  }

  generateReportCards(payload: { schoolYearId: string; periodId: string; classRoomId?: string | null }) {
    return this.http.post<ReportCard[]>(`${this.baseUrl}/report-cards/generate`, payload);
  }

  updateReportCardStatus(id: string, status: AcademicValidationStatus) {
    return this.http.patch<ReportCard>(`${this.baseUrl}/report-cards/${id}/status`, { status });
  }
}
