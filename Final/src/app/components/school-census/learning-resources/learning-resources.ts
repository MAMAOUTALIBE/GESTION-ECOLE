import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { forkJoin } from 'rxjs';
import { AcademicsApiService } from '../shared/academics-api.service';
import { CensusApiService } from '../shared/census-api.service';
import { downloadCsv, downloadExcel, ExportColumn, printTable } from '../shared/export-utils';
import { Region, School, Subject } from '../shared/school-census.models';

type ResourceType = 'textbook' | 'kit' | 'digital' | 'lab';
type ResourceStatus = 'sufficient' | 'watch' | 'shortage';

interface ResourceRow {
  id: string;
  schoolName: string;
  code: string;
  regionId: string;
  region: string;
  level: string;
  subject: string;
  type: ResourceType;
  available: number;
  required: number;
  coverageRate: number;
  lastInventory: string;
  status: ResourceStatus;
}

@Component({
  selector: 'app-learning-resources',
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './learning-resources.html',
  styleUrl: './learning-resources.scss',
})
export class LearningResources {
  private academicsApi = inject(AcademicsApiService);
  private censusApi = inject(CensusApiService);

  regions: Region[] = [];
  rows: ResourceRow[] = [];
  loading = false;
  error = '';
  searchTerm = '';
  selectedRegionId = '';
  selectedType = '';
  selectedStatus = '';
  selectedSubject = '';

  resourceTypes: Array<{ value: ResourceType; label: string; icon: string; color: string }> = [
    { value: 'textbook', label: 'Manuels', icon: 'ri-book-2-line', color: 'primary' },
    { value: 'kit', label: 'Kits pédagogiques', icon: 'ri-briefcase-5-line', color: 'info' },
    { value: 'digital', label: 'Numérique', icon: 'ri-computer-line', color: 'success' },
    { value: 'lab', label: 'Laboratoires', icon: 'ri-flask-line', color: 'warning' },
  ];

  private exportColumns: ExportColumn<ResourceRow>[] = [
    { header: 'Code école', value: (row) => row.code },
    { header: 'Établissement', value: (row) => row.schoolName },
    { header: 'Région', value: (row) => row.region },
    { header: 'Niveau', value: (row) => row.level },
    { header: 'Matière', value: (row) => row.subject },
    { header: 'Type', value: (row) => this.typeLabel(row.type) },
    { header: 'Disponible', value: (row) => row.available },
    { header: 'Besoin', value: (row) => row.required },
    { header: 'Couverture', value: (row) => `${row.coverageRate}%` },
    { header: 'Dernier inventaire', value: (row) => row.lastInventory },
    { header: 'Statut', value: (row) => this.statusLabel(row.status) },
  ];

  ngOnInit() {
    this.load();
  }

  get subjects() {
    return Array.from(new Set(this.rows.map((row) => row.subject))).sort((left, right) =>
      left.localeCompare(right, 'fr-FR'),
    );
  }

