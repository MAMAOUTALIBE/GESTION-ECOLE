import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Router } from '@angular/router';
import { BehaviorSubject, firstValueFrom, tap } from 'rxjs';
import { environment } from '../../../environments/environment';

export type UserRole =
  | 'NATIONAL_ADMIN'
  | 'MINISTRY_ADMIN'
  | 'REGIONAL_ADMIN'
  | 'INSPECTOR'
  | 'PREFECTURE_ADMIN'
  | 'SUB_PREFECTURE_ADMIN'
  | 'SCHOOL_DIRECTOR'
  | 'TEACHER'
  | 'CENSUS_AGENT';

export const NATIONAL_SCOPE_ROLES: UserRole[] = ['NATIONAL_ADMIN', 'MINISTRY_ADMIN'];
export const REGIONAL_SCOPE_ROLES: UserRole[] = ['REGIONAL_ADMIN', 'INSPECTOR'];
export const PREFECTURE_SCOPE_ROLES: UserRole[] = ['PREFECTURE_ADMIN'];
export const SUB_PREFECTURE_SCOPE_ROLES: UserRole[] = ['SUB_PREFECTURE_ADMIN'];
export const SCHOOL_SCOPE_ROLES: UserRole[] = ['SCHOOL_DIRECTOR', 'TEACHER', 'CENSUS_AGENT'];
export const CENSUS_READ_ROLES: UserRole[] = [
  ...NATIONAL_SCOPE_ROLES,
  ...REGIONAL_SCOPE_ROLES,
  ...PREFECTURE_SCOPE_ROLES,
  ...SUB_PREFECTURE_SCOPE_ROLES,
  ...SCHOOL_SCOPE_ROLES,
];
export const CENSUS_WRITE_ROLES: UserRole[] = [
  ...NATIONAL_SCOPE_ROLES,
  'REGIONAL_ADMIN',
  'PREFECTURE_ADMIN',
  'SUB_PREFECTURE_ADMIN',
  'SCHOOL_DIRECTOR',
  'CENSUS_AGENT',
];
export const SCHOOL_MANAGEMENT_ROLES: UserRole[] = [
  ...NATIONAL_SCOPE_ROLES,
  'REGIONAL_ADMIN',
  'PREFECTURE_ADMIN',
  'SUB_PREFECTURE_ADMIN',
];
export const CLASS_MANAGEMENT_ROLES: UserRole[] = [...SCHOOL_MANAGEMENT_ROLES, 'SCHOOL_DIRECTOR'];
export const ACADEMIC_WRITE_ROLES: UserRole[] = [
  ...NATIONAL_SCOPE_ROLES,
  'REGIONAL_ADMIN',
  'PREFECTURE_ADMIN',
  'SUB_PREFECTURE_ADMIN',
  'SCHOOL_DIRECTOR',
  'TEACHER',
  'CENSUS_AGENT',
];
export const ACADEMIC_VALIDATION_ROLES: UserRole[] = [
  ...NATIONAL_SCOPE_ROLES,
  'REGIONAL_ADMIN',
  'PREFECTURE_ADMIN',
  'SCHOOL_DIRECTOR',
];

export interface AuthUser {
  id: string;
  email: string;
  fullName: string;
  role: UserRole;
  region?: { id: string; name: string; code: string } | null;
  prefecture?: { id: string; name: string; code: string } | null;
  subPrefecture?: { id: string; name: string; code: string } | null;
  school?: { id: string; name: string; code: string } | null;
}

export interface AuthSession {
  accessToken: string;
  user: AuthUser;
}

/** Item retourné par GET /api/auth/users (annuaire admin). */
export interface UserDirectoryEntry extends AuthUser {
  isActive: boolean;
  regionId: string | null;
  prefectureId: string | null;
  subPrefectureId: string | null;
  schoolId: string | null;
  createdAt: string;
  updatedAt: string;
}

@Injectable({
  providedIn: 'root',
})
export class AuthService {
  private http = inject(HttpClient);
  private router = inject(Router);
  private storageKey = 'gestion-ee-session';
  private sessionSubject = new BehaviorSubject<AuthSession | null>(this.readSession());

  session$ = this.sessionSubject.asObservable();
  public showLoader = false;

  get session(): AuthSession | null {
    return this.sessionSubject.value;
  }

  get token(): string | null {
    return this.session?.accessToken ?? null;
  }

  get currentUser(): AuthUser | null {
    return this.session?.user ?? null;
  }

  get currentUserName(): string {
    return this.currentUser?.fullName ?? '';
  }

  get currentUserId(): string {
    return this.currentUser?.id ?? '';
  }

  get isAuthenticated(): boolean {
    return Boolean(this.token);
  }

  login(email: string, password: string) {
    this.showLoader = true;
    return this.http
      .post<AuthSession>(`${environment.apiUrl}/auth/login`, {
        email: email.toLowerCase().trim(),
        password,
      })
      .pipe(
        tap((session) => {
          this.persistSession(session);
          this.showLoader = false;
        }),
      );
  }

  loginWithEmail(email: string, password: string) {
    return firstValueFrom(this.login(email, password));
  }

  /** Annuaire des utilisateurs (admin uniquement). */
  listUsers() {
    return this.http.get<UserDirectoryEntry[]>(`${environment.apiUrl}/auth/users`);
  }

  hasAnyRole(roles: readonly UserRole[]): boolean {
    const role = this.currentUser?.role;
    return !roles.length || Boolean(role && roles.includes(role));
  }

  logout(): void {
    localStorage.removeItem(this.storageKey);
    this.sessionSubject.next(null);
    this.router.navigate(['/auth/login']);
  }

  singout(): void {
    this.logout();
  }

  private persistSession(session: AuthSession): void {
    localStorage.setItem(this.storageKey, JSON.stringify(session));
    this.sessionSubject.next(session);
  }

  private readSession(): AuthSession | null {
    const raw = localStorage.getItem(this.storageKey);
    if (!raw) {
      return null;
    }

    try {
      return JSON.parse(raw) as AuthSession;
    } catch {
      localStorage.removeItem(this.storageKey);
      return null;
    }
  }
}
