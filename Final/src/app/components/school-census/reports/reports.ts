import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { CensusApiService } from '../shared/census-api.service';
import { downloadCsv, downloadExcel, ExportColumn, printTable } from '../shared/export-utils';
import {
  CensusDashboard,
  CensusMetadata,
  DashboardFilters,
  Region,
  School,
  TerritoryDashboardRow,
} from '../shared/school-census.models';

type TerritoryMode = 'prefecture' | 'commune';
type TopSchool = CensusDashboard['topSchools'][number];

interface ReportKpi {
  label: string;
  value: string;
  detail: string;
  icon: string;
  color: string;
}

interface QualityRow {
  label: string;
  value: number;
  status: 'success' | 'warning' | 'danger' | 'info';
  action: string;
}

@Component({
  selector: 'app-reports',
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './reports.html',
  styleUrl: './reports.scss',
})
export class Reports {
  private censusApi = inject(CensusApiService);

  dashboard?: CensusDashboard;
  metadata?: CensusMetadata;
  filters: DashboardFilters = {};
  territoryMode: TerritoryMode = 'prefecture';
  territoryRows: TerritoryDashboardRow[] = [];
  generatedAt = new Date();
  loading = false;
  error = '';

  private territoryColumns: ExportColumn<TerritoryDashboardRow>[] = [
    { header: 'Territoire', value: (row) => row.name },
    { header: 'Région', value: (row) => row.region.name },
    { header: 'Écoles', value: (row) => row.schools },
    { header: 'Élèves', value: (row) => row.students },
    { header: 'Enseignants', value: (row) => row.teachers },
    { header: 'Classes', value: (row) => row.classes },
    { header: 'Ratio élèves/enseignant', value: (row) => this.formatRatio(row.studentsPerTeacher) },
    { header: 'Couverture GPS', value: (row) => `${row.gpsCoverageRate}%` },
  ];

  private topSchoolColumns: ExportColumn<TopSchool>[] = [
    { header: 'Établissement', value: (row) => row.name },
    { header: 'Code', value: (row) => row.code },
    { header: 'Région', value: (row) => row.region.name },
    { header: 'Élèves', value: (row) => row.students },
    { header: 'Enseignants', value: (row) => row.teachers },
    { header: 'Classes', value: (row) => row.classes },
  ];

  ngOnInit() {
    this.loadMetadata();
    this.loadDashboard();
  }

  get regions(): Region[] {
    return this.metadata?.regions ?? [];
  }

  get schools(): School[] {
    return this.metadata?.schools ?? [];
  }

  get availablePrefectures(): string[] {
    return this.uniqueTerritoryValues(
      this.schools.filter((school) => !this.filters.regionId || school.regionId === this.filters.regionId),
      'prefecture',
    );
  }

  get availableCommunes(): string[] {
    return this.uniqueTerritoryValues(
      this.schools.filter(
        (school) =>
          (!this.filters.regionId || school.regionId === this.filters.regionId) &&
          (!this.filters.prefecture || school.prefecture === this.filters.prefecture),
      ),
      'commune',
    );
  }

  get availableSchools(): School[] {
    return this.schools
      .filter(
        (school) =>
          (!this.filters.regionId || school.regionId === this.filters.regionId) &&
          (!this.filters.prefecture || school.prefecture === this.filters.prefecture) &&
          (!this.filters.commune || school.commune === this.filters.commune),
      )
      .sort((left, right) => left.name.localeCompare(right.name, 'fr-FR'));
  }

  get reportScope() {
    if (this.filters.schoolId) {
      return this.availableSchools.find((school) => school.id === this.filters.schoolId)?.name ?? 'Établissement';
    }
    if (this.filters.commune) {
      return this.filters.commune;
    }
    if (this.filters.prefecture) {
      return this.filters.prefecture;
    }
    if (this.filters.regionId) {
      return this.regions.find((region) => region.id === this.filters.regionId)?.name ?? 'Région';
    }
    return 'National';
  }

  get kpis(): ReportKpi[] {
    const data = this.dashboard;

    if (!data) {
      return [
        this.kpi('Élèves', '0', 'Effectif consolidé', 'ri-graduation-cap-line', 'primary'),
        this.kpi('Enseignants', '0', 'Personnel recensé', 'ri-briefcase-4-line', 'info'),
        this.kpi('Écoles', '0', 'Établissements actifs', 'ri-school-line', 'secondary'),
        this.kpi('Qualité', '0%', 'Complétude des données', 'ri-shield-check-line', 'warning'),
      ];
    }

    return [
      this.kpi('Élèves', this.formatNumber(data.totals.students), 'Effectif consolidé', 'ri-graduation-cap-line', 'primary'),
      this.kpi('Enseignants', this.formatNumber(data.totals.teachers), 'Personnel recensé', 'ri-briefcase-4-line', 'info'),
      this.kpi('Écoles', this.formatNumber(data.totals.schools), 'Établissements actifs', 'ri-school-line', 'secondary'),
      this.kpi('Qualité', `${data.dataQuality.score}%`, 'Complétude des données', 'ri-shield-check-line', this.qualityColor(data.dataQuality.score)),
      this.kpi('Présents du jour', this.formatNumber(data.totals.presentToday), 'Présences QR validées', 'ri-calendar-check-line', 'success'),
      this.kpi('Ratio E/E', this.formatRatio(data.ratios.studentsPerTeacher), 'Élèves par enseignant', 'ri-scales-3-line', 'warning'),
    ];
  }

