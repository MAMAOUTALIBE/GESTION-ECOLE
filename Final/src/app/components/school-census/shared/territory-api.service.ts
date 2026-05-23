import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { environment } from '../../../../environments/environment';
import { Prefecture, SubPrefecture } from './school-census.models';

export interface PrefecturePayload {
  name: string;
  code: string;
  regionId?: string;
}

export interface SubPrefecturePayload {
  name: string;
  code: string;
  prefectureId: string;
  regionId?: string;
}

@Injectable({ providedIn: 'root' })
export class TerritoryApiService {
  private http = inject(HttpClient);
  private baseUrl = `${environment.apiUrl}/territory`;

  prefectures() {
    return this.http.get<Prefecture[]>(`${this.baseUrl}/prefectures`);
  }

  createPrefecture(payload: PrefecturePayload) {
    return this.http.post<Prefecture>(`${this.baseUrl}/prefectures`, payload);
  }

  subPrefectures() {
    return this.http.get<SubPrefecture[]>(`${this.baseUrl}/sub-prefectures`);
  }

  createSubPrefecture(payload: SubPrefecturePayload) {
    return this.http.post<SubPrefecture>(`${this.baseUrl}/sub-prefectures`, payload);
  }
}
