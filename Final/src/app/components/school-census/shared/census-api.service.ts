import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { environment } from '../../../../environments/environment';
import {
  AttendanceRecord,
  AttendanceStatus,
  CensusDashboard,
  CensusMetadata,
  CensusPerson,
  DashboardFilters,
  Gender,
} from './school-census.models';

export interface CreateStudentPayload {
  firstName: string;
  lastName: string;
  gender: Gender;
  birthDate?: string;
  photoUrl?: string;
  guardianName?: string;
  guardianPhone?: string;
  schoolId: string;
  classRoomId?: string;
}

export interface CreateTeacherPayload {
  firstName: string;
  lastName: string;
  gender: Gender;
  birthDate?: string;
  photoUrl?: string;
  phone?: string;
  subject?: string;
  diploma?: string;
  schoolId: string;
  classRoomIds?: string[];
}

@Injectable({ providedIn: 'root' })
export class CensusApiService {
  private http = inject(HttpClient);
  private baseUrl = `${environment.apiUrl}/census`;

  dashboard(filters: DashboardFilters = {}) {
    let params = new HttpParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value) {
        params = params.set(key, value);
      }
    });

    return this.http.get<CensusDashboard>(`${this.baseUrl}/dashboard`, { params });
  }

  metadata() {
    return this.http.get<CensusMetadata>(`${this.baseUrl}/metadata`);
  }

  students() {
    return this.http.get<CensusPerson[]>(`${this.baseUrl}/students`);
  }

  studentCards() {
    return this.http.get<CensusPerson[]>(`${this.baseUrl}/students/cards`);
  }

  student(id: string) {
    return this.http.get<CensusPerson>(`${this.baseUrl}/students/${id}`);
  }

  createStudent(payload: CreateStudentPayload) {
    return this.http.post<CensusPerson>(`${this.baseUrl}/students`, payload);
  }

  assignStudentClass(id: string, classRoomId?: string) {
    return this.http.patch<CensusPerson>(`${this.baseUrl}/students/${id}/class`, {
      classRoomId: classRoomId || null,
    });
  }

  transferStudent(id: string, payload: { toSchoolId: string; toClassRoomId?: string; reason?: string }) {
    return this.http.post<CensusPerson>(`${this.baseUrl}/students/${id}/transfer`, payload);
  }

  teachers() {
    return this.http.get<CensusPerson[]>(`${this.baseUrl}/teachers`);
  }

  teacherCards() {
    return this.http.get<CensusPerson[]>(`${this.baseUrl}/teachers/cards`);
  }

  teacher(id: string) {
    return this.http.get<CensusPerson>(`${this.baseUrl}/teachers/${id}`);
  }

  createTeacher(payload: CreateTeacherPayload) {
    return this.http.post<CensusPerson>(`${this.baseUrl}/teachers`, payload);
  }

  assignTeacherClasses(id: string, classRoomIds: string[]) {
    return this.http.patch<CensusPerson>(`${this.baseUrl}/teachers/${id}/classes`, { classRoomIds });
  }

  identify(token: string) {
    return this.http.get<{ personType: string; person: CensusPerson }>(`${this.baseUrl}/identify/${token}`);
  }

  scan(qrToken: string, status: AttendanceStatus = 'PRESENT') {
    return this.http.post<{ duplicate: boolean; record: AttendanceRecord }>(
      `${environment.apiUrl}/attendance/scan`,
      { qrToken, status },
    );
  }

  todayAttendance() {
    return this.http.get<AttendanceRecord[]>(`${environment.apiUrl}/attendance/today`);
  }
}
