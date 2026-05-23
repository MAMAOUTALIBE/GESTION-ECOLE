import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { environment } from '../../../../environments/environment';

export type LibraryStatus = 'sufficient' | 'watch' | 'shortage';
export type LibraryLoanStatus = 'borrowed' | 'late' | 'returned';

export interface LibraryInventoryRow {
  id: string;
  schoolId: string;
  schoolName: string;
  code: string;
  regionId: string;
  region: string;
  level: string;
  subjectName: string;
  title: string;
  stock: number;
  loaned: number;
  damaged: number;
  required: number;
  coverageRate: number;
  status: LibraryStatus;
  lastInventory: string;
}

export interface LibraryLoanRow {
  id: string;
  studentName: string;
  uniqueCode: string;
  schoolName: string;
  className: string;
  title: string;
  borrowedAt: string;
  dueAt: string;
  status: LibraryLoanStatus;
}

export interface LibraryListResponse<T> {
  rows: T[];
  total: number;
  page: number;
  pageSize: number;
}

export interface LibraryInventoryQuery {
  search?: string;
  regionId?: string;
  schoolId?: string;
  subjectId?: string;
  status?: LibraryStatus;
  page?: number;
  pageSize?: number;
}

export interface LibraryLoansQuery {
  search?: string;
  regionId?: string;
  schoolId?: string;
  status?: LibraryLoanStatus;
  page?: number;
  pageSize?: number;
}

@Injectable({ providedIn: 'root' })
export class LibraryApiService {
  private http = inject(HttpClient);
  private baseUrl = `${environment.apiUrl}/library`;

  inventory(query: LibraryInventoryQuery = {}) {
    return this.http.get<LibraryListResponse<LibraryInventoryRow>>(`${this.baseUrl}/inventory`, {
      params: this.params(query),
    });
  }

  loans(query: LibraryLoansQuery = {}) {
    return this.http.get<LibraryListResponse<LibraryLoanRow>>(`${this.baseUrl}/loans`, {
      params: this.params(query),
    });
  }

  private params(query: LibraryInventoryQuery | LibraryLoansQuery) {
    let params = new HttpParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== '') {
        params = params.set(key, String(value));
      }
    });
    return params;
  }
}
