import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { GpiSeverity } from '../shared/enrollment-api.service';

/**
 * Card KPI réutilisable pour le dashboard Équité.
 * Affiche : titre, valeur, badge sévérité GPI, comparaison vs N-1 (optionnelle).
 *
 * Le composant est volontairement "présentationnel" (signals d'entrée uniquement)
 * pour rester testable, sans dépendances HTTP.
 */
@Component({
  selector: 'app-equite-kpi-card',
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './equite-kpi-card.html',
  styleUrl: './equite-kpi-card.scss',
})
export class EquiteKpiCard {
  /** Titre court de la KPI (ex. "GPI national"). */
  title = input.required<string>();

  /** Valeur principale affichée (déjà formatée). */
  value = input.required<string>();

  /** Sous-titre / contexte (effectifs, période…). */
  subtitle = input<string>('');

  /** Sévérité — drive la couleur du badge. */
  severity = input<GpiSeverity | null>(null);

  /** Variation par rapport à l'année N-1 (en valeur absolue). */
  deltaPrevYear = input<number | null>(null);

  /** Libellé du badge (ex. "Critique filles"). */
  badgeLabel = input<string>('');

  /** Icône Remixicon affichée à gauche. */
  icon = input<string>('ri-bar-chart-line');

  /**
   * Renvoie une classe CSS Bootstrap *-transparent / text-* selon la sévérité.
   * Choix calqué sur les autres dashboards (cohérence design Spruko).
   */
  readonly severityClass = computed<string>(() => {
    const sev = this.severity();
    switch (sev) {
      case 'CRITICAL_GIRLS':
        return 'bg-danger-transparent text-danger';
      case 'WARNING_GIRLS':
        return 'bg-warning-transparent text-warning';
      case 'WARNING_BOYS':
        return 'bg-info-transparent text-info';
      case 'NORMAL':
        return 'bg-success-transparent text-success';
      default:
        return 'bg-secondary-transparent text-secondary';
    }
  });

  /** "+0.04" / "-0.12" / "" — formatté avec 2 décimales et signe. */
  readonly deltaLabel = computed<string>(() => {
    const d = this.deltaPrevYear();
    if (d === null || d === undefined || !Number.isFinite(d)) {
      return '';
    }
    const sign = d > 0 ? '+' : '';
    return `${sign}${d.toFixed(2)}`;
  });

  readonly deltaClass = computed<string>(() => {
    const d = this.deltaPrevYear();
    if (d === null || d === undefined || !Number.isFinite(d)) {
      return 'text-muted';
    }
    if (Math.abs(d) < 0.01) return 'text-muted';
    return d > 0 ? 'text-success' : 'text-danger';
  });
}
