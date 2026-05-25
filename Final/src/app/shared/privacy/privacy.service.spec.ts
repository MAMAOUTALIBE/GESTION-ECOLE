import { TestBed } from '@angular/core/testing';
import { describe, expect, it, beforeEach } from 'vitest';

import { AuthService, AuthSession, AuthUser, UserRole } from '../services/auth.service';
import { PrivacyService } from './privacy.service';

/**
 * Helper : construit un AuthService minimal pour tester PrivacyService sans
 * faire de vraie requête HTTP. On expose un objet conforme au shape utilisé
 * par PrivacyService (currentUser uniquement).
 */
function makeAuthStub(user: AuthUser | null): Partial<AuthService> {
  const session: AuthSession | null = user ? { accessToken: 'tok', user } : null;
  return {
    get currentUser() {
      return session?.user ?? null;
    },
  } as Partial<AuthService>;
}

function makeUser(overrides: Partial<AuthUser>): AuthUser {
  return {
    id: overrides.id ?? 'u-1',
    email: overrides.email ?? 'user@example.org',
    fullName: overrides.fullName ?? 'Test User',
    role: overrides.role ?? 'CENSUS_AGENT',
    region: overrides.region ?? null,
    prefecture: overrides.prefecture ?? null,
    subPrefecture: overrides.subPrefecture ?? null,
    school: overrides.school ?? null,
  } as AuthUser;
}

function setup(user: AuthUser | null): PrivacyService {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [{ provide: AuthService, useValue: makeAuthStub(user) }],
  });
  return TestBed.inject(PrivacyService);
}

describe('PrivacyService', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
  });

  it('test_national_admin_sees_full_name', () => {
    const service = setup(makeUser({ role: 'NATIONAL_ADMIN' as UserRole }));
    const full = service.displayName(
      { firstName: 'Aïssatou', lastName: 'Diallo' },
      { schoolId: 'sch-99', regionId: 'reg-99' },
    );
    expect(service.canSeeFullName({ schoolId: 'sch-99' })).toBe(true);
    expect(full).toBe('Aïssatou Diallo');
  });

  it('test_teacher_sees_full_name_for_own_school', () => {
    const service = setup(
      makeUser({
        role: 'TEACHER',
        school: { id: 'sch-1', name: 'EP Kaloum', code: 'EPK' },
      }),
    );
    const full = service.displayName(
      { firstName: 'Mariama', lastName: 'Bah' },
      { schoolId: 'sch-1', regionId: 'reg-1' },
    );
    expect(service.canSeeFullName({ schoolId: 'sch-1' })).toBe(true);
    expect(full).toBe('Mariama Bah');
  });

  it('test_teacher_sees_initials_for_other_school', () => {
    const service = setup(
      makeUser({
        role: 'TEACHER',
        school: { id: 'sch-1', name: 'EP Kaloum', code: 'EPK' },
      }),
    );
    const masked = service.displayName(
      { firstName: 'Mariama', lastName: 'Bah' },
      { schoolId: 'sch-2', regionId: 'reg-1' },
    );
    expect(service.canSeeFullName({ schoolId: 'sch-2' })).toBe(false);
    expect(masked).toBe('M. B.');
  });

  it('test_initials_first_letter_dot_first_letter_dot', () => {
    const service = setup(null);
    expect(service.initials('Aïssatou', 'Diallo')).toBe('A. D.');
    expect(service.initials('mamadou', 'sow')).toBe('M. S.');
    expect(service.initials('', 'Diallo')).toBe('D.');
    expect(service.initials('Aïssatou', '')).toBe('A.');
    expect(service.initials('', '')).toBe('');
  });

  it('test_inspector_sees_full_name_in_region_scope', () => {
    const service = setup(
      makeUser({
        role: 'INSPECTOR',
        region: { id: 'reg-1', name: 'Conakry', code: 'CKY' },
      }),
    );
    expect(service.canSeeFullName({ schoolId: 'sch-1', regionId: 'reg-1' })).toBe(true);
    expect(service.canSeeFullName({ schoolId: 'sch-9', regionId: 'reg-9' })).toBe(false);

    const inScope = service.displayName(
      { firstName: 'Fatoumata', lastName: 'Camara' },
      { regionId: 'reg-1' },
    );
    expect(inScope).toBe('Fatoumata Camara');
  });

  it('test_displayName_handles_missing_target_as_initials', () => {
    // School-scope role (TEACHER) sans target → on dégrade aux initiales,
    // car on ne peut pas prouver le matching d'école.
    const service = setup(
      makeUser({
        role: 'TEACHER',
        school: { id: 'sch-1', name: 'EP Kaloum', code: 'EPK' },
      }),
    );
    const masked = service.displayName({ firstName: 'Sékou', lastName: 'Touré' });
    expect(masked).toBe('S. T.');
    expect(service.canSeeFullName(undefined)).toBe(false);
    expect(service.canSeeFullName(null)).toBe(false);
  });

  it('test_no_user_returns_initials_and_redaction_flag', () => {
    const service = setup(null);
    expect(service.canSeeFullName({ schoolId: 'sch-1' })).toBe(false);
    expect(service.displayName({ firstName: 'Aïssatou', lastName: 'Diallo' })).toBe('A. D.');
    expect(service.hasAnyRedaction()).toBe(true);
  });

  it('test_hasAnyRedaction_false_for_central_admin', () => {
    const service = setup(makeUser({ role: 'NATIONAL_ADMIN' }));
    expect(service.hasAnyRedaction()).toBe(false);
  });

  it('test_displayName_empty_person_returns_empty_string', () => {
    const service = setup(makeUser({ role: 'NATIONAL_ADMIN' }));
    expect(service.displayName(null)).toBe('');
    expect(service.displayName({ firstName: '', lastName: '' })).toBe('');
  });
});
