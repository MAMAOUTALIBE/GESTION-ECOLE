import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
  output,
} from '@angular/core';

import {
  StaffingApiService,
  StaffingSeverity,
  TeacherStaffingSnapshot,
} from '../shared/staffing-api.service';
import { School } from '../shared/school-census.models';

/**
 * Module 2D UI — Top 20 écoles classées par sévérité staffing puis ratio.
 *
 * Triage : CRITICAL (4) > UNDER_STAFFED (3) > OVER_STAFFED (2) > ADEQUATE (1),
 * puis ratio décroissant (ratio NULL en tête car classé "sans enseignant").
 *
 * Le tri se fait sur une copie côté composant pour ne pas muter l'input.
 * Click ligne → emit selectedSchoolId pour permettre le highlight carte.
 */
interface StaffingRow {
  id: string;
  schoolId: string;
  schoolName: string;
  regionName: string;
  studentsCount: number;
  teachersCount: number;
  ratio: number | null;
  severity: StaffingSeverity;
  gap: number;
}

const SEVERITY_ORDER: Record<StaffingSeverity, number> = {
  CRITICAL: 4,
  UNDER_STAFFED: 3,
  OVER_STAFFED: 2,
  ADEQUATE: 1,
};

@Component({
  selector: 'app-staffing-table',
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './staffing-table.html',
  styleUrl: './staffing-table.scss',
})
export class StaffingTable {
  snapshots = input.required<TeacherStaffingSnapshot[]>();
  schools = input<School[]>([]);
  selectedSchoolId = input<string | null>(null);
  /** Nombre max de lignes — par défaut 20 (top critiques). */
  limit = input<number>(20);

  /** Émet l'id école sélectionnée pour highlight carte. */
  readonly selectSchool = output<string>();

  readonly rows = computed<StaffingRow[]>(() => {
    const schoolMap = new Map<string, School>();
    for (const s of this.schools() ?? []) {
      schoolMap.set(s.id, s);
    }

    const items = (this.snapshots() ?? []).slice();
    items.sort((a, b) => {
      const sa = SEVERITY_ORDER[a.severity] ?? 0;
      const sb = SEVERITY_ORDER[b.severity] ?? 0;
      if (sa !== sb) return sb - sa;
      // Au sein d'une sévérité, ratio décroissant ; NULL en premier (sans
      // enseignant — situation prioritaire).
      const ra = StaffingApiService.toNumber(a.ratio);
      const rb = StaffingApiService.toNumber(b.ratio);
      if (ra === null && rb === null) return 0;
      if (ra === null) return -1;
      if (rb === null) return 1;
      return rb - ra;
    });

    const limit = this.limit();
    return items.slice(0, limit).map((snap) => {
      const school = schoolMap.get(snap.schoolId);
      return {
        id: snap.id,
        schoolId: snap.schoolId,
        schoolName: school?.name ?? snap.schoolId,
        regionName: school?.region?.name ?? '—',
        studentsCount: snap.studentsCount,
        teachersCount: snap.teachersCount,
        ratio: StaffingApiService.toNumber(snap.ratio),
        severity: snap.severity,
        gap: snap.gap,
      } satisfies StaffingRow;
    });
  });

  severityBadge(sev: StaffingSeverity): string {
    switch (sev) {
      case 'CRITICAL':
        return 'bg-danger-transparent text-danger';
      case 'UNDER_STAFFED':
        return 'bg-warning-transparent text-warning';
      case 'OVER_STAFFED':
        return 'bg-info-transparent text-info';
      case 'ADEQUATE':
        return 'bg-success-transparent text-success';
    }
  }

  severityLabel(sev: StaffingSeverity): string {
    switch (sev) {
      case 'CRITICAL':
        return 'Critique';
      case 'UNDER_STAFFED':
        return 'Sous-doté';
      case 'OVER_STAFFED':
        return 'Sur-doté';
      case 'ADEQUATE':
        return 'Adéquat';
    }
  }

  formatRatio(r: number | null): string {
    return r === null ? '—' : r.toFixed(1);
  }

  onSelect(row: StaffingRow): void {
    this.selectSchool.emit(row.schoolId);
  }

  trackById(_index: number, row: StaffingRow): string {
    return row.id;
  }
}
