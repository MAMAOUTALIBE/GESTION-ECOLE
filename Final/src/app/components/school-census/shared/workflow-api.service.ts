import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { environment } from '../../../../environments/environment';
import { ValidationStatus } from './school-census.models';

export interface ValidationRequest {
  id: string;
  entityType: 'PREFECTURE' | 'SUB_PREFECTURE' | 'SCHOOL' | 'TEACHER';
  entityId: string;
  status: ValidationStatus;
  reviewerRole: string;
  reason?: string | null;
  createdAt: string;
  reviewedAt?: string | null;
  requestedBy?: { id: string; fullName: string; email: string; role: string };
  reviewer?: { id: string; fullName: string; email: string; role: string } | null;
}

export interface AppNotification {
  id: string;
  title: string;
  message: string;
  type: string;
  entityType?: string | null;
  entityId?: string | null;
  isRead: boolean;
  createdAt: string;
}

@Injectable({ providedIn: 'root' })
export class WorkflowApiService {
  private http = inject(HttpClient);
  private baseUrl = environment.apiUrl;

  validationRequests(status?: ValidationStatus) {
    const params = status ? new HttpParams().set('status', status) : undefined;
    return this.http.get<ValidationRequest[]>(`${this.baseUrl}/validation-requests`, { params });
  }

  reviewValidationRequest(id: string, status: 'APPROVED' | 'REJECTED', reason?: string) {
    return this.http.patch<ValidationRequest>(`${this.baseUrl}/validation-requests/${id}/review`, {
      status,
      reason,
    });
  }

  notifications(unreadOnly = false) {
    const params = unreadOnly ? new HttpParams().set('unreadOnly', 'true') : undefined;
    return this.http.get<AppNotification[]>(`${this.baseUrl}/notifications`, { params });
  }

  unreadCount() {
    return this.http.get<{ count: number }>(`${this.baseUrl}/notifications/unread-count`);
  }

  markNotificationRead(id: string) {
    return this.http.patch<AppNotification>(`${this.baseUrl}/notifications/${id}/read`, {});
  }
}