  get filteredRows() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.rows.filter((row) => {
      const matchesRegion = !this.selectedRegionId || row.regionId === this.selectedRegionId;
      const matchesType = !this.selectedType || row.type === this.selectedType;
      const matchesStatus = !this.selectedStatus || row.status === this.selectedStatus;
      const matchesSubject = !this.selectedSubject || row.subject === this.selectedSubject;
      const searchable = this.normalizeSearch(
        [row.schoolName, row.code, row.region, row.level, row.subject, this.typeLabel(row.type)].join(' '),
      );

      return (
        matchesRegion &&
        matchesType &&
        matchesStatus &&
        matchesSubject &&
        (!search || searchable.includes(search))
      );
    });
  }

  get totals() {
    const rows = this.filteredRows;
    const available = rows.reduce((sum, row) => sum + row.available, 0);
    const required = rows.reduce((sum, row) => sum + row.required, 0);

    return {
      lines: rows.length,
      available,
      required,
      missing: Math.max(required - available, 0),
      coverageRate: required ? Math.round((available / required) * 100) : 0,
      shortage: rows.filter((row) => row.status === 'shortage').length,
      digital: rows.filter((row) => row.type === 'digital').length,
    };
  }

  load() {
    this.loading = true;
    this.error = '';

    forkJoin({
      metadata: this.censusApi.metadata(),
      subjects: this.academicsApi.listSubjects(),
    }).subscribe({
      next: ({ metadata, subjects }) => {
        this.regions = metadata.regions;
        this.rows = this.buildResourceRows(metadata.schools, subjects);
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger les ressources pédagogiques.';
        this.loading = false;
      },
    });
  }

  resetFilters() {
    this.searchTerm = '';
    this.selectedRegionId = '';
    this.selectedType = '';
    this.selectedStatus = '';
    this.selectedSubject = '';
  }

  exportRows(format: 'csv' | 'excel' | 'print') {
    if (format === 'csv') {
      downloadCsv('ressources-pedagogiques.csv', this.filteredRows, this.exportColumns);
      return;
    }

    if (format === 'excel') {
      downloadExcel('ressources-pedagogiques.xls', this.filteredRows, this.exportColumns);
      return;
    }

    printTable('Ressources pédagogiques', this.filteredRows, this.exportColumns);
  }

  typeLabel(type: ResourceType) {
    return this.resourceTypes.find((item) => item.value === type)?.label ?? type;
  }

  typeClass(type: ResourceType) {
    const color = this.resourceTypes.find((item) => item.value === type)?.color ?? 'primary';
    return `bg-${color}-transparent text-${color}`;
  }

  statusLabel(status: ResourceStatus) {
    const labels: Record<ResourceStatus, string> = {
      sufficient: 'Suffisant',
      watch: 'À surveiller',
      shortage: 'Manque',
    };

    return labels[status];
  }

  statusClass(status: ResourceStatus) {
    const classes: Record<ResourceStatus, string> = {
      sufficient: 'bg-success-transparent text-success',
      watch: 'bg-warning-transparent text-warning',
      shortage: 'bg-danger-transparent text-danger',
    };

    return classes[status];
  }

  formatNumber(value: number) {
    return value.toLocaleString('fr-FR');
  }

  private buildResourceRows(schools: School[], subjects: Subject[]): ResourceRow[] {
    const subjectNames = subjects.length
      ? subjects.map((subject) => subject.name)
      : ['Français', 'Mathématiques', 'Sciences', 'Histoire-Géographie'];
    const levels = ['Primaire', 'Collège', 'Lycée'];

    return schools.slice(0, 30).flatMap((school, schoolIndex) =>
      this.resourceTypes.slice(0, 3).map((type, typeIndex) => {
        const required = Math.max((school.counts?.students ?? 60 + schoolIndex * 8) + typeIndex * 15, 20);
        const available = Math.round(required * (0.55 + ((schoolIndex + typeIndex) % 6) * 0.08));
        const coverageRate = required ? Math.round((available / required) * 100) : 0;
        const status: ResourceStatus =
          coverageRate >= 90 ? 'sufficient' : coverageRate >= 70 ? 'watch' : 'shortage';

        return {
          id: `${school.id}-${type.value}`,
          schoolName: school.name,
          code: school.code,
          regionId: school.regionId,
          region: school.region?.name ?? 'Région non renseignée',
          level: levels[(schoolIndex + typeIndex) % levels.length],
          subject: subjectNames[(schoolIndex + typeIndex) % subjectNames.length],
          type: type.value,
          available,
          required,
          coverageRate,
          lastInventory: `${String(1 + ((schoolIndex + typeIndex) % 25)).padStart(2, '0')}/04/2026`,
          status,
        };
      }),
    );
  }

  private normalizeSearch(value?: string | null) {
    return (value ?? '')
      .toLocaleLowerCase('fr-FR')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '');
  }
}
