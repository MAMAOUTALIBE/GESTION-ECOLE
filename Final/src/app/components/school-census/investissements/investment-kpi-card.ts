import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
  output,
} from '@angular/core';

import {
  InvestmentApiService,
  PriorityCategory,
} from '../shared/investment-api.service';

/**
 * Module 3C UI — Card KPI réutilisable du dashboard investissements.
 *
 * Présentationnel : reçoit un libellé, une catégorie, et un nombre
 * d'écoles. Couleur dérivée de la catégorie (mapping aligné sur
 * `InvestmentApiService.categoryClass`).
 *
 * Click → émet la catégorie pour permettre à la page parente de filtrer
 * la table par cette catégorie. Sémantique bouton (role + tabindex)
 * pour rester accessible clavier.
 */
@Component({
  selector: 'app-investment-kpi-card',
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './investment-kpi-card.html',
  styleUrl: './investment-kpi-card.scss',
})
export class InvestmentKpiCard {
  /** Libellé court de la KPI (ex. "Très haute priorité"). */
  title = input.required<string>();
  /** Nombre d'écoles dans cette catégorie. */
  value = input.required<number>();
  /** Catégorie associée — drive la couleur et l'émission au click. */
  category = input.required<PriorityCategory>();
  /** Sous-titre (ex. "≥ 70 points · action immédiate"). */
  subtitle = input<string>('');
  /** Icône Remixicon. */
  icon = input<string>('ri-medal-line');
  /** Activé quand la catégorie est sélectionnée (ring autour de la card). */
  selected = input<boolean>(false);

  /** Émet la catégorie au click — la page parente filtre la table. */
  readonly select = output<PriorityCategory>();

  readonly categoryClass = computed<string>(() =>
    InvestmentApiService.categoryClass(this.category()),
  );

  onSelect(): void {
    this.select.emit(this.category());
  }
}
