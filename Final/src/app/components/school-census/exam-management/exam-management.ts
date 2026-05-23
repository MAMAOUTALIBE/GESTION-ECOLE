import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { CensusApiService } from '../shared/census-api.service';
import { downloadCsv, downloadExcel, ExportColumn, printTable } from '../shared/export-utils';
import { Region, School } from '../shared/school-census.models';

type ExamStatus = 'scheduled' | 'collecting' | 'published';
type CenterStatus = 'ready' | 'watch' | 'blocked';

interface ExamSession {
  id: string;
  title: string;
  level: string;
  date: string;
  status: ExamStatus;
  coefficient: number;
}

interface ExamCenterRow {
  id: string;
  schoolName: string;
  code: string;
  regionId: string;
  region: string;
  prefecture: string;
  candidates: number;
  present: number;
  admitted: number;
  averageScore: number;
  incidents: number;
  status: CenterStatus;
}

@Component({
  selector: 'app-exam-management',
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './exam-management.html',
  styleUrl: './exam-management.scss',
})
export class ExamManagement {
  private censusApi = inject(CensusApiService);

  sessions: ExamSession[] = [
    {
      id: 'cee-2026',
      title: 'Certificat d’études élémentaires',
      level: 'Primaire',
      date: '2026-06-08',
      status: 'scheduled',
      coefficient: 1,
    },
    {
      id: 'bepc-2026',
      title: 'BEPC',
      level: 'Collège',
      date: '2026-06-22',
      status: 'collecting',
      coefficient: 2,
    },
    {
      id: 'bac-2026',
      title: 'Baccalauréat',
      level: 'Lycée',
      date: '2026-07-06',
      status: 'scheduled',
      coefficient: 3,
    },
  ];

  regions: Region[] = [];
  centerRows: ExamCenterRow[] = [];
  selectedSessionId = 'bepc-2026';
  selectedRegionId = '';
  selectedStatus = '';
  searchTerm = '';
  loading = false;
  error = '';

  private exportColumns: ExportColumn<ExamCenterRow>[] = [
    { header: 'Code centre', value: (row) => row.code },
    { header: 'Centre', value: (row) => row.schoolName },
    { header: 'Région', value: (row) => row.region },
    { header: 'Préfecture', value: (row) => row.prefecture },
    { header: 'Candidats', value: (row) => row.candidates },
    { header: 'Présents', value: (row) => row.present },
    { header: 'Admis', value: (row) => row.admitted },
    { header: 'Moyenne', value: (row) => this.formatScore(row.averageScore) },
    { header: 'Incidents', value: (row) => row.incidents },
    { header: 'Statut', value: (row) => this.centerStatusLabel(row.status) },
  ];

  ngOnInit() {
    this.load();
  }

  get selectedSession() {
    return this.sessions.find((session) => session.id === this.selectedSessionId) ?? this.sessions[0];
  }

  get filteredRows() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.centerRows.filter((row) => {
      const matchesRegion = !this.selectedRegionId || row.regionId === this.selectedRegionId;
      const matchesStatus = !this.selectedStatus || row.status === this.selectedStatus;
      const searchable = this.normalizeSearch([row.schoolName, row.code, row.region, row.prefecture].join(' '));

      return matchesRegion && matchesStatus && (!search || searchable.includes(search));
    });
  }

  get totals() {
    const rows = this.filteredRows;
    const candidates = rows.reduce((sum, row) => sum + row.candidates, 0);
    const present = rows.reduce((sum, row) => sum + row.present, 0);
    const admitted = rows.reduce((sum, row) => sum + row.admitted, 0);
    const incidents = rows.reduce((sum, row) => sum + row.incidents, 0);

    return {
      centers: rows.length,
      candidates,
      present,
      admitted,
      incidents,
      attendanceRate: candidates ? Math.round((present / candidates) * 100) : 0,
      successRate: present ? Math.round((admitted / present) * 100) : 0,
      averageScore: rows.length
        ? Math.round((rows.reduce((sum, row) => sum + row.averageScore, 0) / rows.length) * 10) / 10
        : 0,
    };
  }

  load() {
    this.loading = true;
    this.error = '';

    this.censusApi.metadata().subscribe({
      next: (metadata) => {
        this.regions = metadata.regions;
        this.centerRows = this.buildCenters(metadata.schools);
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger les centres d’examen.';
        this.loading = false;
      },
    });
  }

  onSessionChange() {
    this.load();
  }

  resetFilters() {
    this.selectedRegionId = '';
    this.selectedStatus = '';
    this.searchTerm = '';
  }

  exportRows(format: 'csv' | 'excel' | 'print') {
    const title = `Examens - ${this.selectedSession.title}`;

    if (format === 'csv') {
      downloadCsv('examens-centres.csv', this.filteredRows, this.exportColumns);
      return;
    }

    if (format === 'excel') {
      downloadExcel('examens-centres.xls', this.filteredRows, this.exportColumns);
      return;
    }

    printTable(title, this.filteredRows, this.exportColumns);
  }

  sessionStatusLabel(status: ExamStatus) {
    const labels: Record<ExamStatus, string> = {
      scheduled: 'Planifié',
      collecting: 'Collecte',
      published: 'Publié',
    };

    return labels[status];
  }

  sessionStatusClass(status: ExamStatus) {
    const classes: Record<ExamStatus, string> = {
      scheduled: 'bg-info-transparent text-info',
      collecting: 'bg-warning-transparent text-warning',
      published: 'bg-success-transparent text-success',
    };

    return classes[status];
  }

  centerStatusLabel(status: CenterStatus) {
    const labels: Record<CenterStatus, string> = {
      ready: 'Prêt',
      watch: 'À surveiller',
      blocked: 'Bloqué',
    };

    return labels[status];
  }

  centerStatusClass(status: CenterStatus) {
    const classes: Record<CenterStatus, string> = {
      ready: 'bg-success-transparent text-success',
      watch: 'bg-warning-transparent text-warning',
      blocked: 'bg-danger-transparent text-danger',
    };

    return classes[status];
  }

  formatNumber(value: number) {
    return value.toLocaleString('fr-FR');
  }

  formatScore(value: number) {
    return value.toLocaleString('fr-FR', { maximumFractionDigits: 1 });
  }

  private buildCenters(schools: School[]): ExamCenterRow[] {
    const sessionFactor = this.selectedSession.coefficient;

    return schools.slice(0, 18).map((school, index) => {
      const base = school.counts?.students ?? 80 + index * 9;
      const candidates = Math.max(Math.round(base * (0.18 + sessionFactor * 0.04)), 12);
      const present = Math.max(candidates - (index % 5), 0);
      const admitted = Math.round(present * (0.48 + (index % 6) * 0.05));
      const averageScore = Math.min(18, Math.round((9.5 + (index % 7) * 0.8 + sessionFactor * 0.35) * 10) / 10);
      const incidents = index % 6 === 0 ? 2 : index % 4 === 0 ? 1 : 0;
      const status: CenterStatus = incidents > 1 ? 'blocked' : incidents === 1 || present < candidates ? 'watch' : 'ready';

      return {
        id: school.id,
        schoolName: school.name,
        code: school.code,
        regionId: school.regionId,
        region: school.region?.name ?? 'Région non renseignée',
        prefecture: school.prefecture ?? 'Préfecture non renseignée',
        candidates,
        present,
        admitted,
        averageScore,
        incidents,
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
