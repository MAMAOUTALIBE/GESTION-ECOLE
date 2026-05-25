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
  InvestmentScoreRead,
  PriorityCategory,
} from '../shared/investment-api.service';

/**
 * Module 3C UI — Top N écoles classées par score total décroissant.
 *
 * Présentationnel : reçoit la liste de scores et la trie côté composant
 * (sans muter l'input). Click ligne → emit selectSchool.
 *
 * Colonnes :
 *   #   École   Région   Score (badge)   Catégorie   4 sparkbars
 *
 * Les sparkbars représentent les 4 dimensions (infrastructure 0..35,
 * saturation 0..25, équité 0..25, accessibilité 0..20). Largeur =
 * `score / max` en %, couleur dérivée de la dimension.
 */
interface InvestmentRow {
  rank: number;
  id: string;
  schoolId: string;
  schoolName: string;
  regionName: string;
  totalScore: number;
  category: PriorityCategory;
  infrastructure: number;
  saturation: number;
  equity: number;
  accessibility: number;
}

const CATEGORY_ORDER: Record<PriorityCategory, number> = {
  TRES_HAUTE: 4,
  HAUTE: 3,
  MOYENNE: 2,
  BASSE: 1,
};

const MAX_INFRA = 35;
const MAX_SAT = 25;
const MAX_EQUITY = 25;
const MAX_ACCESS = 20;

@Component({
  selector: 'app-investment-table',
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './investment-table.html',
  styleUrl: './investment-table.scss',
})
export class InvestmentTable {
  scores = input.required<InvestmentScoreRead[]>();
  selectedSchoolId = input<string | null>(null);
  /** Nombre max de lignes — par défaut 100 (top priorités). */
  limit = input<number>(100);

  readonly selectSchool = output<string>();

  readonly rows = computed<InvestmentRow[]>(() => {
    const items = (this.scores() ?? []).slice();
    items.sort((a, b) => {
      if (b.totalScore !== a.totalScore) return b.totalScore - a.totalScore;
      // Égalité de score : on remonte les catégories plus sévères.
      const ca = CATEGORY_ORDER[a.priorityCategory] ?? 0;
      const cb = CATEGORY_ORDER[b.priorityCategory] ?? 0;
      return cb - ca;
    });
    const limit = this.limit();
    return items.slice(0, limit).map((s, idx) => ({
      rank: idx + 1,
      id: s.id ?? s.schoolId,
      schoolId: s.schoolId,
      schoolName: s.schoolName ?? s.schoolId,
      regionName: s.regionName ?? '—',
      totalScore: s.totalScore,
      category: s.priorityCategory,
      infrastructure: s.infrastructureScore,
      saturation: s.saturationScore,
      equity: s.equityScore,
      accessibility: s.accessibilityScore,
    } satisfies InvestmentRow));
  });

  categoryClass(cat: PriorityCategory): string {
    return InvestmentApiService.categoryClass(cat);
  }

  categoryLabel(cat: PriorityCategory): string {
    return InvestmentApiService.categoryLabel(cat);
  }

  /** Largeur (%) du sparkbar pour une dimension donnée. */
  barWidth(score: number, max: number): number {
    if (max <= 0) return 0;
    const pct = (score / max) * 100;
    if (pct < 0) return 0;
    if (pct > 100) return 100;
    return pct;
  }

  /** Couleur Bootstrap d'une dimension (cohérent KPI cabinet). */
  dimensionColor(
    dim: 'infrastructure' | 'saturation' | 'equity' | 'accessibility',
  ): string {
    switch (dim) {
      case 'infrastructure':
        return 'bg-primary';
      case 'saturation':
        return 'bg-warning';
      case 'equity':
        return 'bg-danger';
      case 'accessibility':
        return 'bg-info';
    }
  }

  onSelect(row: InvestmentRow): void {
    this.selectSchool.emit(row.schoolId);
  }

  trackById(_idx: number, row: InvestmentRow): string {
    return row.id;
  }

  readonly maxInfra = MAX_INFRA;
  readonly maxSat = MAX_SAT;
  readonly maxEquity = MAX_EQUITY;
  readonly maxAccess = MAX_ACCESS;
}
