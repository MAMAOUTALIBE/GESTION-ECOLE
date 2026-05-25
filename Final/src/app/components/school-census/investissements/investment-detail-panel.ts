import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  input,
  output,
} from '@angular/core';
import { Router } from '@angular/router';
import { ApexOptions } from 'ng-apexcharts';

import { SpkApexcharts } from '../../../@spk/charts/spk-apexcharts/spk-apexcharts';
import {
  AccessibilityBreakdown,
  EquityBreakdown,
  InfrastructureBreakdown,
  InvestmentApiService,
  InvestmentScoreRead,
  SaturationBreakdown,
  ScoreBreakdown,
} from '../shared/investment-api.service';

/**
 * Module 3C UI — Panneau de détail (slide-in droite) d'un score d'école.
 *
 * Affiche :
 *  - en-tête : nom école, région, score total badge coloré,
 *  - radar chart ApexCharts (4 axes 0..100 normalisés),
 *  - breakdown texte par dimension (eau, électricité, latrines, GPI, …),
 *  - bouton "Voir école dans la carte" (navigation queryParam schoolId),
 *  - bouton "Fermer" (emit close).
 *
 * Composant purement présentationnel. La récupération du détail
 * `breakdownJson` est faite par la page parente (qui peut soit utiliser
 * la ligne déjà reçue dans la liste — qui contient `breakdownJson` —
 * soit appeler `getSchoolPriority` pour rafraîchir).
 */
@Component({
  selector: 'app-investment-detail-panel',
  imports: [CommonModule, SpkApexcharts],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './investment-detail-panel.html',
  styleUrl: './investment-detail-panel.scss',
})
export class InvestmentDetailPanel {
  /** Score actif. `null` masque le panneau. */
  score = input<InvestmentScoreRead | null>(null);

  /** Émet quand l'utilisateur clique sur "Fermer". */
  readonly close = output<void>();

  private router = inject(Router);

  readonly visible = computed<boolean>(() => this.score() !== null);

  /** Classe Bootstrap dérivée de la catégorie de priorité. */
  readonly badgeClass = computed<string>(() => {
    const s = this.score();
    if (!s) return 'bg-secondary-transparent text-secondary';
    return InvestmentApiService.categoryClass(s.priorityCategory);
  });

  readonly categoryLabel = computed<string>(() => {
    const s = this.score();
    if (!s) return '';
    return InvestmentApiService.categoryLabel(s.priorityCategory);
  });

  // ---- Radar chart ApexCharts ----
  readonly radarOptions = computed<ApexOptions>(() => {
    const s = this.score();
    // Normalise chaque dimension sur 100 pour qu'elles soient lisibles
    // côte à côte sur le radar (les pondérations natives diffèrent : 35,
    // 25, 25, 20).
    const infra = s ? (s.infrastructureScore / 35) * 100 : 0;
    const sat = s ? (s.saturationScore / 25) * 100 : 0;
    const eq = s ? (s.equityScore / 25) * 100 : 0;
    const acc = s ? (s.accessibilityScore / 20) * 100 : 0;
    return {
      series: [
        {
          name: 'Score normalisé (/100)',
          data: [
            Number(infra.toFixed(0)),
            Number(sat.toFixed(0)),
            Number(eq.toFixed(0)),
            Number(acc.toFixed(0)),
          ],
        },
      ],
      chart: {
        type: 'radar',
        height: 280,
        toolbar: { show: false },
        fontFamily: 'inherit',
      },
      labels: ['Infrastructure', 'Saturation', 'Équité', 'Accessibilité'],
      colors: ['#1a3a6e'],
      fill: { opacity: 0.35 },
      markers: { size: 4 },
      yaxis: {
        min: 0,
        max: 100,
        tickAmount: 4,
      },
      stroke: { width: 2 },
      tooltip: {
        y: { formatter: (val: number) => `${val} / 100` },
      },
    };
  });

  // ---- Breakdown helpers (extractions typées) ----
  readonly breakdown = computed<ScoreBreakdown>(() => {
    return (this.score()?.breakdownJson as ScoreBreakdown) ?? {};
  });

  readonly infra = computed<InfrastructureBreakdown>(
    () => (this.breakdown().infrastructure as InfrastructureBreakdown) ?? {},
  );
  readonly sat = computed<SaturationBreakdown>(
    () => (this.breakdown().saturation as SaturationBreakdown) ?? {},
  );
  readonly eq = computed<EquityBreakdown>(
    () => (this.breakdown().equity as EquityBreakdown) ?? {},
  );
  readonly acc = computed<AccessibilityBreakdown>(
    () => (this.breakdown().accessibility as AccessibilityBreakdown) ?? {},
  );

  // ---- Actions ----
  onClose(): void {
    this.close.emit();
  }

  /** Navigation vers la carte scolaire avec le schoolId surligné. */
  onViewOnMap(): void {
    const s = this.score();
    if (!s) return;
    this.router.navigate(['/school-census/map'], {
      queryParams: { schoolId: s.schoolId },
    });
  }

  // ---- Helpers d'affichage ----
  yesNo(value: boolean | undefined | null): string {
    if (value === undefined || value === null) return '—';
    return value ? 'Oui' : 'Non';
  }

  /** Présence inverse (true = "manquant" → "Non" pour l'utilisateur). */
  hasResource(missing: boolean | undefined | null): string {
    if (missing === undefined || missing === null) return '—';
    return missing ? 'Non' : 'Oui';
  }

  formatRatio(value: number | null | undefined): string {
    if (value === undefined || value === null) return '—';
    return value.toFixed(2);
  }

  formatGpi(value: number | null | undefined): string {
    if (value === undefined || value === null) return '—';
    return value.toFixed(3);
  }

  formatDistance(value: number | null | undefined): string {
    if (value === undefined || value === null) return '—';
    return `${value.toFixed(2)} km`;
  }

  /** Recommandations textuelles dérivées du breakdown (heuristique simple). */
  readonly recommendations = computed<string[]>(() => {
    const reco: string[] = [];
    const i = this.infra();
    if (i.missingWater) reco.push("Installer une source d'eau potable.");
    if (i.missingElectricity) reco.push('Électrifier le bâtiment.');
    if (i.missingToilets) reco.push('Construire des latrines filles + garçons.');
    if (i.classroomsRatioCritical)
      reco.push('Réhabiliter les salles inutilisables.');
    if (i.buildingCondition === 'DANGEROUS' || i.buildingCondition === 'POOR')
      reco.push('Reconstruire ou rénover en urgence le bâtiment principal.');
    const s = this.sat();
    if (s.severity === 'CRITICAL')
      reco.push('Étendre la capacité d\'accueil (nouvelles salles).');
    const e = this.eq();
    if (e.severity === 'CRITICAL')
      reco.push('Plan de scolarisation des filles (sensibilisation, cantine).');
    const a = this.acc();
    if (a.zoneType === 'RURAL' && (a.distanceBonus ?? 0) > 0)
      reco.push('Étudier un ramassage scolaire ou un internat de proximité.');
    if (reco.length === 0)
      reco.push(
        'Pas d\'action immédiate recommandée — surveiller au prochain recensement.',
      );
    return reco;
  });
}
