import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { environment } from '../../../../environments/environment';

// =====================================================================
// Phase 14 — Intelligence (predictions + anomalies + forecasts + assistant)
// =====================================================================

export interface DropoutRiskRow {
  studentId: string;
  studentName: string;
  uniqueCode: string;
  schoolId: string;
  schoolName: string;
  classLevel: string | null;
  riskScore: number;
  riskLevel: 'low' | 'medium' | 'high' | 'critical';
  drivers: string[];
  absenceRate30d: number;
  presentDays: number;
  absentDays: number;
}

export interface DropoutSummary {
  total: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  rows: DropoutRiskRow[];
}

export interface ForecastPoint {
  month: string;
  newStudents: number;
  cumulativeTotal: number;
}

export interface EnrollmentForecast {
  horizonYears: number;
  history: { month: string; students: number }[];
  forecast: ForecastPoint[];
  annualGrowthPct: number;
  totalCurrent: number;
  totalForecasted: number;
  method: string;
}

export interface Anomaly {
  type: 'GRADE_INFLATION' | 'PRESENCE_WITHOUT_GRADES' | 'RAPID_SCAN' | 'EXTREME_RATIO';
  severity: 'low' | 'medium' | 'high';
  entityKind: string;
  entityId: string;
  label: string;
  detail: string;
  metric: number | null;
}

export interface SiteRecommendation {
  prefectureId: string;
  prefectureName: string;
  regionName: string;
  currentSchoolCount: number;
  suggestedLatitude: number;
  suggestedLongitude: number;
  rationale: string;
  estimatedCostUSD: number;
}

export interface DiplomaVerifyResponse {
  valid: boolean;
  verificationCode: string;
  studentName: string | null;
  schoolName: string | null;
  classLevel: string | null;
  average: number | null;
  rank: number | null;
  totalStudents: number | null;
  issuedAt: string | null;
  signature: string | null;
  signatureAlgorithm: string;
  message: string | null;
}

export interface AssistantChatRequest {
  message: string;
  conversationId?: string;
}

export interface AssistantChatResponse {
  reply: string;
  citations: { source: string }[];
  toolsUsed: string[];
}

export interface OpenDataNationalStats {
  country: string;
  totals: {
    students: number; teachers: number; schools: number;
    classes: number; regions: number;
  };
  ratios: {
    studentsPerTeacher: number;
    studentsPerSchool: number;
    averageClassSize: number;
  };
  coverage: {
    waterAccessPct: number;
    electricityAccessPct: number;
  };
  license: string;
  source: string;
}

@Injectable({ providedIn: 'root' })
export class IntelligenceApiService {
  private http = inject(HttpClient);
  private base = environment.apiUrl;

  // ---- Predictions ----
  dropoutRisk(query: { schoolId?: string; limit?: number; minScore?: number } = {}) {
    let p = new HttpParams();
    Object.entries(query).forEach(([k, v]) => {
      if (v !== undefined && v !== null) p = p.set(k, String(v));
    });
    return this.http.get<DropoutSummary>(`${this.base}/predictions/dropout-risk`, { params: p });
  }

  // ---- Forecast ----
  enrollmentForecast(horizonYears = 5) {
    return this.http.get<EnrollmentForecast>(
      `${this.base}/analytics/enrollment/forecast`,
      { params: new HttpParams().set('horizonYears', String(horizonYears)) },
    );
  }

  // ---- Anomalies ----
  scanAnomalies(limit = 50) {
    return this.http.get<Anomaly[]>(
      `${this.base}/anomalies/scan`,
      { params: new HttpParams().set('limit', String(limit)) },
    );
  }

  // ---- Site recommendations ----
  siteRecommendations(topN = 10, radiusKm = 5) {
    let p = new HttpParams().set('topN', String(topN)).set('radiusKm', String(radiusKm));
    return this.http.get<{ radiusKm: number; recommendations: SiteRecommendation[] }>(
      `${this.base}/cartography/site-recommendations`, { params: p },
    );
  }

  // ---- Diploma verification (PUBLIC) ----
  verifyDiploma(code: string) {
    return this.http.get<DiplomaVerifyResponse>(
      `${this.base}/diplomas/verify/${encodeURIComponent(code)}`,
    );
  }

  // ---- OpenData (PUBLIC) ----
  publicNationalStats() {
    return this.http.get<OpenDataNationalStats>(`${this.base}/opendata/national-stats`);
  }

  // ---- Assistant LLM ----
  chat(req: AssistantChatRequest) {
    return this.http.post<AssistantChatResponse>(`${this.base}/assistant/chat`, req);
  }
}
