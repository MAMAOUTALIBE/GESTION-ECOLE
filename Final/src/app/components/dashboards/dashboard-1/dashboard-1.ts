import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { ApexOptions } from 'ng-apexcharts';
import { SpkApexcharts } from '../../../@spk/charts/spk-apexcharts/spk-apexcharts';
import { SpkDashboardsCard } from '../../../@spk/spk-dashboards-card/spk-dashboards-card';
import { CensusApiService } from '../../school-census/shared/census-api.service';
import {
  AttendanceRecord,
  CensusDashboard,
  CensusMetadata,
  DashboardFilters,
  Region,
  School,
  TerritoryDashboardRow,
} from '../../school-census/shared/school-census.models';

interface DashboardMetric {
  label: string;
  value: string;
  detail: string;
  icon: string;
  color: string;
}

@Component({
  selector: 'app-dashboard-1',
  imports: [CommonModule, FormsModule, RouterModule, SpkDashboardsCard, SpkApexcharts],
  templateUrl: './dashboard-1.html',
  styleUrl: './dashboard-1.scss',
})
export class Dashboard1 {
  private api = inject(CensusApiService);

  loading = false;
  error = '';
  metadata?: CensusMetadata;
  dashboard?: CensusDashboard;
  filters: DashboardFilters = {};
  territoryMode: 'prefecture' | 'commune' = 'prefecture';
  territoryRows: TerritoryDashboardRow[] = [];
  recentAttendances: AttendanceRecord[] = [];
  ministryMetrics: DashboardMetric[] = [];
  qualityItems: DashboardMetric[] = [];
  cards = [
    this.card('Élèves recensés', '0', 'Total validé', 'users', 'primary'),
    this.card('Enseignants', '0', 'Total validé', 'briefcase', 'info'),
    this.card('Écoles', '0', 'Périmètre actif', 'home', 'secondary'),
    this.card('Classes', '0', 'Capacité déclarée', 'grid', 'success'),
  ];

  regionChart: ApexOptions = {
    series: [],
    chart: { type: 'bar', height: 320, toolbar: { show: false } },
    xaxis: { categories: [] },
  };

  ngOnInit() {
    this.loadMetadata();
    this.loadDashboard();
  }

  loadMetadata() {
    this.api.metadata().subscribe({
      next: (metadata) => {
        this.metadata = metadata;
      },
    });
  }

