import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
  output,
} from '@angular/core';

import { ScenarioRead, ScenarioStatus } from '../shared/simulator-api.service';

/**
 * Module 3B UI — Table simple des scénarios créés.
 *
 * Actions disponibles selon le statut :
 *   - DRAFT     : "Calculer impact"
 *   - COMPUTED  : "Voir détails"
 *   - DRAFT|COMPUTED : "Archiver"
 *   - ARCHIVED  : pas d'action (mais le backend ne renvoie pas ces lignes
 *     par défaut, donc cette branche est défensive).
 */
interface ScenarioRow {
  id: string;
  name: string;
  status: ScenarioStatus;
  createdAt: string;
  computedAt: string | null;
  operationsCount: number;
  canCompute: boolean;
  canView: boolean;
  canArchive: boolean;
}

@Component({
  selector: 'app-scenarios-table',
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './scenarios-table.html',
  styleUrl: './scenarios-table.scss',
})
export class ScenariosTable {
  scenarios = input.required<ScenarioRead[]>();
  /** L'utilisateur peut-il déclencher compute / archive ? */
  canWrite = input<boolean>(false);
  busy = input<boolean>(false);

  readonly compute = output<string>();
  readonly view = output<string>();
  readonly archive = output<string>();

  readonly rows = computed<ScenarioRow[]>(() => {
    const list = this.scenarios() ?? [];
    return list.map((s) => {
      const ops = this.countOperations(s);
      return {
        id: s.id,
        name: s.name,
        status: s.status,
        createdAt: s.createdAt,
        computedAt: s.computedAt,
        operationsCount: ops,
        canCompute: this.canWrite() && s.status === 'DRAFT',
        canView: s.status === 'COMPUTED',
        canArchive: this.canWrite() && s.status !== 'ARCHIVED',
      };
    });
  });

  statusClass(status: ScenarioStatus): string {
    switch (status) {
      case 'DRAFT':
        return 'badge bg-warning-transparent text-warning';
      case 'COMPUTED':
        return 'badge bg-success-transparent text-success';
      case 'ARCHIVED':
        return 'badge bg-secondary-transparent text-secondary';
    }
  }

  formatDate(value: string | null): string {
    if (!value) return '—';
    try {
      return new Date(value).toLocaleString('fr-FR');
    } catch {
      return value;
    }
  }

  onCompute(id: string): void {
    if (this.busy()) return;
    this.compute.emit(id);
  }

  onView(id: string): void {
    this.view.emit(id);
  }

  onArchive(id: string): void {
    if (this.busy()) return;
    this.archive.emit(id);
  }

  private countOperations(s: ScenarioRead): number {
    const json = s.scenarioJson as { operations?: unknown[] } | null;
    if (json && Array.isArray(json.operations)) {
      return json.operations.length;
    }
    return 0;
  }
}