  get qualityRows(): QualityRow[] {
    const quality = this.dashboard?.dataQuality;

    if (!quality) {
      return [];
    }

    return [
      {
        label: 'Élèves sans classe',
        value: quality.studentsWithoutClass,
        status: quality.studentsWithoutClass ? 'warning' : 'success',
        action: 'Affecter les élèves aux classes',
      },
      {
        label: 'Élèves sans photo',
        value: quality.studentsWithoutPhoto,
        status: quality.studentsWithoutPhoto ? 'warning' : 'success',
        action: 'Compléter les cartes scolaires',
      },
      {
        label: 'Enseignants sans classe',
        value: quality.teachersWithoutClasses,
        status: quality.teachersWithoutClasses ? 'warning' : 'success',
        action: 'Finaliser les affectations',
      },
      {
        label: 'Écoles sans GPS',
        value: quality.schoolsWithoutCoordinates,
        status: quality.schoolsWithoutCoordinates ? 'info' : 'success',
        action: 'Mettre à jour la carte scolaire',
      },
      {
        label: 'Écoles sans téléphone',
        value: quality.schoolsMissingPhone,
        status: quality.schoolsMissingPhone ? 'info' : 'success',
        action: 'Compléter les contacts',
      },
    ];
  }

  get topSchools(): TopSchool[] {
    return this.dashboard?.topSchools ?? [];
  }

  loadMetadata() {
    this.censusApi.metadata().subscribe({
      next: (metadata) => {
        this.metadata = metadata;
      },
    });
  }

  loadDashboard() {
    this.loading = true;
    this.error = '';

    this.censusApi.dashboard(this.filters).subscribe({
      next: (dashboard) => {
        this.dashboard = dashboard;
        this.generatedAt = new Date();
        this.updateTerritoryRows();
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger les rapports officiels.';
        this.loading = false;
      },
    });
  }

  onRegionChange() {
    this.filters.prefecture = '';
    this.filters.commune = '';
    this.filters.schoolId = '';
    this.loadDashboard();
  }

  onPrefectureChange() {
    this.filters.commune = '';
    this.filters.schoolId = '';
    this.loadDashboard();
  }

  onCommuneChange() {
    this.filters.schoolId = '';
    this.loadDashboard();
  }

  resetFilters() {
    this.filters = {};
    this.loadDashboard();
  }

  setTerritoryMode(mode: TerritoryMode) {
    this.territoryMode = mode;
    this.updateTerritoryRows();
  }

  exportTerritories(format: 'csv' | 'excel' | 'print') {
    const title = `Rapport territorial - ${this.reportScope}`;

    if (format === 'csv') {
      downloadCsv(this.fileName('rapport-territorial', 'csv'), this.territoryRows, this.territoryColumns);
      return;
    }

    if (format === 'excel') {
      downloadExcel(this.fileName('rapport-territorial', 'xls'), this.territoryRows, this.territoryColumns);
      return;
    }

    printTable(title, this.territoryRows, this.territoryColumns);
  }

  exportTopSchools(format: 'csv' | 'excel' | 'print') {
    const title = `Rapport établissements - ${this.reportScope}`;

    if (format === 'csv') {
      downloadCsv(this.fileName('rapport-etablissements', 'csv'), this.topSchools, this.topSchoolColumns);
      return;
    }

    if (format === 'excel') {
      downloadExcel(this.fileName('rapport-etablissements', 'xls'), this.topSchools, this.topSchoolColumns);
      return;
    }

    printTable(title, this.topSchools, this.topSchoolColumns);
  }

  statusClass(status: QualityRow['status']) {
    return `bg-${status}-transparent text-${status}`;
  }

  formatNumber(value?: number | null) {
    return (value ?? 0).toLocaleString('fr-FR');
  }

  formatRatio(value?: number | null) {
    return value ? value.toLocaleString('fr-FR', { maximumFractionDigits: 1 }) : 'N/A';
  }

  private updateTerritoryRows() {
    if (!this.dashboard) {
      this.territoryRows = [];
      return;
    }

    this.territoryRows =
      this.territoryMode === 'prefecture' ? this.dashboard.byPrefecture : this.dashboard.byCommune;
  }

  private uniqueTerritoryValues(schools: School[], field: 'prefecture' | 'commune') {
    return Array.from(new Set(schools.map((school) => school[field]).filter(Boolean) as string[])).sort((left, right) =>
      left.localeCompare(right, 'fr-FR'),
    );
  }

  private qualityColor(score: number) {
    if (score >= 90) {
      return 'success';
    }
    if (score >= 70) {
      return 'warning';
    }
    return 'danger';
  }

  private fileName(prefix: string, extension: string) {
    const date = this.generatedAt.toISOString().slice(0, 10);
    const scope = this.reportScope
      .toLocaleLowerCase('fr-FR')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '');

    return `${prefix}-${scope || 'national'}-${date}.${extension}`;
  }

  private kpi(label: string, value: string, detail: string, icon: string, color: string): ReportKpi {
    return { label, value, detail, icon, color };
  }
}
