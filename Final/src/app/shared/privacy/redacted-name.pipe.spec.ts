import { TestBed } from '@angular/core/testing';
import { describe, expect, it, beforeEach } from 'vitest';

import { AuthService, AuthUser } from '../services/auth.service';
import { RedactedNamePipe } from './redacted-name.pipe';

function makeAuthStub(user: AuthUser | null): Partial<AuthService> {
  return {
    get currentUser() {
      return user;
    },
  } as Partial<AuthService>;
}

function makeUser(overrides: Partial<AuthUser>): AuthUser {
  return {
    id: 'u-1',
    email: 'user@example.org',
    fullName: 'Test User',
    role: 'CENSUS_AGENT',
    region: null,
    prefecture: null,
    subPrefecture: null,
    school: null,
    ...overrides,
  } as AuthUser;
}

function setupPipe(user: AuthUser | null): RedactedNamePipe {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [
      { provide: AuthService, useValue: makeAuthStub(user) },
      RedactedNamePipe,
    ],
  });
  return TestBed.inject(RedactedNamePipe);
}

describe('RedactedNamePipe', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
  });

  it('returns full name for a NATIONAL_ADMIN', () => {
    const pipe = setupPipe(makeUser({ role: 'NATIONAL_ADMIN' }));
    const out = pipe.transform({ firstName: 'Aïssatou', lastName: 'Diallo' }, { schoolId: 'sch-1' });
    expect(out).toBe('Aïssatou Diallo');
  });

  it('returns initials for a TEACHER on another school', () => {
    const pipe = setupPipe(
      makeUser({
        role: 'TEACHER',
        school: { id: 'sch-1', name: 'EP', code: 'EP' },
      }),
    );
    const out = pipe.transform(
      { firstName: 'Mariama', lastName: 'Bah' },
      { schoolId: 'sch-9' },
    );
    expect(out).toBe('M. B.');
  });

  it('returns empty string when person is null', () => {
    const pipe = setupPipe(makeUser({ role: 'NATIONAL_ADMIN' }));
    expect(pipe.transform(null)).toBe('');
    expect(pipe.transform(undefined)).toBe('');
  });

  it('returns initials when there is no current user', () => {
    const pipe = setupPipe(null);
    const out = pipe.transform({ firstName: 'Fatou', lastName: 'Camara' }, { schoolId: 'sch-1' });
    expect(out).toBe('F. C.');
  });
});
