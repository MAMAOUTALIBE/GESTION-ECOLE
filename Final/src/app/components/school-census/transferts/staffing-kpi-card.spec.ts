import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { StaffingKpiCard } from './staffing-kpi-card';

describe('StaffingKpiCard', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [StaffingKpiCard],
    });
  });

  it('rend le titre et la valeur passés en input', () => {
    const fixture = TestBed.createComponent(StaffingKpiCard);
    fixture.componentRef.setInput('title', 'Écoles critiques');
    fixture.componentRef.setInput('value', 12);
    fixture.detectChanges();
    const text: string = fixture.nativeElement.textContent ?? '';
    expect(text).toContain('Écoles critiques');
    expect(text).toContain('12');
  });

  it('applique text-danger pour sévérité CRITICAL', () => {
    const fixture = TestBed.createComponent(StaffingKpiCard);
    fixture.componentRef.setInput('title', 'KPI');
    fixture.componentRef.setInput('value', 5);
    fixture.componentRef.setInput('severity', 'CRITICAL');
    fixture.componentRef.setInput('badgeLabel', 'Critique');
    fixture.detectChanges();
    expect(fixture.componentInstance.severityClass()).toContain('text-danger');
    const badge: HTMLElement | null = fixture.nativeElement.querySelector('.badge');
    expect(badge?.textContent).toContain('Critique');
  });

  it('applique text-warning pour status PENDING', () => {
    const fixture = TestBed.createComponent(StaffingKpiCard);
    fixture.componentRef.setInput('title', 'En attente');
    fixture.componentRef.setInput('value', 3);
    fixture.componentRef.setInput('status', 'PENDING');
    fixture.detectChanges();
    expect(fixture.componentInstance.severityClass()).toContain('text-warning');
  });

  it('applique text-success pour status EXECUTED', () => {
    const fixture = TestBed.createComponent(StaffingKpiCard);
    fixture.componentRef.setInput('title', 'Exécutées');
    fixture.componentRef.setInput('value', 8);
    fixture.componentRef.setInput('status', 'EXECUTED');
    fixture.detectChanges();
    expect(fixture.componentInstance.severityClass()).toContain('text-success');
  });
});
