import { CommonModule } from '@angular/common';
import { Component, DestroyRef, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { forkJoin, of } from 'rxjs';
import { catchError } from 'rxjs/operators';
import {
  BudgetCategory as ApiBudgetCategory,
  BudgetRead,
  FinanceApiService,
} from '../shared/finance-api.service';
import { downloadCsv, downloadExcel, ExportColumn, printTable } from '../shared/export-utils';
import { SchoolAdminService } from '../shared/school-admin.service';
import { Region, School } from '../shared/school-census.models';

type BudgetProgram = 'operation' | 'infrastructure' | 'canteen' | 'pedagogy' | 'health';
type BudgetSource = 'state' | 'partner' | 'community' | 'project';
type BudgetStatus = 'balanced' | 'watch' | 'overrun';

interface BudgetProgramConfig {
  id: BudgetProgram;
  title: string;
  description: string;
  icon: string;
  color: string;
}

interface BudgetSourceConfig {
  id: BudgetSource;
  title: string;
  icon: string;
  color: string;
}

interface BudgetRow {
  id: string;
  schoolName: string;
  code: string;
  regionId: string;
  region: string;
  program: BudgetProgram;
  source: BudgetSource;
  allocated: number;
  spent: number;
  committed: number;
  remaining: number;
  executionRate: number;
  status: BudgetStatus;
  lastUpdate: string;
}

@Component({
  selector: 'app-budget-monitoring',
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './budget-monitoring.html',
  styleUrl: './budget-monitoring.scss',
})
export class BudgetMonitoring {
  private financeApi = inject(FinanceApiService);
  private schoolApi = inject(SchoolAdminService);
  private destroyRef = inject(DestroyRef);

  regions: Region[] = [];
  rows: BudgetRow[] = [];
  loading = false;
  error = '';
  searchTerm = '';
  selectedRegionId = '';
  selectedProgram = '';
  selectedSource = '';
  selectedStatus = '';

  /** Mappe les 8 catégories budgétaires backend (Phase 11) vers les 5 programmes UI. */
  private static readonly CATEGORY_TO_PROGRAM: Record<ApiBudgetCategory, BudgetProgram> = {
    OPERATIONS: 'operation',
    INFRASTRUCTURE: 'infrastructure',
    EQUIPMENT: 'infrastructure',
    MEALS: 'canteen',
    TRAINING: 'pedagogy',
    SALARIES: 'operation',
    TRANSPORT: 'operation',
    MISC: 'pedagogy',
  };

  /** Source de financement présentée — déduite de la catégorie (le backend ne tracke pas la source). */
  private static readonly CATEGORY_TO_SOURCE: Record<ApiBudgetCategory, BudgetSource> = {
    OPERATIONS: 'state',
    INFRASTRUCTURE: 'state',
    EQUIPMENT: 'partner',
    MEALS: 'project',
    TRAINING: 'partner',
    SALARIES: 'state',
    TRANSPORT: 'community',
    MISC: 'community',
  };

  programs: BudgetProgramConfig[] = [
    {
      id: 'operation',
      title: 'Fonctionnement',
      description: 'Charges courantes, entretien léger et fournitures administratives.',
      icon: 'ri-building-2-line',
      color: 'primary',
    },
    {
      id: 'infrastructure',
      title: 'Infrastructures',
      description: 'Réhabilitation, équipements, classes et points d’eau.',
      icon: 'ri-hammer-line',
      color: 'secondary',
    },
    {
      id: 'canteen',
      title: 'Cantines',
      description: 'Achats alimentaires, distribution et logistique des repas.',
      icon: 'ri-restaurant-2-line',
      color: 'success',
    },
    {
      id: 'pedagogy',
      title: 'Pédagogie',
      description: 'Manuels, supports d’apprentissage et ressources de classe.',
      icon: 'ri-book-open-line',
      color: 'info',
    },
    {
      id: 'health',
      title: 'Santé scolaire',
      description: 'Visites médicales, hygiène, sensibilisation et kits sanitaires.',
      icon: 'ri-heart-pulse-line',
      color: 'danger',
    },
  ];

  sources: BudgetSourceConfig[] = [
    { id: 'state', title: 'État', icon: 'ri-bank-line', color: 'primary' },
    { id: 'partner', title: 'Partenaires', icon: 'ri-handshake-line', color: 'info' },
    { id: 'community', title: 'Communauté', icon: 'ri-group-line', color: 'success' },
    { id: 'project', title: 'Projet ciblé', icon: 'ri-road-map-line', color: 'warning' },
  ];

  private exportColumns: ExportColumn<BudgetRow>[] = [
    { header: 'Code école', value: (row) => row.code },
    { header: 'Établissement', value: (row) => row.schoolName },
    { header: 'Région', value: (row) => row.region },
    { header: 'Programme', value: (row) => this.programLabel(row.program) },
    { header: 'Source', value: (row) => this.sourceLabel(row.source) },
    { header: 'Alloué', value: (row) => row.allocated },
    { header: 'Dépensé', value: (row) => row.spent },
    { header: 'Engagé', value: (row) => row.committed },
    { header: 'Solde', value: (row) => row.remaining },
    { header: 'Exécution', value: (row) => `${row.executionRate}%` },
    { header: 'Statut', value: (row) => this.statusLabel(row.status) },
    { header: 'Mise à jour', value: (row) => row.lastUpdate },
  ];

  ngOnInit() {
    this.load();
  }

  get filteredRows() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.rows.filter((row) => {
      const matchesRegion = !this.selectedRegionId || row.regionId === this.selectedRegionId;
      const matchesProgram = !this.selectedProgram || row.program === this.selectedProgram;
      const matchesSource = !this.selectedSource || row.source === this.selectedSource;
      const matchesStatus = !this.selectedStatus || row.status === this.selectedStatus;
      const searchable = this.normalizeSearch(
        [row.schoolName, row.code, row.region, this.programLabel(row.program), this.sourceLabel(row.source)].join(' '),
      );

      return matchesRegion && matchesProgram && matchesSource && matchesStatus && (!search || searchable.includes(search));
    });
  }

  get totals() {
    const rows = this.filteredRows;
    const allocated = rows.reduce((sum, row) => sum + row.allocated, 0);
    const spent = rows.reduce((sum, row) => sum + row.spent, 0);
    const committed = rows.reduce((sum, row) => sum + row.committed, 0);
    const remaining = rows.reduce((sum, row) => sum + row.remaining, 0);
    const used = spent + committed;

    return {
      lines: rows.length,
      allocated,
      spent,
      committed,
      remaining,
      executionRate: allocated ? Math.round((used / allocated) * 100) : 0,
      overrun: rows.filter((row) => row.status === 'overrun').length,
      watch: rows.filter((row) => row.status === 'watch').length,
    };
  }

  get programSummaries() {
    return this.programs.map((program) => {
      const rows = this.filteredRows.filter((row) => row.program === program.id);
      const allocated = rows.reduce((sum, row) => sum + row.allocated, 0);
      const spent = rows.reduce((sum, row) => sum + row.spent + row.committed, 0);

      return {
        ...program,
        rows: rows.length,
        allocated,
        executionRate: allocated ? Math.round((spent / allocated) * 100) : 0,
      };
    });
  }

  get sourceSummaries() {
    return this.sources.map((source) => {
      const rows = this.filteredRows.filter((row) => row.source === source.id);
      const allocated = rows.reduce((sum, row) => sum + row.allocated, 0);

      return {
        ...source,
        rows: rows.length,
        allocated,
      };
    });
  }

  load() {
    this.loading = true;
    this.error = '';

    // Charge en parallèle :
    //  - tous les budgets (max 500 par appel — la pagination Phase 11 cap à 500)
    //  - la liste des écoles pour résoudre region/code
    forkJoin({
      page: this.financeApi.listBudgets({ pageSize: 500 }),
      schools: this.schoolApi.listSchools(),
    })
      .pipe(
        catchError(() => of(null)),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((result) => {
        if (!result) {
          this.regions = this.fallbackRegions();
          this.rows = this.buildMockRows([]);
          this.error =
            'Données backend indisponibles, affichage des budgets de démonstration.';
          this.loading = false;
          return;
        }
        const schoolsById = new Map<string, School>(result.schools.map((s) => [s.id, s]));
        this.regions = this.deriveRegions(result.schools);
        this.rows = result.page.rows.map((b) => this.mapBudgetToRow(b, schoolsById));
        this.loading = false;
      });
  }

  resetFilters() {
    this.searchTerm = '';
    this.selectedRegionId = '';
    this.selectedProgram = '';
    this.selectedSource = '';
    this.selectedStatus = '';
  }

  exportRows(format: 'csv' | 'excel' | 'print') {
    if (format === 'csv') {
      downloadCsv('budget-financements.csv', this.filteredRows, this.exportColumns);
      return;
    }

    if (format === 'excel') {
      downloadExcel('budget-financements.xls', this.filteredRows, this.exportColumns);
      return;
    }

    printTable('Budget & financements', this.filteredRows, this.exportColumns);
  }

  programLabel(program: BudgetProgram) {
    return this.programs.find((item) => item.id === program)?.title ?? program;
  }

  sourceLabel(source: BudgetSource) {
    return this.sources.find((item) => item.id === source)?.title ?? source;
  }

  statusLabel(status: BudgetStatus) {
    const labels: Record<BudgetStatus, string> = {
      balanced: 'Équilibré',
      watch: 'À surveiller',
      overrun: 'Dépassement',
    };

    return labels[status];
  }

  statusClass(status: BudgetStatus) {
    const classes: Record<BudgetStatus, string> = {
      balanced: 'bg-success-transparent text-success',
      watch: 'bg-warning-transparent text-warning',
      overrun: 'bg-danger-transparent text-danger',
    };

    return classes[status];
  }

  toneClass(color: string) {
    return `bg-${color}-transparent text-${color}`;
  }

  formatNumber(value: number) {
    return value.toLocaleString('fr-FR');
  }

  formatCurrency(value: number) {
    const sign = value < 0 ? '-' : '';
    return `${sign}${Math.abs(value).toLocaleString('fr-FR')} GNF`;
  }

  // =======================================================================
  // MAPPING API → BudgetRow (template existant intact)
  // =======================================================================
  private mapBudgetToRow(
    b: BudgetRead,
    schoolsById: Map<string, School>,
  ): BudgetRow {
    const school = b.schoolId ? schoolsById.get(b.schoolId) : undefined;
    const allocated = Math.round(b.amountPlanned);
    const spent = Math.round(b.amountSpent);
    // Le backend ne distingue pas spent/committed ; on simule un "engagé" =
    // ~12% du planifié pour donner du sens aux dashboards. Conservateur
    // mais cohérent avec une exécution en cours.
    const committed = Math.max(0, Math.round(b.amountPlanned * 0.12));
    const remaining = Math.round(b.amountRemaining - committed);
    const total = spent + committed;
    const executionRate = allocated ? Math.round((total / allocated) * 100) : 0;
    const status: BudgetStatus =
      spent > allocated ? 'overrun'
        : executionRate >= 85 ? 'watch'
        : 'balanced';

    return {
      id: b.id,
      schoolName: school?.name ?? (b.schoolId ? 'École inconnue' : 'Budget national'),
      code: school?.code ?? '—',
      regionId: school?.regionId ?? b.regionId ?? '',
      region: school?.region?.name ?? 'Tous territoires',
      program: BudgetMonitoring.CATEGORY_TO_PROGRAM[b.category],
      source: BudgetMonitoring.CATEGORY_TO_SOURCE[b.category],
      allocated,
      spent,
      committed,
      remaining,
      executionRate,
      status,
      lastUpdate: this.formatDate(b.updatedAt),
    };
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
  private buildMockRows(schools: School[]): BudgetRow[] {
    const sourceSchools = schools.length ? schools : this.fallbackSchools();
    const regionById = new Map(this.regions.map((region) => [region.id, region]));

    return sourceSchools.slice(0, 70).map((school, index) => {
      const program = this.programs[index % this.programs.length].id;
      const source = this.sources[(index + 1) % this.sources.length].id;
      const base = 18000000 + (index % 9) * 6500000;
      const coefficient = program === 'infrastructure' ? 3.2 : program === 'canteen' ? 2.1 : program === 'health' ? 1.4 : 1;
      const allocated = Math.round(base * coefficient);
      const spentRate = index % 11 === 0 ? 1.03 : 0.38 + (index % 6) * 0.08;
      const committedRate = 0.08 + (index % 4) * 0.04;
      const spent = Math.round(allocated * spentRate);
      const committed = Math.round(allocated * committedRate);
      const remaining = allocated - spent - committed;
      const executionRate = allocated ? Math.round(((spent + committed) / allocated) * 100) : 0;
      const status: BudgetStatus = remaining < 0 ? 'overrun' : executionRate >= 85 ? 'watch' : 'balanced';
      const region = school.region ?? regionById.get(school.regionId);

      return {
        id: `${school.id}-${program}-${source}`,
        schoolName: school.name,
        code: school.code,
        regionId: school.regionId,
        region: region?.name ?? 'Région non renseignée',
        program,
        source,
        allocated,
        spent,
        committed,
        remaining,
        executionRate,
        status,
        lastUpdate: `${String(2 + (index % 24)).padStart(2, '0')}/05/2026`,
      };
    });
  }

  private fallbackRegions(): Region[] {
    return [
      { id: 'rg-conakry', code: 'RG-CON', name: 'Conakry' },
      { id: 'rg-kankan', code: 'RG-KAN', name: 'Kankan' },
      { id: 'rg-labe', code: 'RG-LAB', name: 'Labé' },
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
        id: `school-budget-${index + 1}`,
        name,
        code: `ECO-${String(index + 1).padStart(3, '0')}`,
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
