import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
} from '@angular/core';

import {
  ImpactReport,
  SimulatorApiService,
} from '../shared/simulator-api.service';

/**
 * Module 3B UI — Carte d'indicateurs d'impact du scénario what-if.
 *
 * Présentationnel : reçoit un ImpactReport (ou null tant qu'aucun calcul) et
 * affiche 4 KPI cards :
 *   - Couverture       : nb d'écoles avant → après, delta %
 *   - Saturation       : saturation moyenne + nb écoles critiques
 *   - Distance         : distance moyenne école-élève (km)
 *   - Redistribution   : nb d'élèves redistribués (CLOSE/MERGE)
 *
 * Code couleur pour les deltas :
 *   - vert : amélioration métier (couverture +, saturation -, distance -)
 *   - rouge : régression
 *   - gris : delta nul ou indisponible (null)
 */
type Direction = 'up' | 'down' | 'neutral';

interface KpiBlock {
  title: string;
  beforeLabel: string;
  afterLabel: string;
  deltaLabel: string;
  direction: Direction;
  /** vrai = amélioration, faux = régression, null = neutre / indispo. */
  improvement: boolean | null;
}

@Component({
  selector: 'app-impact-report',
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './impact-report.html',
  styleUrl: './impact-report.scss',
})
export class ImpactReportComponent {
  report = input<ImpactReport | null>(null);

  readonly hasReport = computed<boolean>(() => this.report() !== null);

  /** Coverage : up = + d'écoles = mieux. */
  readonly coverageBlock = computed<KpiBlock>(() => {
    const r = this.report();
    if (!r) return this.emptyBlock('Couverture');
    const before = r.coverage.beforeCount;
    const after = r.coverage.afterCount;
    const deltaPct = SimulatorApiService.toNumber(r.coverage.deltaPct) ?? 0;
    const direction = this.directionOf(after - before);
    return {
      title: 'Couverture',
      beforeLabel: `${before} écoles`,
      afterLabel: `${after} écoles`,
      deltaLabel: `${this.signed(deltaPct)} %`,
      direction,
      improvement:
        direction === 'neutral' ? null : direction === 'up',
    };
  });

  /** Saturation : on inverse — down = mieux (moins saturé). */
  readonly saturationBlock = computed<KpiBlock>(() => {
    const r = this.report();
    if (!r) return this.emptyBlock('Saturation');
    const before = SimulatorApiService.toNumber(r.saturation.beforeAvg);
    const after = SimulatorApiService.toNumber(r.saturation.afterAvg);
    if (before === null || after === null) {
      return this.emptyBlock('Saturation', {
        beforeLabel: '—',
        afterLabel: '—',
        critical: r.saturation.criticalSchoolsBefore,
        criticalAfter: r.saturation.criticalSchoolsAfter,
      });
    }
    const delta = after - before;
    const direction = this.directionOf(delta);
    // Mieux = saturation qui baisse → improvement quand direction = down.
    return {
      title: 'Saturation',
      beforeLabel: `${this.formatRatio(before)} (${r.saturation.criticalSchoolsBefore} critiques)`,
      afterLabel: `${this.formatRatio(after)} (${r.saturation.criticalSchoolsAfter} critiques)`,
      deltaLabel: `${this.signedRatio(delta)}`,
      direction,
      improvement:
        direction === 'neutral' ? null : direction === 'down',
    };
  });

  /** Distance moyenne : on inverse — down = mieux (élèves plus proches). */
  readonly distanceBlock = computed<KpiBlock>(() => {
    const r = this.report();
    if (!r) return this.emptyBlock('Distance moyenne');
    const before = SimulatorApiService.toNumber(r.distance.beforeKmMean);
    const after = SimulatorApiService.toNumber(r.distance.afterKmMean);
    const delta = SimulatorApiService.toNumber(r.distance.deltaKm);
    if (before === null || after === null) {
      return this.emptyBlock('Distance moyenne');
    }
    const direction = this.directionOf(delta ?? after - before);
    return {
      title: 'Distance moyenne',
      beforeLabel: `${this.formatRatio(before)} km`,
      afterLabel: `${this.formatRatio(after)} km`,
      deltaLabel: `${this.signedRatio(delta ?? after - before)} km`,
      direction,
      improvement:
        direction === 'neutral' ? null : direction === 'down',
    };
  });

  /** Redistribution : la valeur est neutre (informationnelle). */
  readonly redistributionBlock = computed<KpiBlock>(() => {
    const r = this.report();
    if (!r) return this.emptyBlock('Élèves redistribués');
    const n = r.redistributedStudents;
    return {
      title: 'Élèves redistribués',
      beforeLabel: '—',
      afterLabel: `${n.toLocaleString('fr-FR')} élèves`,
      deltaLabel: n > 0 ? `+${n.toLocaleString('fr-FR')}` : '0',
      direction: 'neutral',
      improvement: null,
    };
  });

  /** Classe CSS pour la flèche/delta selon improvement. */
  deltaClass(block: KpiBlock): string {
    if (block.improvement === null) return 'impact-delta impact-delta--neutral';
    return block.improvement
      ? 'impact-delta impact-delta--good'
      : 'impact-delta impact-delta--bad';
  }

  arrowFor(block: KpiBlock): string {
    switch (block.direction) {
      case 'up':
        return '▲';
      case 'down':
        return '▼';
      default:
        return '–';
    }
  }

  private emptyBlock(
    title: string,
    extra?: {
      beforeLabel?: string;
      afterLabel?: string;
      critical?: number;
      criticalAfter?: number;
    },
  ): KpiBlock {
    return {
      title,
      beforeLabel: extra?.beforeLabel ?? '—',
      afterLabel: extra?.afterLabel ?? '—',
      deltaLabel: '—',
      direction: 'neutral',
      improvement: null,
    };
  }

  private directionOf(delta: number): Direction {
    if (!Number.isFinite(delta) || delta === 0) return 'neutral';
    return delta > 0 ? 'up' : 'down';
  }

  private formatRatio(n: number): string {
    return n.toFixed(2);
  }

  private signed(n: number): string {
    if (!Number.isFinite(n) || n === 0) return '0';
    const sign = n > 0 ? '+' : '';
    return `${sign}${n.toFixed(1)}`;
  }

  private signedRatio(n: number): string {
    if (!Number.isFinite(n) || n === 0) return '0';
    const sign = n > 0 ? '+' : '';
    return `${sign}${n.toFixed(2)}`;
  }
}
