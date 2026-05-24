import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { RouterModule } from '@angular/router';

import { CriticalSchool, EnrollmentApiService } from '../shared/enrollment-api.service';

interface CriticalSchoolRow {
  schoolId: string;
  name: string;
  regionName: string;
  gpi: number | null;
  severity: CriticalSchool['severity'];
  girls: number;
  boys: number;
}

/**
 * Table "écoles critiques" — top N écoles dont le GPI tombe sous le seuil
 * UNESCO (0.85). Table HTML pure (pas de gridjs) pour rester accessible
 * au clavier et lisible avec un lecteur d'écran.
 */
@Component({
  selector: 'app-equite-critical-schools-table',
  imports: [CommonModule, RouterModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './equite-critical-schools-table.html',
  styleUrl: './equite-critical-schools-table.scss',
})
export class EquiteCriticalSchoolsTable {
  schools = input.required<CriticalSchool[]>();
  title = input<string>('Écoles à GPI critique');

  readonly rows = computed<CriticalSchoolRow[]>(() =>
    (this.schools() ?? []).map((s) => ({
      schoolId: s.entityId ?? '',
      name: s.entityName ?? s.entityId ?? 'École inconnue',
      regionName: '—', // optionnel : enrichi par le caller si dispo
      gpi: EnrollmentApiService.toNumber(s.gpi),
      severity: s.severity,
      girls: s.girlsCount,
      boys: s.boysCount,
    })),
  );

  severityBadge(sev: CriticalSchool['severity']): string {
    switch (sev) {
      case 'CRITICAL_GIRLS':
        return 'bg-danger-transparent text-danger';
      case 'WARNING_GIRLS':
        return 'bg-warning-transparent text-warning';
      case 'WARNING_BOYS':
        return 'bg-info-transparent text-info';
      default:
        return 'bg-success-transparent text-success';
    }
  }

  severityLabel(sev: CriticalSchool['severity']): string {
    switch (sev) {
      case 'CRITICAL_GIRLS':
        return 'Critique filles';
      case 'WARNING_GIRLS':
        return 'Alerte filles';
      case 'WARNING_BOYS':
        return 'Alerte garçons';
      default:
        return 'Normal';
    }
  }
}
