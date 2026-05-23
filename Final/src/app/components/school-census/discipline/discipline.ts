import { CommonModule } from '@angular/common';
import { Component, DestroyRef, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { catchError, forkJoin, of } from 'rxjs';
import { CensusApiService } from '../shared/census-api.service';
import { downloadCsv, downloadExcel, ExportColumn, printTable } from '../shared/export-utils';
import { IncidentRow as ApiIncidentRow, SchoolLifeApiService } from '../shared/schoollife-api.service';
import { CensusPerson, Region } from '../shared/school-census.models';

type IncidentType = 'absence' | 'conduct' | 'violence' | 'material' | 'mediation';
type IncidentStatus = 'open' | 'mediation' | 'resolved' | 'escalated';
type SeverityLevel = 'low' | 'medium' | 'high';

interface DisciplineRow {
  id: string;
  studentName: string;
  uniqueCode: string;
  schoolName: string;
  className: string;
  regionId: string;
  region: string;
  type: IncidentType;
  severity: SeverityLevel;
  status: IncidentStatus;
  reportedAt: string;
  daysOpen: number;
  action: string;
}

@Component({
  selector: 'app-discipline',
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './discipline.html',
  styleUrl: './discipline.scss',
})
export class Discipline {
  private censusApi = inject(CensusApiService);
  private schoolLifeApi = inject(SchoolLifeApiService);
  private destroyRef = inject(DestroyRef);

  regions: Region[] = [];
  rows: DisciplineRow[] = [];
  loading = false;
  error = '';
  searchTerm = '';
  selectedRegionId = '';
  selectedType = '';
  selectedStatus = '';
  selectedSeverity = '';

  /** Mappage backend (Phase 13) → catégories UI existantes. */
  private static readonly TYPE_MAP: Record<string, IncidentType> = {
    LATENESS: 'absence',
    ABSENCE: 'absence',
    INSUBORDINATION: 'conduct',
    BULLYING: 'conduct',
    FIGHTING: 'violence',
    PROPERTY_DAMAGE: 'material',
    OTHER: 'mediation',
  };
  private static readonly SEVERITY_MAP: Record<string, SeverityLevel> = {
    LOW: 'low', MEDIUM: 'medium', HIGH: 'high',
  };

  incidentTypes: Array<{ value: IncidentType; label: string }> = [
    { value: 'absence', label: 'Absences répétées' },
    { value: 'conduct', label: 'Conduite' },
    { value: 'violence', label: 'Violence' },
    { value: 'material', label: 'Dégradation' },
    { value: 'mediation', label: 'Médiation' },
  ];

  private exportColumns: ExportColumn<DisciplineRow>[] = [
    { header: 'Code élève', value: (row) => row.uniqueCode },
    { header: 'Élève', value: (row) => row.studentName },
    { header: 'École', value: (row) => row.schoolName },
    { header: 'Classe', value: (row) => row.className },
    { header: 'Région', value: (row) => row.region },
    { header: 'Type', value: (row) => this.typeLabel(row.type) },
    { header: 'Gravité', value: (row) => this.severityLabel(row.severity) },
    { header: 'Statut', value: (row) => this.statusLabel(row.status) },
    { header: 'Signalé le', value: (row) => row.reportedAt },
    { header: 'Jours ouverts', value: (row) => row.daysOpen },
    { header: 'Action', value: (row) => row.action },
  ];

  ngOnInit() {
    this.load();
  }

  get filteredRows() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.rows.filter((row) => {
      const matchesRegion = !this.selectedRegionId || row.regionId === this.selectedRegionId;
      const matchesType = !this.selectedType || row.type === this.selectedType;
      const matchesStatus = !this.selectedStatus || row.status === this.selectedStatus;
      const matchesSeverity = !this.selectedSeverity || row.severity === this.selectedSeverity;
      const searchable = this.normalizeSearch(
        [row.studentName, row.uniqueCode, row.schoolName, row.className, row.region, row.action].join(' '),
      );

      return (
        matchesRegion &&
        matchesType &&
        matchesStatus &&
        matchesSeverity &&
        (!search || searchable.includes(search))
      );
    });
  }

  get totals() {
    const rows = this.filteredRows;

    return {
      incidents: rows.length,
      open: rows.filter((row) => row.status === 'open').length,
      mediation: rows.filter((row) => row.status === 'mediation').length,
      resolved: rows.filter((row) => row.status === 'resolved').length,
      escalated: rows.filter((row) => row.status === 'escalated').length,
      highSeverity: rows.filter((row) => row.severity === 'high').length,
      overdue: rows.filter((row) => row.status !== 'resolved' && row.daysOpen > 5).length,
    };
  }

  load() {
    this.loading = true;
    this.error = '';

    forkJoin({
      metadata: this.censusApi.metadata(),
      incidents: this.schoolLifeApi.listIncidents({ limit: 1000 }),
    })
      .pipe(catchError(() => of(null)), takeUntilDestroyed(this.destroyRef))
      .subscribe((result) => {
        if (!result) {
          this.error = 'Impossible de charger la discipline scolaire.';
          this.loading = false;
          return;
        }
        this.regions = result.metadata.regions;
        // Index région via les écoles métadata
        const schoolToRegion = new Map<string, { id: string; name: string }>();
        for (const s of result.metadata.schools ?? []) {
          if (s.region) schoolToRegion.set(s.id, { id: s.region.id, name: s.region.name });
        }
        this.rows = result.incidents.map((i) => this.toRow(i, schoolToRegion));
        this.loading = false;
      });
  }

  private toRow(
    i: ApiIncidentRow,
    schoolToRegion: Map<string, { id: string; name: string }>,
  ): DisciplineRow {
    const reg = schoolToRegion.get(i.schoolId);
    const reportedAt = new Date(i.occurredAt);
    const daysOpen = Math.max(0, Math.floor(
      (Date.now() - reportedAt.getTime()) / (1000 * 60 * 60 * 24),
    ));
    // Statut UI dérivé de la sanction
    let status: IncidentStatus;
    if (i.sanction === 'EXPULSION' || i.sanction === 'SUSPENSION') status = 'escalated';
    else if (i.sanction === 'PARENT_MEETING' || i.sanction === 'DETENTION') status = 'mediation';
    else if (i.sanction === 'WARNING' && daysOpen > 14) status = 'resolved';
    else if (i.sanction === 'NONE' && daysOpen > 30) status = 'resolved';
    else status = 'open';

    const action = ({
      NONE: 'À examiner',
      WARNING: 'Avertissement notifié',
      DETENTION: 'Retenue programmée',
      PARENT_MEETING: 'Convocation parents',
      SUSPENSION: 'Exclusion temporaire',
      EXPULSION: 'Exclusion définitive',
    } as const)[i.sanction];

    return {
      id: i.id,
      studentName: i.student
        ? `${i.student.firstName} ${i.student.lastName}`
        : '— Anonyme —',
      uniqueCode: i.student?.uniqueCode ?? '—',
      schoolName: i.school?.name ?? '—',
      className: '—',
      regionId: reg?.id ?? '',
      region: reg?.name ?? 'Région N/A',
      type: Discipline.TYPE_MAP[i.type] ?? 'mediation',
      severity: Discipline.SEVERITY_MAP[i.severity] ?? 'low',
      status,
      reportedAt: reportedAt.toLocaleDateString('fr-FR'),
      daysOpen,
      action,
    };
  }

  resetFilters() {
    this.searchTerm = '';
    this.selectedRegionId = '';
    this.selectedType = '';
    this.selectedStatus = '';
    this.selectedSeverity = '';
  }

  exportRows(format: 'csv' | 'excel' | 'print') {
    if (format === 'csv') {
      downloadCsv('discipline-incidents.csv', this.filteredRows, this.exportColumns);
      return;
    }

    if (format === 'excel') {
      downloadExcel('discipline-incidents.xls', this.filteredRows, this.exportColumns);
      return;
    }

    printTable('Discipline & incidents', this.filteredRows, this.exportColumns);
  }

  typeLabel(type: IncidentType) {
    return this.incidentTypes.find((item) => item.value === type)?.label ?? type;
  }

  statusLabel(status: IncidentStatus) {
    const labels: Record<IncidentStatus, string> = {
      open: 'Ouvert',
      mediation: 'Médiation',
      resolved: 'Résolu',
      escalated: 'Escaladé',
    };

    return labels[status];
  }

  statusClass(status: IncidentStatus) {
    const classes: Record<IncidentStatus, string> = {
      open: 'bg-warning-transparent text-warning',
      mediation: 'bg-info-transparent text-info',
      resolved: 'bg-success-transparent text-success',
      escalated: 'bg-danger-transparent text-danger',
    };

    return classes[status];
  }

  severityLabel(severity: SeverityLevel) {
    const labels: Record<SeverityLevel, string> = {
      low: 'Faible',
      medium: 'Moyenne',
      high: 'Élevée',
    };

    return labels[severity];
  }

  severityClass(severity: SeverityLevel) {
    const classes: Record<SeverityLevel, string> = {
      low: 'bg-success-transparent text-success',
      medium: 'bg-warning-transparent text-warning',
      high: 'bg-danger-transparent text-danger',
    };

    return classes[severity];
  }

  formatNumber(value: number) {
    return value.toLocaleString('fr-FR');
  }

  private buildIncidentRows(students: CensusPerson[]): DisciplineRow[] {
    const actions = [
      'Entretien avec le parent',
      'Suivi par le directeur',
      'Médiation en cours',
      'Conseil de discipline',
      'Accompagnement pédagogique',
    ];

    return students.slice(0, 64).map((student, index) => {
      const type = this.incidentTypes[index % this.incidentTypes.length].value;
      const severity: SeverityLevel = index % 8 === 0 ? 'high' : index % 3 === 0 ? 'medium' : 'low';
      const status: IncidentStatus =
        index % 10 === 0 ? 'escalated' : index % 4 === 0 ? 'resolved' : index % 3 === 0 ? 'mediation' : 'open';
      const daysOpen = status === 'resolved' ? index % 3 : 1 + (index % 12);
      const reportedDate = new Date(2026, 3, Math.max(1, 29 - (index % 25)));

      return {
        id: student.id,
        studentName: student.fullName,
        uniqueCode: student.uniqueCode,
        schoolName: student.school?.name ?? 'École non renseignée',
        className: student.classRoom?.name ?? 'Classe non affectée',
        regionId: student.school?.regionId ?? '',
        region: student.school?.region?.name ?? 'Région non renseignée',
        type,
        severity,
        status,
        reportedAt: reportedDate.toLocaleDateString('fr-FR'),
        daysOpen,
        action: actions[index % actions.length],
      };
    });
  }

  private normalizeSearch(value?: string | null) {
    return (value ?? '')
      .toLocaleLowerCase('fr-FR')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '');
  }
}
