import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import {
  RecommendationStatus,
  StaffingSeverity,
} from '../shared/staffing-api.service';

/**
 * Module 2D UI — Card KPI réutilisable du dashboard transferts.
 *
 * Présentationnel : reçoit titre + valeur + sévérité (couleur). Pas
 * d'appel HTTP, pas d'état interne — facile à tester en pure unit-test.
 *
 * Mapping couleurs :
 *  - CRITICAL                : danger (rouge)
 *  - UNDER_STAFFED + PENDING : warning (jaune)
 *  - ADEQUATE + EXECUTED     : success (vert)
 *  - OVER_STAFFED + REVIEWED : info    (bleu)
 *  - ACCEPTED                : primary
 *  - REJECTED                : secondary
 */
@Component({
  selector: 'app-staffing-kpi-card',
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './staffing-kpi-card.html',
  styleUrl: './staffing-kpi-card.scss',
})
export class StaffingKpiCard {
  /** Titre court de la KPI. */
  title = input.required<string>();

  /** Valeur principale (formattée par l'appelant). */
  value = input.required<string | number>();

  /** Sous-titre / contexte (effectifs total, période…). */
  subtitle = input<string>('');

  /** Sévérité staffing — drive la couleur. */
  severity = input<StaffingSeverity | null>(null);

  /** Statut workflow — drive la couleur (alternative à severity). */
  status = input<RecommendationStatus | null>(null);

  /** Libellé du badge (ex. "Critique"). */
  badgeLabel = input<string>('');

  /** Icône Remixicon affichée à gauche. */
  icon = input<string>('ri-team-line');

  /** Classe Bootstrap pour l'icône et le badge. */
  readonly severityClass = computed<string>(() => {
    const sev = this.severity();
    if (sev) {
      switch (sev) {
        case 'CRITICAL':
          return 'bg-danger-transparent text-danger';
        case 'UNDER_STAFFED':
          return 'bg-warning-transparent text-warning';
        case 'ADEQUATE':
          return 'bg-success-transparent text-success';
        case 'OVER_STAFFED':
          return 'bg-info-transparent text-info';
      }
    }
    const st = this.status();
    if (st) {
      switch (st) {
        case 'PENDING':
          return 'bg-warning-transparent text-warning';
        case 'REVIEWED':
          return 'bg-info-transparent text-info';
        case 'ACCEPTED':
          return 'bg-primary-transparent text-primary';
        case 'REJECTED':
          return 'bg-secondary-transparent text-secondary';
        case 'EXECUTED':
          return 'bg-success-transparent text-success';
      }
    }
    return 'bg-secondary-transparent text-secondary';
  });
}
