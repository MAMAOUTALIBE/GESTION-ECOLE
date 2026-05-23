import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { environment } from '../../../../environments/environment';

export interface PlatformSetting {
  id: string;
  key: string;
  value: unknown;       // string | number | boolean | object selon valueType
  category: string;
  label: string;
  description: string | null;
  valueType: 'string' | 'number' | 'boolean' | 'json';
  updatedById: string | null;
}

export interface ImportTemplate {
  kind: 'students' | 'teachers' | 'schools';
  label: string;
  columns: string[];
  downloadUrl: string;
}

@Injectable({ providedIn: 'root' })
export class AdminApiService {
  private http = inject(HttpClient);
  private baseUrl = `${environment.apiUrl}/admin`;

  listSettings() {
    return this.http.get<PlatformSetting[]>(`${this.baseUrl}/settings`);
  }

  updateSetting(key: string, value: unknown) {
    return this.http.patch<PlatformSetting>(
      `${this.baseUrl}/settings/${encodeURIComponent(key)}`,
      { value },
    );
  }

  /** GET /api/imports/templates — liste des kinds disponibles. */
  listImportTemplates() {
    return this.http.get<ImportTemplate[]>(`${environment.apiUrl}/imports/templates`);
  }
}
