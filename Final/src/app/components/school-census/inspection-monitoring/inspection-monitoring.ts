import { CommonModule } from '@angular/common';
import { Component, DestroyRef, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { forkJoin, of } from 'rxjs';
import { catchError, map, switchMap } from 'rxjs/operators';
import {
  InspectionCriterion,
  InspectionListItem,
  InspectionRead,
  InspectionsApiService,
  InspectionStatus as ApiInspectionStatus,
} from '../shared/inspections-api.service';
import { downloadCsv, downloadExcel, ExportColumn, printTable } from '../shared/export-utils';
import { SchoolAdminService } from '../shared/school-admin.service';
import { Region, School } from '../shared/school-census.models';

type InspectionType = 'pedagogic' | 'administrative' | 'infrastructure' | 'health' | 'attendance';
type InspectionStatus = 'planned' | 'in_progress' | 'completed' | 'late';
type ActionPriority = 'low' | 'medium' | 'high';

interface InspectionTypeConfig {
  id: InspectionType;
  title: string;
  description: string;
  icon: string;
  color: string;
}

interface InspectionRow {
  id: string;
  schoolName: string;
  code: string;
  regionId: string;
  region: string;
  prefecture: string;
  type: InspectionType;
  supervisor: string;
  scheduledAt: string;
  dueDate: string;
  score: number;
  findings: number;
  openActions: number;
  priority: ActionPriority;
  status: InspectionStatus;
}

@Component({
  selector: 'app-inspection-monitoring',
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './inspection-monitoring.html',
  styleUrl: './inspection-monitoring.scss',
})
export class InspectionMonitoring {
  private inspectionsApi = inject(InspectionsApiService);
  private schoolApi = inject(SchoolAdminService);
  private destroyRef = inject(DestroyRef);

  regions: Region[] = [];
  rows: InspectionRow[] = [];
  loading = false;
  error = '';
  searchTerm = '';
  selectedRegionId = '';
  selectedType = '';
  selectedPriority = '';
  selectedStatus = '';

  /**
   * Mappe les 8 critères backend (Phase 10) vers les 5 types affichés à l'écran.
   * Si une inspection a plusieurs constats, on prend le critère majoritaire.
   */
  private static readonly CRITERION_TO_TYPE: Record<InspectionCriterion, InspectionType> = {
    GOVERNANCE: 'administrative',
    DOCUMENTS: 'administrative',
    PEDAGOGY: 'pedagogic',
    INFRASTRUCTURE: 'infrastructure',
    SAFETY: 'infrastructure',
    EQUITY: 'infrastructure',
    HYGIENE: 'health',
    ATTENDANCE: 'attendance',
  };

  inspectionTypes: InspectionTypeConfig[] = [
    {
      id: 'pedagogic',
      title: 'Pédagogique',
      description: 'Cahiers, évaluations, progression et pratiques de classe.',
      icon: 'ri-book-open-line',
      color: 'primary',
    },
    {
      id: 'administrative',
      title: 'Administrative',
      description: 'Registres, gouvernance, affichages et dossiers obligatoires.',
      icon: 'ri-file-list-3-line',
      color: 'info',
    },
    {
      id: 'infrastructure',
      title: 'Infrastructure',
      description: 'Salles, mobilier, eau, latrines, clôture et sécurité.',
      icon: 'ri-building-4-line',
      color: 'secondary',
    },
    {
      id: 'health',
      title: 'Santé & hygiène',
      description: 'Hygiène scolaire, infirmerie, points d’eau et sensibilisation.',
      icon: 'ri-heart-pulse-line',
      color: 'danger',
    },
    {
      id: 'attendance',
      title: 'Présences',
      description: 'Contrôle des absences, retards et remontées QR.',
      icon: 'ri-calendar-check-line',
      color: 'success',
    },
  ];

  private supervisors = [
    'Aminata Diallo',
    'Moussa Camara',
    'Fatoumata Barry',
    'Ibrahima Condé',
    'Mariama Bah',
    'Abdoulaye Sylla',
  ];

  private exportColumns: ExportColumn<InspectionRow>[] = [
    { header: 'Code école', value: (row) => row.code },
    { header: 'Établissement', value: (row) => row.schoolName },
    { header: 'Région', value: (row) => row.region },
    { header: 'Préfecture', value: (row) => row.prefecture },
    { header: 'Type', value: (row) => this.typeLabel(row.type) },
    { header: 'Superviseur', value: (row) => row.supervisor },
    { header: 'Date prévue', value: (row) => row.scheduledAt },
    { header: 'Échéance', value: (row) => row.dueDate },
    { header: 'Score', value: (row) => `${row.score}%` },
    { header: 'Constats', value: (row) => row.findings },
    { header: 'Actions ouvertes', value: (row) => row.openActions },
    { header: 'Priorité', value: (row) => this.priorityLabel(row.priority) },
    { header: 'Statut', value: (row) => this.statusLabel(row.status) },
  ];

  ngOnInit() {
    this.load();
  }

  get filteredRows() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.rows.filter((row) => {
      const matchesRegion = !this.selectedRegionId || row.regionId === this.selectedRegionId;
      const matchesType = !this.selectedType || row.type === this.selectedType;
      const matchesPriority = !this.selectedPriority || row.priority === this.selectedPriority;
      const matchesStatus = !this.selectedStatus || row.status === this.selectedStatus;
      const searchable = this.normalizeSearch(
        [row.schoolName, row.code, row.region, row.prefecture, row.supervisor, this.typeLabel(row.type)].join(' '),
      );

      return matchesRegion && matchesType && matchesPriority && matchesStatus && (!search || searchable.includes(search));
    });
  }

  get totals() {
    const rows = this.filteredRows;
    const completedRows = rows.filter((row) => row.status === 'completed');
    const score = rows.length ? Math.round(rows.reduce((sum, row) => sum + row.score, 0) / rows.length) : 0;

    return {
      planned: rows.filter((row) => row.status === 'planned').length,
      completed: completedRows.length,
      active: rows.filter((row) => row.status === 'in_progress').length,
      late: rows.filter((row) => row.status === 'late').length,
      highPriority: rows.filter((row) => row.priority === 'high').length,
      openActions: rows.reduce((sum, row) => sum + row.openActions, 0),
      score,
      completionRate: rows.length ? Math.round((completedRows.length / rows.length) * 100) : 0,
    };
  }

  get typeSummaries() {
    return this.inspectionTypes.map((type) => {
      const rows = this.filteredRows.filter((row) => row.type === type.id);
      const score = rows.length ? Math.round(rows.reduce((sum, row) => sum + row.score, 0) / rows.length) : 0;

      return {
        ...type,
        rows: rows.length,
        score,
        openActions: rows.reduce((sum, row) => sum + row.openActions, 0),
      };
    });
  }

  get prioritySummaries() {
    return [
      {
        priority: 'high' as ActionPriority,
        label: 'Haute priorité',
        count: this.filteredRows.filter((row) => row.priority === 'high').length,
        className: 'bg-danger-transparent text-danger',
      },
      {
        priority: 'medium' as ActionPriority,
        label: 'Priorité moyenne',
        count: this.filteredRows.filter((row) => row.priority === 'medium').length,
        className: 'bg-warning-transparent text-warning',
      },
      {
        priority: 'low' as ActionPriority,
        label: 'Priorité faible',
        count: this.filteredRows.filter((row) => row.priority === 'low').length,
        className: 'bg-success-transparent text-success',
      },
    ];
  }

  load() {
    this.loading = true;
    this.error = '';

    // 1. Liste paginée des inspections (jusqu'à 500 — la pagination Phase 10 capée à 500)
    // 2. En parallèle : la liste des écoles pour résoudre region/prefecture/code
    // 3. Pour chaque inspection : un GET détail pour récupérer les findings et
    //    en déduire le type dominant (le critère majoritaire) — 8 inspections
    //    typiques = 8 appels parallèles, OK.
    forkJoin({
      page: this.inspectionsApi.list({ pageSize: 500 }),
      schools: this.schoolApi.listSchools(),
    })
      .pipe(
        switchMap(({ page, schools }) => {
          if (page.rows.length === 0) {
            return of({ list: page.rows, schools, details: [] as InspectionRead[] });
          }
          return forkJoin(
            page.rows.map((item) => this.inspectionsApi.get(item.id)),
          ).pipe(map((details) => ({ list: page.rows, schools, details })));
        }),
        catchError(() => of(null)),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((result) => {
        if (!result) {
          // Échec API → fallback démo (l'utilisateur n'a pas un écran vide)
          this.regions = this.fallbackRegions();
          this.rows = this.buildMockRows([]);
          this.error =
            'Données backend indisponibles, affichage des inspections de démonstration.';
          this.loading = false;
          return;
        }
        const schoolsById = new Map<string, School>(result.schools.map((s) => [s.id, s]));
        const detailsById = new Map<string, InspectionRead>(
          result.details.map((d) => [d.id, d]),
        );
        this.regions = this.deriveRegions(result.schools);
        this.rows = result.list.map((item) =>
          this.mapInspectionToRow(item, schoolsById, detailsById),
        );
        this.loading = false;
      });
  }

  resetFilters() {
    this.searchTerm = '';
    this.selectedRegionId = '';
    this.selectedType = '';
    this.selectedPriority = '';
    this.selectedStatus = '';
  }

  exportRows(format: 'csv' | 'excel' | 'print') {
    if (format === 'csv') {
      downloadCsv('inspections-supervision.csv', this.filteredRows, this.exportColumns);
      return;
    }

    if (format === 'excel') {
      downloadExcel('inspections-supervision.xls', this.filteredRows, this.exportColumns);
      return;
    }

    printTable('Inspections & supervision', this.filteredRows, this.exportColumns);
  }

  typeLabel(type: InspectionType) {
    return this.inspectionTypes.find((item) => item.id === type)?.title ?? type;
  }

  statusLabel(status: InspectionStatus) {
    const labels: Record<InspectionStatus, string> = {
      planned: 'Planifiée',
      in_progress: 'En cours',
      completed: 'Terminée',
      late: 'En retard',
    };

    return labels[status];
  }

  statusClass(status: InspectionStatus) {
    const classes: Record<InspectionStatus, string> = {
      planned: 'bg-info-transparent text-info',
      in_progress: 'bg-primary-transparent text-primary',
      completed: 'bg-success-transparent text-success',
      late: 'bg-danger-transparent text-danger',
    };

    return classes[status];
  }

  priorityLabel(priority: ActionPriority) {
    const labels: Record<ActionPriority, string> = {
      low: 'Faible',
      medium: 'Moyenne',
      high: 'Haute',
    };

    return labels[priority];
  }

  priorityClass(priority: ActionPriority) {
    const classes: Record<ActionPriority, string> = {
      low: 'bg-success-transparent text-success',
      medium: 'bg-warning-transparent text-warning',
      high: 'bg-danger-transparent text-danger',
    };

    return classes[priority];
  }

  scoreClass(score: number) {
    if (score >= 80) {
      return 'text-success';
    }

    if (score >= 60) {
      return 'text-warning';
    }

    return 'text-danger';
  }

  toneClass(color: string) {
    return `bg-${color}-transparent text-${color}`;
  }

  formatNumber(value: number) {
    return value.toLocaleString('fr-FR');
  }

  // =======================================================================
  // MAPPING API → InspectionRow (template existant intact)
  // =======================================================================
  private mapInspectionToRow(
    item: InspectionListItem,
    schoolsById: Map<string, School>,
    detailsById: Map<string, InspectionRead>,
  ): InspectionRow {
    const school = schoolsById.get(item.schoolId);
    const detail = detailsById.get(item.id);
    const score = Math.round(item.overallScore ?? 0);
    const findings = detail?.findings.length ?? 0;
    const openActions = item.actionItemsOpen;
    const priority: ActionPriority =
      score < 60 || openActions >= 6 ? 'high' : score < 78 ? 'medium' : 'low';

    // dueDate = première dueDate d'une action ouverte (la plus pressante)
    const openActionItems = (detail?.actionItems ?? []).filter(
      (a) => a.status === 'OPEN' || a.status === 'IN_PROGRESS',
    );
    const earliestDue = openActionItems
      .map((a) => new Date(a.dueDate))
      .sort((a, b) => a.getTime() - b.getTime())[0];

    const scheduledAt = this.formatDate(item.scheduledDate);
    const dueDate = earliestDue ? this.formatDate(earliestDue.toISOString()) : '—';

    return {
      id: item.id,
      schoolName: school?.name ?? item.school?.name ?? 'École inconnue',
      code: school?.code ?? item.school?.code ?? '',
      regionId: school?.regionId ?? '',
      region: school?.region?.name ?? 'Région non renseignée',
      prefecture:
        school?.prefecture ?? school?.prefectureRef?.name ?? 'Préfecture non renseignée',
      type: this.deriveDominantType(detail),
      supervisor: item.inspector?.fullName ?? 'Non assigné',
      scheduledAt,
      dueDate,
      score,
      findings,
      openActions,
      priority,
      status: this.mapStatus(item.status, item.scheduledDate, openActions),
    };
  }

  private deriveDominantType(detail: InspectionRead | undefined): InspectionType {
    const findings = detail?.findings ?? [];
    if (findings.length === 0) {
      return 'infrastructure';
    }
    const counts = new Map<InspectionType, number>();
    for (const f of findings) {
      const t = InspectionMonitoring.CRITERION_TO_TYPE[f.criterion];
      counts.set(t, (counts.get(t) ?? 0) + 1);
    }
    let best: InspectionType = 'infrastructure';
    let max = 0;
    for (const [t, n] of counts) {
      if (n > max) {
        max = n;
        best = t;
      }
    }
    return best;
  }

  private mapStatus(
    apiStatus: ApiInspectionStatus,
    scheduledDate: string,
    openActions: number,
  ): InspectionStatus {
    if (apiStatus === 'COMPLETED') {
      return 'completed';
    }
    if (apiStatus === 'IN_PROGRESS') {
      return 'in_progress';
    }
    if (apiStatus === 'CANCELLED') {
      return 'completed'; // termine la ligne ; aucun bucket "annulée" côté UI existante
    }
    // PLANNED : si la date est passée et qu'il reste des actions, c'est en retard
    const scheduled = new Date(scheduledDate);
    if (scheduled.getTime() < Date.now() && openActions > 0) {
      return 'late';
    }
    return 'planned';
  }

  private deriveRegions(schools: School[]): Region[] {
    const regions = new Map<string, Region>();
    for (const s of schools) {
      if (s.region) {
        regions.set(s.region.id, s.region);
      }
    }
    const list = Array.from(regions.values());
    return list.length ? list.sort((a, b) => a.name.localeCompare(b.name)) : this.fallbackRegions();
  }

  private formatDate(iso: string): string {
    const d = new Date(iso);
    return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}/${d.getFullYear()}`;
  }

  // =======================================================================
  // Fallback démo (utilisé seulement si l'API tombe)
  // =======================================================================
  private buildMockRows(schools: School[]): InspectionRow[] {
    const sourceSchools = schools.length ? schools : this.fallbackSchools();
    const regionById = new Map(this.regions.map((region) => [region.id, region]));

    return sourceSchools.slice(0, 75).map((school, index) => {
      const type = this.inspectionTypes[index % this.inspectionTypes.length].id;
      const score = Math.max(42, Math.min(96, 58 + (index % 9) * 5 - (index % 4) * 3));
      const findings = Math.max(1, 10 - Math.round(score / 12) + (index % 3));
      const openActions = Math.max(0, findings - (index % 4));
      const priority: ActionPriority = score < 60 || openActions >= 6 ? 'high' : score < 78 ? 'medium' : 'low';
      const status: InspectionStatus =
        index % 10 === 0 ? 'late' : index % 4 === 0 ? 'in_progress' : index % 3 === 0 ? 'completed' : 'planned';
      const region = school.region ?? regionById.get(school.regionId);

      return {
        id: `${school.id}-${type}-${index}`,
        schoolName: school.name,
        code: school.code,
        regionId: school.regionId,
        region: region?.name ?? 'Région non renseignée',
        prefecture: school.prefecture ?? school.prefectureRef?.name ?? 'Préfecture non renseignée',
        type,
        supervisor: this.supervisors[index % this.supervisors.length],
        scheduledAt: `${String(3 + (index % 24)).padStart(2, '0')}/05/2026`,
        dueDate: `${String(8 + (index % 18)).padStart(2, '0')}/05/2026`,
        score,
        findings,
        openActions,
        priority,
        status,
      };
    });
  }

  private fallbackRegions(): Region[] {
    return [
      { id: 'rg-conakry', code: 'RG-CON', name: 'Conakry' },
      { id: 'rg-kankan', code: 'RG-KAN', name: 'Kankan' },
      { id: 'rg-nzerekore', code: 'RG-NZE', name: 'Nzérékoré' },
    ];
  }

  private fallbackSchools(): School[] {
    const regions = this.regions.length ? this.regions : this.fallbackRegions();
    const names = [
      'École Primaire Almamya',
      'Collège 2 Octobre',
      'Lycée Donka',
      'École Kouroula',
      'Collège Central Labé',
      'École Application Kankan',
      'Lycée Yimbaya',
      'École Franco-Arabe Madina',
    ];

    return names.map((name, index) => {
      const region = regions[index % regions.length];

      return {
        id: `school-inspection-${index + 1}`,
        name,
        code: `ECO-${String(index + 1).padStart(3, '0')}`,
        prefecture: index % 2 ? 'Matoto' : 'Kaloum',
        regionId: region.id,
        region,
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
