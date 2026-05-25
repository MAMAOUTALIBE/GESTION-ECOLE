import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { ImpactReport } from '../shared/simulator-api.service';
import { ImpactReportComponent } from './impact-report';

describe('ImpactReportComponent', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [ImpactReportComponent],
    });
  });

  it('affiche des placeholders quand aucun rapport n\'est fourni', () => {
    const fixture = TestBed.createComponent(ImpactReportComponent);
    fixture.componentRef.setInput('report', null);
    fixture.detectChanges();

    expect(fixture.componentInstance.hasReport()).toBe(false);
    // Toutes les valeurs de delta tombent sur le placeholder neutre.
    const cov = fixture.componentInstance.coverageBlock();
    expect(cov.improvement).toBeNull();
    expect(cov.deltaLabel).toBe('—');
  });

  it('classe la couverture comme amélioration quand after > before', () => {
    const report: ImpactReport = {
      coverage: { beforeCount: 100, afterCount: 105, deltaPct: '5.0' },
      saturation: {
        beforeAvg: '1.10',
        afterAvg: '0.95',
        criticalSchoolsBefore: 4,
        criticalSchoolsAfter: 1,
      },
      distance: {
        beforeKmMean: '4.0',
        afterKmMean: '3.5',
        deltaKm: '-0.5',
      },
      redistributedStudents: 120,
    };
    const fixture = TestBed.createComponent(ImpactReportComponent);
    fixture.componentRef.setInput('report', report);
    fixture.detectChanges();

    const cov = fixture.componentInstance.coverageBlock();
    expect(cov.improvement).toBe(true);
    expect(cov.direction).toBe('up');
    expect(cov.deltaLabel).toContain('+5');

    // Saturation : baisse = amélioration.
    const sat = fixture.componentInstance.saturationBlock();
    expect(sat.improvement).toBe(true);
    expect(sat.direction).toBe('down');

    // Distance : baisse = amélioration.
    const dist = fixture.componentInstance.distanceBlock();
    expect(dist.improvement).toBe(true);
    expect(dist.direction).toBe('down');
    expect(dist.deltaLabel).toContain('km');

    // Redistribution : neutre, affiche bien le nombre.
    const red = fixture.componentInstance.redistributionBlock();
    expect(red.improvement).toBeNull();
    expect(red.afterLabel).toContain('120');

    // Présence visuelle : 4 cards rendues, avec le mot "Couverture".
    const html: string = fixture.nativeElement.textContent ?? '';
    expect(html).toContain('Couverture');
    expect(html).toContain('Saturation');
    expect(html).toContain('Distance');
    expect(html).toContain('Élèves redistribués');
  });
});
