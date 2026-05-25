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

import { Operation } from '../shared/simulator-api.service';
import { School } from '../shared/school-census.models';

/** Mode courant du panneau opérations. */
export type SimulatorMode = 'view' | 'create' | 'close' | 'merge';

/** Payload émis lors de la validation du dialog "Sauvegarder". */
export interface SaveScenarioPayload {
  name: string;
  description: string | null;
}

/**
 * Module 3B UI — Panneau de pilotage des opérations du scénario.
 *
 * Responsabilités :
 *   - Toggle 4 modes (view/create/close/merge) → réémis vers la page.
 *   - Liste des opérations en cours sous forme de chips, avec X pour
 *     retirer une op.
 *   - Bouton "Calculer impact" actif quand >= 1 op + scénario sauvegardé.
 *   - Bouton "Sauvegarder" qui ouvre un mini-formulaire (nom + description).
 *
 * Présentationnel : pas d'appel HTTP, pas d'état métier — c'est la page
 * `SimulateurPage` qui orchestre.
 */
@Component({
  selector: 'app-operations-panel',
  imports: [CommonModule, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './operations-panel.html',
  styleUrl: './operations-panel.scss',
})
export class OperationsPanel {
  operations = input.required<Operation[]>();
  /** Indispensable pour résoudre un libellé école sur les ops CLOSE / MERGE. */
  schools = input<School[]>([]);
  /** Mode actuel piloté par la page (pour synchroniser carte ↔ panneau). */
  mode = input<SimulatorMode>('view');
  /** Désactive les actions pendant un appel /compute ou /create. */
  busy = input<boolean>(false);
  /** Désactive les actions d'écriture (utilisateur non-administrateur). */
  canEdit = input<boolean>(true);
  /** Indique qu'un scénario est déjà persisté (autorise /compute). */
  scenarioId = input<string | null>(null);

  readonly modeChange = output<SimulatorMode>();
  readonly removeOperation = output<number>();
  readonly compute = output<void>();
  readonly save = output<SaveScenarioPayload>();

  readonly draftName = signal<string>('');
  readonly draftDescription = signal<string>('');
  readonly showSaveDialog = signal<boolean>(false);

  readonly hasOperations = computed<boolean>(
    () => (this.operations() ?? []).length > 0,
  );

  readonly canCompute = computed<boolean>(
    () => !this.busy() && this.hasOperations() && this.scenarioId() !== null,
  );

  readonly canSave = computed<boolean>(
    () => !this.busy() && this.hasOperations() && this.canEdit(),
  );

  setMode(value: SimulatorMode): void {
    if (!this.canEdit() && value !== 'view') {
      return;
    }
    this.modeChange.emit(value);
  }

  modeBtnClass(target: SimulatorMode): string {
    return this.mode() === target
      ? 'btn btn-primary btn-sm'
      : 'btn btn-outline-primary btn-sm';
  }

  /** Label court pour une opération, affiché dans le chip. */
  labelFor(op: Operation): string {
    switch (op.type) {
      case 'CREATE_SCHOOL':
        return `+ ${op.name} (${op.capacity} pl.)`;
      case 'CLOSE_SCHOOL': {
        const name = this.schoolNameById(op.schoolId);
        return `× ${name}`;
      }
      case 'MERGE_SCHOOLS': {
        const names = op.sourceSchoolIds
          .map((id) => this.schoolNameById(id))
          .join(' + ');
        return `${names} → ${op.targetName}`;
      }
    }
  }

  classFor(op: Operation): string {
    switch (op.type) {
      case 'CREATE_SCHOOL':
        return 'op-chip op-chip--create';
      case 'CLOSE_SCHOOL':
        return 'op-chip op-chip--close';
      case 'MERGE_SCHOOLS':
        return 'op-chip op-chip--merge';
    }
  }

  onRemove(index: number): void {
    if (this.busy()) return;
    this.removeOperation.emit(index);
  }

  onCompute(): void {
    if (!this.canCompute()) return;
    this.compute.emit();
  }

  openSaveDialog(): void {
    if (!this.canSave()) return;
    this.draftName.set('');
    this.draftDescription.set('');
    this.showSaveDialog.set(true);
  }

  closeSaveDialog(): void {
    this.showSaveDialog.set(false);
  }

  confirmSave(): void {
    const name = this.draftName().trim();
    if (!name) return;
    const desc = this.draftDescription().trim();
    this.save.emit({ name, description: desc.length ? desc : null });
    this.showSaveDialog.set(false);
  }

  setDraftName(value: string): void {
    this.draftName.set(value);
  }

  setDraftDescription(value: string): void {
    this.draftDescription.set(value);
  }

  private schoolNameById(id: string): string {
    const list = this.schools() ?? [];
    return list.find((s) => s.id === id)?.name ?? id;
  }
}
