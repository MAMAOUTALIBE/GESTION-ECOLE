import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { ThemeService } from './theme.service';

describe('ThemeService', () => {
  beforeEach(() => {
    try {
      window.localStorage.removeItem('ge.theme');
    } catch {
      // ignore
    }
    document.documentElement.removeAttribute('data-theme');
    TestBed.resetTestingModule();
  });

  it('defaults to auto mode when no preference is stored', () => {
    const svc = TestBed.inject(ThemeService);
    expect(svc.mode()).toBe('auto');
  });

  it('persists the chosen theme into localStorage', () => {
    const svc = TestBed.inject(ThemeService);
    svc.setTheme('dark');
    expect(window.localStorage.getItem('ge.theme')).toBe('dark');
    expect(svc.mode()).toBe('dark');
  });

  it('applies data-theme="dark" on <html> when dark is selected', () => {
    const svc = TestBed.inject(ThemeService);
    svc.setTheme('dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });

  it('removes data-theme when light is selected', () => {
    const svc = TestBed.inject(ThemeService);
    svc.setTheme('dark');
    svc.setTheme('light');
    expect(document.documentElement.getAttribute('data-theme')).toBeNull();
  });

  it('cycles light -> dark -> auto -> light', () => {
    const svc = TestBed.inject(ThemeService);
    svc.setTheme('light');
    svc.cycle();
    expect(svc.mode()).toBe('dark');
    svc.cycle();
    expect(svc.mode()).toBe('auto');
    svc.cycle();
    expect(svc.mode()).toBe('light');
  });

  it('ignores unknown values', () => {
    const svc = TestBed.inject(ThemeService);
    svc.setTheme('light');
    // @ts-expect-error — on teste volontairement une valeur invalide.
    svc.setTheme('rainbow');
    expect(svc.mode()).toBe('light');
  });
});
