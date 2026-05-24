import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { EquiteKpiCard } from './equite-kpi-card';

describe('EquiteKpiCard', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [EquiteKpiCard],
    });
  });

  it('rend un titre et une valeur passés en input', () => {
    const fixture = TestBed.createComponent(EquiteKpiCard);
    fixture.componentRef.setInput('title', 'GPI national');
    fixture.componentRef.setInput('value', '0.94');
    fixture.detectChanges();
    const text: string = fixture.nativeElement.textContent ?? '';
    expect(text).toContain('GPI national');
    expect(text).toContain('0.94');
  });

  it('applique la classe danger sur sévérité CRITICAL_GIRLS', () => {
    const fixture = TestBed.createComponent(EquiteKpiCard);
    fixture.componentRef.setInput('title', 'GPI');
    fixture.componentRef.setInput('value', '0.7');
    fixture.componentRef.setInput('severity', 'CRITICAL_GIRLS');
    fixture.componentRef.setInput('badgeLabel', 'Critique filles');
    fixture.detectChanges();
    expect(fixture.componentInstance.severityClass()).toContain('text-danger');
    expect(fixture.nativeElement.querySelector('.badge')?.textContent).toContain(
      'Critique filles',
    );
  });

  it('formatte deltaPrevYear avec signe et 2 décimales', () => {
    const fixture = TestBed.createComponent(EquiteKpiCard);
    fixture.componentRef.setInput('title', 'GPI');
    fixture.componentRef.setInput('value', '0.94');
    fixture.componentRef.setInput('deltaPrevYear', 0.0234);
    fixture.detectChanges();
    expect(fixture.componentInstance.deltaLabel()).toBe('+0.02');
    expect(fixture.componentInstance.deltaClass()).toBe('text-success');
  });

  it('renvoie une classe muted si delta nul ou non fini', () => {
    const fixture = TestBed.createComponent(EquiteKpiCard);
    fixture.componentRef.setInput('title', 'GPI');
    fixture.componentRef.setInput('value', '0.94');
    fixture.componentRef.setInput('deltaPrevYear', null);
    fixture.detectChanges();
    expect(fixture.componentInstance.deltaLabel()).toBe('');
    expect(fixture.componentInstance.deltaClass()).toBe('text-muted');
  });
});
