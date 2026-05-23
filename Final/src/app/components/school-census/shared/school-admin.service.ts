import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { environment } from '../../../../environments/environment';
import { ClassRoom, School } from './school-census.models';

export interface SchoolPayload {
  name: string;
  code: string;
  regionId: string;
  prefectureId?: string | null;
  subPrefectureId?: string | null;
  prefecture?: string | null;
  commune?: string | null;
  type?: string | null;
  address?: string | null;
  phone?: string | null;
  latitude?: number | null;
  longitude?: number | null;
}

export interface ClassRoomPayload {
  name: string;
  level?: string | null;
  maxStudents?: number | null;
  schoolYear?: string | null;
  schoolId: string;
}

@Injectable({ providedIn: 'root' })
export class SchoolAdminService {
  private http = inject(HttpClient);

  listSchools() {
    return this.http.get<School[]>(`${environment.apiUrl}/schools`);
  }

  createSchool(payload: SchoolPayload) {
    return this.http.post<School>(`${environment.apiUrl}/schools`, payload);
  }

  updateSchool(id: string, payload: Partial<SchoolPayload>) {
    return this.http.patch<School>(`${environment.apiUrl}/schools/${id}`, payload);
  }

  deleteSchool(id: string) {
    return this.http.delete<{ deleted: boolean }>(`${environment.apiUrl}/schools/${id}`);
  }

  listClasses() {
    return this.http.get<ClassRoom[]>(`${environment.apiUrl}/classes`);
  }

  createClass(payload: ClassRoomPayload) {
    return this.http.post<ClassRoom>(`${environment.apiUrl}/classes`, payload);
  }

  updateClass(id: string, payload: Partial<ClassRoomPayload>) {
    return this.http.patch<ClassRoom>(`${environment.apiUrl}/classes/${id}`, payload);
  }

  deleteClass(id: string) {
    return this.http.delete<{ deleted: boolean }>(`${environment.apiUrl}/classes/${id}`);
  }
}
