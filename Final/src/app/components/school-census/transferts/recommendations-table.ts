import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
  output,
  signal,
} from '@angular/core';

import {
  RecommendationStatus,
  StaffingApiService,
  TeacherTransferRecommendation,
} from '../shared/staffing-api.service';
import { School } from '../shared/school-census.models';

/**
 * Module 2D UI — Table des recommandations transferts + workflow de revue.
 *
 * Workflow :
 *   PENDING  → REVIEWED      ("Marquer revue")
 *   REVIEWED → ACCEPTED/REJECTED ("Accepter" / "Rejeter")
 *   ACCEPTED → EXECUTED      ("Marquer exécutée")
 *   EXECUTED/REJECTED        → terminal, plus d'action.
 *
 * Une seule modal de confirmation (signal `pendingAction`) avec un champ
 * `reviewNote` optionnel. Le composant émet `review` qui contient l'id +
 * la transition demandée — c'est la page qui appelle l'API.
 */
export interface PendingAction {
  recommendationId: string;
  targetStatus: Exclude<RecommendationStatus, 'PENDING'>;
  fromLabel: string;
  toLabel: string;
}

export interface ReviewActionEvent {
  recommendationId: string;
  targetStatus: Exclude<RecommendationStatus, 'PENDING'>;
  reviewNote: string | null;
}

interface RecommendationRow {
  id: string;
  fromName: string;
  fromRegion: string;
  toName: string;
  toRegion: string;
  transfersSuggested: number;
  priorityScore: number;
  status: RecommendationStatus;
}

@Component({
  selector: 'app-recommendations-table',
  imports: [CommonModule, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './recommendations-table.html',
  styleUrl: './recommendations-table.scss',
})
export class RecommendationsTable {
  recommendations = input.required<TeacherTransferRecommendation[]>();
  schools = input<School[]>([]);
  /** L'utilisateur peut-il appliquer les actions (REGIONAL_ADMIN+) ? */
  canReview = input<boolean>(false);

  readonly review = output<ReviewActionEvent>();

  readonly pendingAction = signal<PendingAction | null>(null);
  readonly reviewNote = signal<string>('');

  readonly rows = computed<RecommendationRow[]>(() => {
    const schools = new Map<string, School>();
    for (const s of this.schools() ?? []) {
      schools.set(s.id, s);
    }
    return (this.recommendations() ?? []).map((reco) => {
      const from = schools.get(reco.fromSchoolId);
      const to = schools.get(reco.toSchoolId);
      const score = StaffingApiService.toNumber(reco.priorityScore) ?? 0;
      return {
        id: reco.id,
        fromName: from?.name ?? reco.fromSchoolId,
        fromRegion: from?.region?.name ?? '—',
        toName: to?.name ?? reco.toSchoolId,
        toRegion: to?.region?.name ?? '—',
        transfersSuggested: reco.transfersSuggested,
        priorityScore: score,
        status: reco.status,
      } satisfies RecommendationRow;
    });
  });

  statusBadge(st: RecommendationStatus): string {
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

  statusLabel(st: RecommendationStatus): string {
    switch (st) {
      case 'PENDING':
        return 'En attente';
      case 'REVIEWED':
        return 'En revue';
      case 'ACCEPTED':
        return 'Acceptée';
      case 'REJECTED':
        return 'Rejetée';
      case 'EXECUTED':
        return 'Exécutée';
    }
  }

  /**
   * Pour un statut donné, renvoie la liste des transitions autorisées.
   * Utilisé par le template pour générer 0..N boutons d'action.
   */
  allowedTransitions(
    st: RecommendationStatus,
  ): Array<{ status: Exclude<RecommendationStatus, 'PENDING'>; label: string; cls: string }> {
    switch (st) {
      case 'PENDING':
        return [
          { status: 'REVIEWED', label: 'Marquer revue', cls: 'btn-outline-info' },
        ];
      case 'REVIEWED':
        return [
          { status: 'ACCEPTED', label: 'Accepter', cls: 'btn-outline-primary' },
          { status: 'REJECTED', label: 'Rejeter', cls: 'btn-outline-secondary' },
        ];
      case 'ACCEPTED':
        return [
          { status: 'EXECUTED', label: 'Marquer exécutée', cls: 'btn-outline-success' },
        ];
      default:
        return [];
    }
  }

  openConfirm(
    row: RecommendationRow,
    target: Exclude<RecommendationStatus, 'PENDING'>,
  ): void {
    this.pendingAction.set({
      recommendationId: row.id,
      targetStatus: target,
      fromLabel: this.statusLabel(row.status),
      toLabel: this.statusLabel(target),
    });
    this.reviewNote.set('');
  }

  cancelConfirm(): void {
    this.pendingAction.set(null);
    this.reviewNote.set('');
  }

  confirmAction(): void {
    const action = this.pendingAction();
    if (!action) return;
    const note = this.reviewNote().trim();
    this.review.emit({
      recommendationId: action.recommendationId,
      targetStatus: action.targetStatus,
      reviewNote: note ? note : null,
    });
    this.pendingAction.set(null);
    this.reviewNote.set('');
  }

  trackById(_index: number, row: RecommendationRow): string {
    return row.id;
  }
}
