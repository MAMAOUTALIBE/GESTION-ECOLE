import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { InvestmentKpiCard } from './investment-kpi-card';

describe('InvestmentKpiCard', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [InvestmentKpiCard],
    });
  });

  it('rend le titre, la valeur et la classe associée à la catégorie TRES_HAUTE', () => {
    const fixture = TestBed.createComponent(InvestmentKpiCard);
    fixture.componentRef.setInput('title', 'Très haute priorité');
    fixture.componentRef.setInput('value', 8);
    fixture.componentRef.setInput('category', 'TRES_HAUTE');
    fixture.detectChanges();
    const text: string = fixture.nativeElement.textContent ?? '';
    expect(text).toContain('Très haute priorité');
    expect(text).toContain('8');
    expect(fixture.componentInstance.categoryClass()).toContain('text-danger');
  });

  it('émet la catégorie au click et marque la card sélectionnée via aria-pressed', () => {
    const fixture = TestBed.createComponent(InvestmentKpiCard);
    fixture.componentRef.setInput('title', 'Haute priorité');
    fixture.componentRef.setInput('value', 3);
    fixture.componentRef.setInput('category', 'HAUTE');
    fixture.componentRef.setInput('selected', true);
    let received: string | null = null;
    fixture.componentInstance.select.subscribe((c) => (received = c));
    fixture.detectChanges();
    const card: HTMLElement = fixture.nativeElement.querySelector(
      '.investment-kpi',
    ) as HTMLElement;
    expect(card).not.toBeNull();
    expect(card.getAttribute('aria-pressed')).toBe('true');
    card.click();
    expect(received).toBe('HAUTE');
  });
});
