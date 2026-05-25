import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it } from 'vitest';

import { InvestmentScoreRead } from '../shared/investment-api.service';
import { InvestmentDetailPanel } from './investment-detail-panel';

function buildScore(
  partial: Partial<InvestmentScoreRead> = {},
): InvestmentScoreRead {
  return {
    schoolId: 'SCH-1',
    schoolName: 'École Test',
    regionName: 'Conakry',
    baseSchoolYearId: 'SY-2026',
    infrastructureScore: 30,
    saturationScore: 20,
    equityScore: 15,
    accessibilityScore: 15,
    totalScore: 80,
    priorityCategory: 'TRES_HAUTE',
    computedAt: '2026-05-25T10:00:00Z',
    breakdownJson: {
      infrastructure: {
        missingWater: true,
        missingElectricity: false,
        missingToilets: true,
        buildingCondition: 'POOR',
        classroomsRatio: 0.4,
        classroomsRatioCritical: true,
        missingInternet: true,
        score: 30,
      },
      saturation: { severity: 'CRITICAL', score: 25 },
      equity: { gpi: 0.7, severity: 'CRITICAL', score: 25 },
      accessibility: {
        zoneType: 'RURAL',
        zonePoints: 15,
        avgDistanceKm: 5.2,
        distanceBonus: 5,
        score: 20,
      },
    },
    ...partial,
  };
}

describe('InvestmentDetailPanel', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [InvestmentDetailPanel],
      providers: [provideRouter([])],
    });
  });

  it('génère les options du radar (4 axes normalisés sur 100)', () => {
    const fixture = TestBed.createComponent(InvestmentDetailPanel);
    fixture.componentRef.setInput('score', buildScore());
    fixture.detectChanges();
    const opts = fixture.componentInstance.radarOptions();
    expect(opts.chart?.type).toBe('radar');
    expect(opts.labels).toEqual([
      'Infrastructure',
      'Saturation',
      'Équité',
      'Accessibilité',
    ]);
    const series = opts.series as Array<{ name: string; data: number[] }>;
    expect(series.length).toBe(1);
    expect(series[0].data.length).toBe(4);
    // Infrastructure : 30 / 35 * 100 = 86 (arrondi)
    expect(series[0].data[0]).toBeGreaterThanOrEqual(85);
    expect(series[0].data[0]).toBeLessThanOrEqual(87);
    // Saturation : 20 / 25 * 100 = 80
    expect(series[0].data[1]).toBe(80);
    // Équité : 15 / 25 * 100 = 60
    expect(series[0].data[2]).toBe(60);
    // Accessibilité : 15 / 20 * 100 = 75
    expect(series[0].data[3]).toBe(75);
  });

  it('affiche le breakdown texte avec les libellés métier (eau, électricité, latrines, GPI, zone)', () => {
    const fixture = TestBed.createComponent(InvestmentDetailPanel);
    fixture.componentRef.setInput('score', buildScore());
    fixture.detectChanges();
    const html: string = fixture.nativeElement.textContent ?? '';
    expect(html).toContain('École Test');
    expect(html).toContain('Conakry');
    expect(html).toContain('Eau potable');
    expect(html).toContain('Électricité');
    expect(html).toContain('Latrines');
    expect(html).toContain('GPI école');
    expect(html).toContain('0.700');
    expect(html).toContain('Zone');
    expect(html).toContain('RURAL');
    // Recommandations heuristiques
    expect(html).toContain("Installer une source d'eau potable");
    expect(html).toContain('Construire des latrines');
  });

  it("émet l'événement close au clic sur le bouton Fermer", () => {
    const fixture = TestBed.createComponent(InvestmentDetailPanel);
    fixture.componentRef.setInput('score', buildScore());
    let closed = 0;
    fixture.componentInstance.close.subscribe(() => closed++);
    fixture.detectChanges();
    const btn: HTMLElement | null = fixture.nativeElement.querySelector(
      '[data-testid="close-btn"]',
    );
    expect(btn).not.toBeNull();
    btn?.click();
    expect(closed).toBe(1);
  });

  it("masque le panneau (visible=false) quand le score est null", () => {
    const fixture = TestBed.createComponent(InvestmentDetailPanel);
    fixture.componentRef.setInput('score', null);
    fixture.detectChanges();
    expect(fixture.componentInstance.visible()).toBe(false);
    const aside: HTMLElement | null =
      fixture.nativeElement.querySelector('.investment-panel');
    expect(aside).toBeNull();
  });
});
