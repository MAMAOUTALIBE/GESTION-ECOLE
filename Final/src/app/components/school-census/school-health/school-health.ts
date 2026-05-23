import { CommonModule } from '@angular/common';
import { Component, DestroyRef, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { catchError, forkJoin, of } from 'rxjs';
import { CensusApiService } from '../shared/census-api.service';
import { downloadCsv, downloadExcel, ExportColumn, printTable } from '../shared/export-utils';
import { HealthVisitRow, SchoolLifeApiService } from '../shared/schoollife-api.service';
import { Region, School } from '../shared/school-census.models';

type HealthStatus = 'good' | 'watch' | 'critical';

interface HealthRow {
  id: string;
  schoolName: string;
  code: string;
  regionId: string;
  region: string;
  prefecture: string;
  commune: string;
  studentsMonitored: number;
  screened: number;
  vaccinated: number;
  chronicCases: number;
  incidents: number;
  infirmary: boolean;
  firstAidKits: number;
  status: HealthStatus;
}

@Component({
  selector: 'app-school-health',
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './school-health.html',
  styleUrl: './school-health.scss',
})
export class SchoolHealth {
  private censusApi = inject(CensusApiService);
  private schoolLifeApi = inject(SchoolLifeApiService);
  private destroyRef = inject(DestroyRef);

  rows: HealthRow[] = [];
  regions: Region[] = [];
  loading = false;
  error = '';
  searchTerm = '';
  selectedRegionId = '';
  selectedStatus = '';
  selectedService = '';

  private exportColumns: ExportColumn<HealthRow>[] = [
    { header: 'Code école', value: (row) => row.code },
    { header: 'Établissement', value: (row) => row.schoolName },
    { header: 'Région', value: (row) => row.region },
    { header: 'Préfecture', value: (row) => row.prefecture },
    { header: 'Commune', value: (row) => row.commune },
    { header: 'Élèves suivis', value: (row) => row.studentsMonitored },
    { header: 'Dépistés', value: (row) => row.screened },
    { header: 'Vaccinés', value: (row) => row.vaccinated },
    { header: 'Cas chroniques', value: (row) => row.chronicCases },
    { header: 'Incidents', value: (row) => row.incidents },
    { header: 'Infirmerie', value: (row) => (row.infirmary ? 'Oui' : 'Non') },
    { header: 'Trousses', value: (row) => row.firstAidKits },
    { header: 'Statut', value: (row) => this.statusLabel(row.status) },
  ];

  ngOnInit() {
    this.load();
  }

  get filteredRows() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.rows.filter((row) => {
      const matchesRegion = !this.selectedRegionId || row.regionId === this.selectedRegionId;
      const matchesStatus = !this.selectedStatus || row.status === this.selectedStatus;
      const matchesService =
        !this.selectedService ||
        (this.selectedService === 'infirmary' && row.infirmary) ||
        (this.selectedService === 'missing-infirmary' && !row.infirmary) ||
        (this.selectedService === 'first-aid' && row.firstAidKits > 0) ||
        (this.selectedService === 'missing-first-aid' && row.firstAidKits === 0);
      const searchable = this.normalizeSearch(
        [row.schoolName, row.code, row.region, row.prefecture, row.commune].join(' '),
      );

      return matchesRegion && matchesStatus && matchesService && (!search || searchable.includes(search));
    });
  }

  get totals() {
    const rows = this.filteredRows;
    const studentsMonitored = rows.reduce((sum, row) => sum + row.studentsMonitored, 0);
    const screened = rows.reduce((sum, row) => sum + row.screened, 0);
    const vaccinated = rows.reduce((sum, row) => sum + row.vaccinated, 0);
    const chronicCases = rows.reduce((sum, row) => sum + row.chronicCases, 0);
    const incidents = rows.reduce((sum, row) => sum + row.incidents, 0);

    return {
      schools: rows.length,
      studentsMonitored,
      screened,
      vaccinated,
      chronicCases,
      incidents,
      screeningRate: studentsMonitored ? Math.round((screened / studentsMonitored) * 100) : 0,
      vaccinationRate: studentsMonitored ? Math.round((vaccinated / studentsMonitored) * 100) : 0,
      infirmaryCoverage: rows.length ? Math.round((rows.filter((row) => row.infirmary).length / rows.length) * 100) : 0,
      critical: rows.filter((row) => row.status === 'critical').length,
    };
  }

  load() {
    this.loading = true;
    this.error = '';

    forkJoin({
      metadata: this.censusApi.metadata(),
      visits: this.schoolLifeApi.listHealthVisits({ limit: 2000 }),
    })
      .pipe(catchError(() => of(null)), takeUntilDestroyed(this.destroyRef))
      .subscribe((result) => {
        if (!result) {
          this.error = 'Impossible de charger le suivi santé scolaire.';
          this.loading = false;
          return;
        }
        this.regions = result.metadata.regions;
        this.rows = this.buildHealthRows(result.metadata.schools, result.visits);
        this.loading = false;
      });
  }

  resetFilters() {
    this.searchTerm = '';
    this.selectedRegionId = '';
    this.selectedStatus = '';
    this.selectedService = '';
  }

  exportRows(format: 'csv' | 'excel' | 'print') {
    if (format === 'csv') {
      downloadCsv('sante-scolaire.csv', this.filteredRows, this.exportColumns);
      return;
    }

    if (format === 'excel') {
      downloadExcel('sante-scolaire.xls', this.filteredRows, this.exportColumns);
      return;
    }

    printTable('Santé scolaire', this.filteredRows, this.exportColumns);
  }

  statusLabel(status: HealthStatus) {
    const labels: Record<HealthStatus, string> = {
      good: 'Conforme',
      watch: 'À surveiller',
      critical: 'Critique',
    };

    return labels[status];
  }

  statusClass(status: HealthStatus) {
    const classes: Record<HealthStatus, string> = {
      good: 'bg-success-transparent text-success',
      watch: 'bg-warning-transparent text-warning',
      critical: 'bg-danger-transparent text-danger',
    };

    return classes[status];
  }

  formatNumber(value: number) {
    return value.toLocaleString('fr-FR');
  }

  rowRate(value: number, total: number) {
    return total ? Math.round((value / total) * 100) : 0;
  }

  private buildHealthRows(schools: School[], visits: HealthVisitRow[] = []): HealthRow[] {
    // Agrège les vraies visites par école
    const visitsBySchool = new Map<string, HealthVisitRow[]>();
    for (const v of visits) {
      const arr = visitsBySchool.get(v.schoolId) ?? [];
      arr.push(v);
      visitsBySchool.set(v.schoolId, arr);
    }
    return schools.map((school, index) => {
      const schl = visitsBySchool.get(school.id) ?? [];
      const studentsMonitored = Math.max(school.counts?.students ?? 1, 1);
      const screened = schl.filter((v) => v.type === 'CHECKUP').length;
      const vaccinated = schl.filter((v) => v.type === 'VACCINATION').length;
      const chronicCases = schl.filter((v) => v.type === 'ILLNESS').length;
      const incidents = schl.filter((v) => v.type === 'INJURY').length;
      // Infirmerie / trousses : pas dans le backend, on déduit du volume d'activité
      const infirmary = schl.length > 3;
      const firstAidKits = Math.min(3, Math.ceil(schl.length / 4));
      const screeningRate = this.rowRate(screened, studentsMonitored);
      const vaccinationRate = this.rowRate(vaccinated, studentsMonitored);
      const status: HealthStatus =
        !infirmary || firstAidKits === 0 || incidents > 2
          ? 'critical'
          : screeningRate < 75 || vaccinationRate < 75 || chronicCases > 6
            ? 'watch'
            : 'good';

      return {
        id: school.id,
        schoolName: school.name,
        code: school.code,
        regionId: school.regionId,
        region: school.region?.name ?? 'Région non renseignée',
        prefecture: school.prefecture ?? 'Préfecture non renseignée',
        commune: school.commune ?? 'Commune non renseignée',
        studentsMonitored,
        screened,
        vaccinated,
        chronicCases,
        incidents,
        infirmary,
        firstAidKits,
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