  loadDashboard() {
    this.loading = true;
    this.error = '';

    this.api.dashboard(this.filters).subscribe({
      next: (dashboard) => {
        this.applyDashboard(dashboard);
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger le tableau de bord.';
        this.loading = false;
      },
    });
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

  setTerritoryMode(mode: 'prefecture' | 'commune') {
    this.territoryMode = mode;
    this.updateTerritoryRows();
  }

  private applyDashboard(dashboard: CensusDashboard) {
    this.dashboard = dashboard;
    this.updateTerritoryRows();
    this.cards = [
      this.card('Élèves recensés', dashboard.totals.students.toLocaleString('fr-FR'), 'Total validé', 'users', 'primary'),
      this.card('Enseignants', dashboard.totals.teachers.toLocaleString('fr-FR'), 'Total validé', 'briefcase', 'info'),
      this.card('Écoles', dashboard.totals.schools.toLocaleString('fr-FR'), 'Périmètre actif', 'home', 'secondary'),
      this.card('Classes', dashboard.totals.classes.toLocaleString('fr-FR'), 'Capacité déclarée', 'grid', 'success'),
    ];
    this.ministryMetrics = [
      {
        label: 'Personnes enregistrées',
        value: dashboard.totals.registeredPeople.toLocaleString('fr-FR'),
        detail: 'Élèves et enseignants consolidés',
        icon: 'ri-database-2-line',
        color: 'primary',
      },
      {
        label: 'Ratio élèves / enseignant',
        value: this.formatRatio(dashboard.ratios.studentsPerTeacher),
        detail: 'Indicateur de charge pédagogique',
        icon: 'ri-scales-3-line',
        color: 'info',
      },
      {
        label: 'Présences du jour',
        value: dashboard.totals.presentToday.toLocaleString('fr-FR'),
        detail: `${dashboard.totals.attendanceToday.toLocaleString('fr-FR')} pointage(s) QR aujourd’hui`,
        icon: 'ri-qr-scan-2-line',
        color: 'success',
      },
      {
        label: 'Qualité des données',
        value: `${dashboard.dataQuality.score}%`,
        detail: 'Complétude des champs critiques',
        icon: 'ri-shield-check-line',
        color: this.qualityColor(dashboard.dataQuality.score),
      },
    ];
    this.qualityItems = [
      {
        label: 'Élèves sans classe',
        value: dashboard.dataQuality.studentsWithoutClass.toLocaleString('fr-FR'),
        detail: 'À affecter avant validation officielle',
        icon: 'ri-team-line',
        color: dashboard.dataQuality.studentsWithoutClass ? 'warning' : 'success',
      },
      {
        label: 'Élèves sans photo',
        value: dashboard.dataQuality.studentsWithoutPhoto.toLocaleString('fr-FR'),
        detail: 'Cartes scolaires incomplètes',
        icon: 'ri-image-line',
        color: dashboard.dataQuality.studentsWithoutPhoto ? 'warning' : 'success',
      },
      {
        label: 'Enseignants sans classe',
        value: dashboard.dataQuality.teachersWithoutClasses.toLocaleString('fr-FR'),
        detail: 'Affectation pédagogique manquante',
        icon: 'ri-briefcase-4-line',
        color: dashboard.dataQuality.teachersWithoutClasses ? 'warning' : 'success',
      },
      {
        label: 'Écoles sans GPS',
        value: dashboard.dataQuality.schoolsWithoutCoordinates.toLocaleString('fr-FR'),
        detail: 'Carte scolaire nationale à compléter',
        icon: 'ri-map-pin-line',
        color: dashboard.dataQuality.schoolsWithoutCoordinates ? 'info' : 'success',
      },
    ];
    this.recentAttendances = dashboard.recentAttendances;
    this.regionChart = {
      series: [
        {
          name: 'Élèves',
          data: dashboard.byRegion.map((region) => region.students),
        },
        {
          name: 'Enseignants',
          data: dashboard.byRegion.map((region) => region.teachers),
        },
      ],
      chart: { type: 'bar', height: 320, toolbar: { show: false } },
      colors: ['var(--primary-color)', '#23b7e5'],
      dataLabels: { enabled: false },
      grid: { borderColor: '#f2f6f7' },
      legend: { show: true, position: 'top' },
      plotOptions: {
        bar: {
          columnWidth: '38%',
          borderRadius: 4,
        },
      },
      xaxis: {
        categories: dashboard.byRegion.map((region) => region.name),
        labels: { rotate: -30 },
      },
      yaxis: {
        labels: {
          formatter: (value) => value.toFixed(0),
        },
      },
    };
  }

  formatNumber(value?: number | null) {
    return (value ?? 0).toLocaleString('fr-FR');
  }

  formatRatio(value?: number | null) {
    return value ? value.toLocaleString('fr-FR', { maximumFractionDigits: 1 }) : 'N/A';
  }

  alertClass(level: string) {
    return `alert-${level}`;
  }

  metricClass(color: string) {
    return `bg-${color}-transparent text-${color}`;
  }

  qualityColor(score: number) {
    if (score >= 90) {
      return 'success';
    }
    if (score >= 70) {
      return 'warning';
    }
    return 'danger';
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

  private card(title: string, value: string, status: string, icon: string, color: string) {
    return {
      title,
      value,
      status,
      sales: '',
      icon,
      iconBg: color,
      Bgcolor: color,
      direction: 'up',
      textcolor: 'success',
    };
  }
}
