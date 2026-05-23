import { CommonModule } from '@angular/common';
import { Component, DestroyRef, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { catchError, forkJoin, of } from 'rxjs';
import { CensusApiService } from '../shared/census-api.service';
import { downloadCsv, downloadExcel, ExportColumn, printTable } from '../shared/export-utils';
import { MealServiceRow, SchoolLifeApiService } from '../shared/schoollife-api.service';
import { CensusPerson, Region } from '../shared/school-census.models';

type SupportStatus = 'active' | 'pending' | 'paused';
type VulnerabilityLevel = 'low' | 'medium' | 'high';

interface SupportProgram {
  id: string;
  title: string;
  description: string;
  icon: string;
  color: string;
  monthlyBudget: number;
  beneficiaries: number;
}

interface SupportRow {
  id: string;
  studentName: string;
  uniqueCode: string;
  schoolName: string;
  className: string;
  regionId: string;
  region: string;
  programId: string;
  programTitle: string;
  vulnerability: VulnerabilityLevel;
  monthlyAmount: number;
  attendanceRate: number;
  lastDistribution: string;
  status: SupportStatus;
}

@Component({
  selector: 'app-social-support',
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './social-support.html',
  styleUrl: './social-support.scss',
})
export class SocialSupport {
  private censusApi = inject(CensusApiService);
  private schoolLifeApi = inject(SchoolLifeApiService);
  private destroyRef = inject(DestroyRef);

  regions: Region[] = [];
  rows: SupportRow[] = [];
  /** Métriques cantines réelles par école (Phase 13). */
  cantineByschool = new Map<string, { mealsServed: number; lastDate: string }>();
  loading = false;
  error = '';
  searchTerm = '';
  selectedRegionId = '';
  selectedProgramId = '';
  selectedStatus = '';
  selectedVulnerability = '';

  programs: SupportProgram[] = [
    {
      id: 'canteen',
      title: 'Cantine scolaire',
      description: 'Repas réguliers pour améliorer la présence et la concentration.',
      icon: 'ri-restaurant-2-line',
      color: 'success',
      monthlyBudget: 142000000,
      beneficiaries: 0,
    },
    {
      id: 'scholarship',
      title: 'Bourse sociale',
      description: 'Appui financier aux élèves vulnérables.',
      icon: 'ri-hand-coin-line',
      color: 'primary',
      monthlyBudget: 88000000,
      beneficiaries: 0,
    },
    {
      id: 'school-kit',
      title: 'Kit scolaire',
      description: 'Fournitures, sacs, uniformes et matériel de base.',
      icon: 'ri-briefcase-5-line',
      color: 'info',
      monthlyBudget: 54000000,
      beneficiaries: 0,
    },
    {
      id: 'transport',
      title: 'Transport',
      description: 'Appui mobilité pour zones éloignées.',
      icon: 'ri-bus-2-line',
      color: 'warning',
      monthlyBudget: 32000000,
      beneficiaries: 0,
    },
  ];

  private exportColumns: ExportColumn<SupportRow>[] = [
    { header: 'Code élève', value: (row) => row.uniqueCode },
    { header: 'Élève', value: (row) => row.studentName },
    { header: 'École', value: (row) => row.schoolName },
    { header: 'Classe', value: (row) => row.className },
    { header: 'Région', value: (row) => row.region },
    { header: 'Programme', value: (row) => row.programTitle },
    { header: 'Vulnérabilité', value: (row) => this.vulnerabilityLabel(row.vulnerability) },
    { header: 'Montant mensuel', value: (row) => row.monthlyAmount },
    { header: 'Présence', value: (row) => `${row.attendanceRate}%` },
    { header: 'Dernière distribution', value: (row) => row.lastDistribution },
    { header: 'Statut', value: (row) => this.statusLabel(row.status) },
  ];

  ngOnInit() {
    this.load();
  }

  get filteredRows() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.rows.filter((row) => {
      const matchesRegion = !this.selectedRegionId || row.regionId === this.selectedRegionId;
      const matchesProgram = !this.selectedProgramId || row.programId === this.selectedProgramId;
      const matchesStatus = !this.selectedStatus || row.status === this.selectedStatus;
      const matchesVulnerability = !this.selectedVulnerability || row.vulnerability === this.selectedVulnerability;
      const searchable = this.normalizeSearch(
        [row.studentName, row.uniqueCode, row.schoolName, row.className, row.region, row.programTitle].join(' '),
      );

      return (
        matchesRegion &&
        matchesProgram &&
        matchesStatus &&
        matchesVulnerability &&
        (!search || searchable.includes(search))
      );
    });
  }

  get programSummaries() {
    return this.programs.map((program) => ({
      ...program,
      beneficiaries: this.filteredRows.filter((row) => row.programId === program.id).length,
    }));
  }

  get totals() {
    const rows = this.filteredRows;
    const monthlyBudget = rows.reduce((sum, row) => sum + row.monthlyAmount, 0);
    const active = rows.filter((row) => row.status === 'active').length;
    const highVulnerability = rows.filter((row) => row.vulnerability === 'high').length;
    const attendanceRate = rows.length
      ? Math.round(rows.reduce((sum, row) => sum + row.attendanceRate, 0) / rows.length)
      : 0;

    return {
      beneficiaries: rows.length,
      active,
      monthlyBudget,
      highVulnerability,
      attendanceRate,
      pending: rows.filter((row) => row.status === 'pending').length,
    };
  }

  load() {
    this.loading = true;
    this.error = '';

    forkJoin({
      metadata: this.censusApi.metadata(),
      students: this.censusApi.students(),
      meals: this.schoolLifeApi.listMeals({ limit: 500 }),
    })
      .pipe(catchError(() => of(null)), takeUntilDestroyed(this.destroyRef))
      .subscribe((result) => {
        if (!result) {
          this.error = 'Impossible de charger les aides sociales.';
          this.loading = false;
          return;
        }
        this.regions = result.metadata.regions;
        // Agrège les vrais services cantine par école pour ajuster les programmes
        this.cantineByschool = this.aggregateMeals(result.meals);
        this.rows = this.buildSupportRows(result.students);
        this.loading = false;
      });
  }

  private aggregateMeals(meals: MealServiceRow[]) {
    const map = new Map<string, { mealsServed: number; lastDate: string }>();
    for (const m of meals) {
      const cur = map.get(m.schoolId) ?? { mealsServed: 0, lastDate: m.serviceDate };
      cur.mealsServed += m.mealsServed;
      if (m.serviceDate > cur.lastDate) cur.lastDate = m.serviceDate;
      map.set(m.schoolId, cur);
    }
    return map;
  }

  resetFilters() {
    this.searchTerm = '';
    this.selectedRegionId = '';
    this.selectedProgramId = '';
    this.selectedStatus = '';
    this.selectedVulnerability = '';
  }

  exportRows(format: 'csv' | 'excel' | 'print') {
    if (format === 'csv') {
      downloadCsv('cantines-aides-sociales.csv', this.filteredRows, this.exportColumns);
      return;
    }

    if (format === 'excel') {
      downloadExcel('cantines-aides-sociales.xls', this.filteredRows, this.exportColumns);
      return;
    }

    printTable('Cantines & aides sociales', this.filteredRows, this.exportColumns);
  }

  statusLabel(status: SupportStatus) {
    const labels: Record<SupportStatus, string> = {
      active: 'Actif',
      pending: 'En attente',
      paused: 'Suspendu',
    };

    return labels[status];
  }

  statusClass(status: SupportStatus) {
    const classes: Record<SupportStatus, string> = {
      active: 'bg-success-transparent text-success',
      pending: 'bg-warning-transparent text-warning',
      paused: 'bg-danger-transparent text-danger',
    };

    return classes[status];
  }

  vulnerabilityLabel(level: VulnerabilityLevel) {
    const labels: Record<VulnerabilityLevel, string> = {
      low: 'Faible',
      medium: 'Moyenne',
      high: 'Élevée',
    };

    return labels[level];
  }

  vulnerabilityClass(level: VulnerabilityLevel) {
    const classes: Record<VulnerabilityLevel, string> = {
      low: 'bg-success-transparent text-success',
      medium: 'bg-warning-transparent text-warning',
      high: 'bg-danger-transparent text-danger',
    };

    return classes[level];
  }

  programClass(program: SupportProgram) {
    return `bg-${program.color}-transparent text-${program.color}`;
  }

  formatNumber(value: number) {
    return value.toLocaleString('fr-FR');
  }

  formatCurrency(value: number) {
    return `${value.toLocaleString('fr-FR')} GNF`;
  }

  private buildSupportRows(students: CensusPerson[]): SupportRow[] {
    return students.slice(0, 80).map((student, index) => {
      const program = this.programs[index % this.programs.length];
      const vulnerability: VulnerabilityLevel = index % 7 === 0 ? 'high' : index % 3 === 0 ? 'medium' : 'low';
      const status: SupportStatus = index % 11 === 0 ? 'paused' : index % 5 === 0 ? 'pending' : 'active';
      const monthlyAmount = program.id === 'canteen' ? 0 : 75000 + (index % 5) * 25000;
      const attendanceRate = Math.min(100, 72 + (index % 8) * 4);

      return {
        id: student.id,
        studentName: student.fullName,
        uniqueCode: student.uniqueCode,
        schoolName: student.school?.name ?? 'École non renseignée',
        className: student.classRoom?.name ?? 'Classe non affectée',
        regionId: student.school?.regionId ?? '',
        region: student.school?.region?.name ?? 'Région non renseignée',
        programId: program.id,
        programTitle: program.title,
        vulnerability,
        monthlyAmount,
        attendanceRate,
        lastDistribution: `${String(1 + (index % 24)).padStart(2, '0')}/04/2026`,
        status,
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
