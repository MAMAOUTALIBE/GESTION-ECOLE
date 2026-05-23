import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { environment } from '../../../../environments/environment';

// =====================================================================
// Types — miroir des schémas Pydantic Phase 13
// =====================================================================
export type IncidentType =
  | 'LATENESS' | 'INSUBORDINATION' | 'FIGHTING' | 'ABSENCE'
  | 'BULLYING' | 'PROPERTY_DAMAGE' | 'OTHER';
export type IncidentSeverity = 'LOW' | 'MEDIUM' | 'HIGH';
export type IncidentSanction =
  | 'NONE' | 'WARNING' | 'DETENTION' | 'PARENT_MEETING' | 'SUSPENSION' | 'EXPULSION';

export type HealthVisitType = 'CHECKUP' | 'ILLNESS' | 'INJURY' | 'VACCINATION' | 'OTHER';
export type HealthVisitStatus = 'REPORTED' | 'TREATED' | 'REFERRED' | 'RESOLVED';

export type TransportRouteStatus = 'ACTIVE' | 'MAINTENANCE' | 'INACTIVE';

export type MealServiceType = 'BREAKFAST' | 'LUNCH' | 'SNACK';

export type DayOfWeek = 'MONDAY' | 'TUESDAY' | 'WEDNESDAY' | 'THURSDAY' | 'FRIDAY' | 'SATURDAY';

export interface SchoolBrief { id: string; name: string; code: string; }
export interface StudentBrief { id: string; firstName: string; lastName: string; uniqueCode: string; }
export interface SubjectBrief { id: string; name: string; code: string; }
export interface TeacherBrief { id: string; firstName: string; lastName: string; }
export interface ClassRoomBrief { id: string; name: string; level: string | null; }

export interface IncidentRow {
  id: string;
  schoolId: string;
  school: SchoolBrief | null;
  studentId: string | null;
  student: StudentBrief | null;
  type: IncidentType;
  severity: IncidentSeverity;
  description: string;
  sanction: IncidentSanction;
  occurredAt: string;
  recordedById: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface HealthVisitRow {
  id: string;
  schoolId: string;
  school: SchoolBrief | null;
  studentId: string | null;
  student: StudentBrief | null;
  type: HealthVisitType;
  description: string;
  visitDate: string;
  nurseName: string | null;
  status: HealthVisitStatus;
  createdAt: string;
  updatedAt: string;
}

export interface BusRouteRow {
  id: string;
  schoolId: string;
  school: SchoolBrief | null;
  name: string;
  capacity: number;
  departureTime: string;
  returnTime: string;
  driverName: string | null;
  driverPhone: string | null;
  plate: string | null;
  studentsAssigned: number;
  status: TransportRouteStatus;
  createdAt: string;
  updatedAt: string;
}

export interface MealServiceRow {
  id: string;
  schoolId: string;
  school: SchoolBrief | null;
  type: MealServiceType;
  serviceDate: string;
  mealsPlanned: number;
  mealsServed: number;
  costPerMealGNF: number;
  notes: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface TimetableSlotRow {
  id: string;
  classRoomId: string;
  classRoom: ClassRoomBrief | null;
  dayOfWeek: DayOfWeek;
  startTime: string;
  endTime: string;
  subjectId: string | null;
  subject: SubjectBrief | null;
  teacherId: string | null;
  teacher: TeacherBrief | null;
  room: string | null;
  createdAt: string;
  updatedAt: string;
}

@Injectable({ providedIn: 'root' })
export class SchoolLifeApiService {
  private http = inject(HttpClient);
  private baseUrl = `${environment.apiUrl}/schoollife`;

  listIncidents(query: { schoolId?: string; severity?: IncidentSeverity; limit?: number } = {}) {
    return this.http.get<IncidentRow[]>(`${this.baseUrl}/incidents`, {
      params: this.params(query),
    });
  }

  listHealthVisits(query: { schoolId?: string; limit?: number } = {}) {
    return this.http.get<HealthVisitRow[]>(`${this.baseUrl}/health-visits`, {
      params: this.params(query),
    });
  }

  listBusRoutes(query: { schoolId?: string; limit?: number } = {}) {
    return this.http.get<BusRouteRow[]>(`${this.baseUrl}/bus-routes`, {
      params: this.params(query),
    });
  }

  listMeals(query: { schoolId?: string; limit?: number } = {}) {
    return this.http.get<MealServiceRow[]>(`${this.baseUrl}/meals`, {
      params: this.params(query),
    });
  }

  listTimetable(query: { classRoomId?: string; schoolId?: string; limit?: number } = {}) {
    return this.http.get<TimetableSlotRow[]>(`${this.baseUrl}/timetable`, {
      params: this.params(query),
    });
  }

  private params(query: object): HttpParams {
    let params = new HttpParams();
    Object.entries(query as Record<string, unknown>).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') {
        params = params.set(k, String(v));
      }
    });
    return params;
  }
}
