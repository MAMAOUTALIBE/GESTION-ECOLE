import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { environment } from '../../../../environments/environment';

// =====================================================================
// Types (miroir des schémas Pydantic backend — Phase 8 + Phase 10)
// =====================================================================
export type TerritoryLevel = 'region' | 'prefecture' | 'sub-prefecture';
export type TopMetric = 'students' | 'attendance' | 'gps' | 'ratio';

export interface NationalKpis {
  students: number;
  teachers: number;
  schools: number;
  classes: number;
  regions: number;
  studentsPerTeacher: number;
  studentsPerSchool: number;
  teachersPerSchool: number;
  geolocatedSchools: number;
  gpsCoverageRate: number;
  approvedSchools: number;
  pendingSchools: number;
  attendanceLast7Days: number;
  presentLast7Days: number;
  absentLast7Days: number;
  lateLast7Days: number;
  presenceRateLast7Days: number;
  parentReachable: number;
  parentReachableRate: number;
}

export interface TerritoryRow {
  id: string;
  name: string;
  parentId: string | null;
  parentName: string | null;
  schools: number;
  students: number;
  teachers: number;
  classes: number;
  geolocatedSchools: number;
  gpsCoverageRate: number;
  studentsPerTeacher: number;
  studentsPerSchool: number;
}

export interface TerritoriesResponse {
  level: TerritoryLevel;
  total: number;
  rows: TerritoryRow[];
}

export interface AttendancePoint {
  day: string; // YYYY-MM-DD
  present: number;
  late: number;
  absent: number;
  total: number;
  presenceRate: number;
}

export interface AttendanceTrends {
  days: number;
  points: AttendancePoint[];
}

export interface EnrollmentPoint {
  month: string; // YYYY-MM
  students: number;
  teachers: number;
}

export interface EnrollmentTrends {
  months: number;
  points: EnrollmentPoint[];
}

export interface TopSchoolRow {
  id: string;
  code: string;
  name: string;
  regionId: string | null;
  regionName: string | null;
  students: number;
  teachers: number;
  classes: number;
  presenceRateLast7Days: number | null;
  gpsCoverageRate: number | null;
}

export interface TopSchoolsResponse {
  metric: TopMetric;
  limit: number;
  rows: TopSchoolRow[];
}

export interface QualityResponse {
  score: number;
  studentsTotal: number;
  studentsWithoutClass: number;
  studentsWithoutPhoto: number;
  studentsMissingBirthDate: number;
  teachersTotal: number;
  teachersWithoutClasses: number;
  teachersWithoutPhoto: number;
  teachersMissingBirthDate: number;
  schoolsTotal: number;
  schoolsMissingCoordinates: number;
  schoolsMissingPhone: number;
}

export interface CohortLevelStats {
  level: string;
  enrolled: number;
  male: number;
  female: number;
  repeaters: number;
  averageAge: number | null;
}

export interface CohortReport {
  schoolYearId: string | null;
  schoolYearName: string | null;
  levels: CohortLevelStats[];
  totalStudents: number;
  totalRepeaters: number;
  repeaterRate: number;
}

export interface EquityRow {
  territoryId: string | null;
  territoryName: string;
  students: number;
  male: number;
  female: number;
  genderParityIndex: number;
  schoolsTotal: number;
  schoolsWithGirlsToilets: number;
  girlsToiletsCoverage: number;
  schoolsWithElectricity: number;
  electricityCoverage: number;
  schoolsWithWater: number;
  waterCoverage: number;
}

export interface EquityResponse {
  rows: EquityRow[];
  nationalGpi: number;
  nationalGirlsToiletsCoverage: number;
  nationalElectricityCoverage: number;
  nationalWaterCoverage: number;
}

export interface PolicySimulationRequest {
  regionId?: string | null;
  addSchools?: number;
  addTeachers?: number;
  addClassrooms?: number;
  targetGirlsToiletsCoverage?: number | null;
  targetElectricityCoverage?: number | null;
  horizonYears?: number;
}

export interface PolicySimulationDelta {
  metric: string;
  baseline: number;
  scenario: number;
  delta: number;
  deltaPct: number | null;
  interpretation: string;
}

export interface PolicySimulationResponse {
  regionId: string | null;
  horizonYears: number;
  baseline: Record<string, number>;
  scenario: Record<string, number>;
  deltas: PolicySimulationDelta[];
  estimatedAdditionalStudentsCovered: number;
  estimatedCostUSD: number;
  notes: string[];
}

export interface AuditLogRow {
  id: string;
  actorId: string | null;
  action: string;
  entity: string;
  entityId: string | null;
  metadata: Record<string, unknown> | null;
  createdAt: string;
}

export interface AuditLogPage {
  rows: AuditLogRow[];
  total: number;
  page: number;
  pageSize: number;
}

export interface AuditLogQuery {
  actorId?: string;
  entity?: string;
  entityId?: string;
  action?: string;
  page?: number;
  pageSize?: number;
}

export type ExportType = 'national' | 'territories' | 'top-schools' | 'quality';

@Injectable({ providedIn: 'root' })
export class AnalyticsApiService {
  private http = inject(HttpClient);
  private baseUrl = `${environment.apiUrl}/analytics`;

  national() {
    return this.http.get<NationalKpis>(`${this.baseUrl}/national`);
  }

  territories(level: TerritoryLevel = 'region') {
    return this.http.get<TerritoriesResponse>(`${this.baseUrl}/territories`, {
      params: new HttpParams().set('level', level),
    });
  }

  attendanceTrends(days = 30) {
    return this.http.get<AttendanceTrends>(`${this.baseUrl}/attendance/trends`, {
      params: new HttpParams().set('days', String(days)),
    });
  }

  enrollmentTrends(months = 12) {
    return this.http.get<EnrollmentTrends>(`${this.baseUrl}/enrollment/trends`, {
      params: new HttpParams().set('months', String(months)),
    });
  }

  topSchools(metric: TopMetric = 'students', limit = 10) {
    return this.http.get<TopSchoolsResponse>(`${this.baseUrl}/top-schools`, {
      params: new HttpParams().set('metric', metric).set('limit', String(limit)),
    });
  }

  quality() {
    return this.http.get<QualityResponse>(`${this.baseUrl}/quality`);
  }

  // Phase 10 — pouvoir décisionnel
  cohorts(schoolYearId?: string) {
    let params = new HttpParams();
    if (schoolYearId) {
      params = params.set('schoolYearId', schoolYearId);
    }
    return this.http.get<CohortReport>(`${this.baseUrl}/cohorts`, { params });
  }

  equity() {
    return this.http.get<EquityResponse>(`${this.baseUrl}/equity`);
  }

  policySimulator(payload: PolicySimulationRequest) {
    return this.http.post<PolicySimulationResponse>(
      `${this.baseUrl}/policy-simulator`,
      payload,
    );
  }

  auditLogs(query: AuditLogQuery = {}) {
    let params = new HttpParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        params = params.set(key, String(value));
      }
    });
    return this.http.get<AuditLogPage>(`${this.baseUrl}/audit-logs`, { params });
  }

  exportCsvUrl(
    type: ExportType,
    options: { level?: TerritoryLevel; metric?: TopMetric; limit?: number } = {},
  ) {
    let params = new HttpParams().set('type', type);
    if (options.level) {
      params = params.set('level', options.level);
    }
    if (options.metric) {
      params = params.set('metric', options.metric);
    }
    if (options.limit !== undefined) {
      params = params.set('limit', String(options.limit));
    }
    return `${this.baseUrl}/export?${params.toString()}`;
  }
}
