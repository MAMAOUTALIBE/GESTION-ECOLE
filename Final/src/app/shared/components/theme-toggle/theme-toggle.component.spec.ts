import { ComponentFixture, TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { ThemeService } from '../../theme/theme.service';
import { ThemeToggleComponent } from './theme-toggle.component';

describe('ThemeToggleComponent', () => {
  let fixture: ComponentFixture<ThemeToggleComponent>;
  let service: ThemeService;

  beforeEach(() => {
    try {
      window.localStorage.removeItem('ge.theme');
    } catch {
      // ignore
    }
    document.documentElement.removeAttribute('data-theme');
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [ThemeToggleComponent],
    });
    fixture = TestBed.createComponent(ThemeToggleComponent);
    service = TestBed.inject(ThemeService);
  });

  it('creates the component', () => {
    expect(fixture.componentInstance).toBeTruthy();
  });

  it('renders an accessible button with an aria-label', () => {
    fixture.detectChanges();
    const btn = fixture.nativeElement.querySelector('button.ge-theme-toggle');
    expect(btn).not.toBeNull();
    expect(btn.getAttribute('aria-label')).toMatch(/Thème/);
  });

  it('cycles the theme when the button is clicked', () => {
    fixture.detectChanges();
    const initial = service.mode();
    const btn: HTMLButtonElement = fixture.nativeElement.querySelector('button.ge-theme-toggle');
    btn.click();
    fixture.detectChanges();
    expect(service.mode()).not.toBe(initial);
  });
});
