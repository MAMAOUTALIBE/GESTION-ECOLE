import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { downloadCsv, downloadExcel, ExportColumn, printTable } from '../shared/export-utils';

type CalendarType = 'academic' | 'exam' | 'holiday' | 'inspection' | 'deadline';
type CalendarStatus = 'planned' | 'active' | 'completed' | 'delayed';

interface CalendarItem {
  id: string;
  title: string;
  type: CalendarType;
  status: CalendarStatus;
  scope: string;
  startDate: string;
  endDate: string;
  owner: string;
  progress: number;
  priority: 'low' | 'medium' | 'high';
}

@Component({
  selector: 'app-school-calendar',
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './school-calendar.html',
  styleUrl: './school-calendar.scss',
})
export class SchoolCalendar {
  searchTerm = '';
  selectedType = '';
  selectedStatus = '';
  selectedPriority = '';

  types: Array<{ value: CalendarType; label: string; icon: string; color: string }> = [
    { value: 'academic', label: 'Académique', icon: 'ri-calendar-event-line', color: 'primary' },
    { value: 'exam', label: 'Examens', icon: 'ri-award-line', color: 'warning' },
    { value: 'holiday', label: 'Vacances', icon: 'ri-sun-line', color: 'success' },
    { value: 'inspection', label: 'Inspections', icon: 'ri-search-eye-line', color: 'info' },
    { value: 'deadline', label: 'Échéances', icon: 'ri-alarm-warning-line', color: 'danger' },
  ];

  items: CalendarItem[] = [
    {
      id: 'cal-001',
      title: 'Rentrée administrative',
      type: 'academic',
      status: 'planned',
      scope: 'National',
      startDate: '2026-09-01',
      endDate: '2026-09-05',
      owner: 'Direction nationale',
      progress: 20,
      priority: 'high',
    },
    {
      id: 'cal-002',
      title: 'Rentrée des classes',
      type: 'academic',
      status: 'planned',
      scope: 'National',
      startDate: '2026-09-15',
      endDate: '2026-09-15',
      owner: 'Écoles',
      progress: 12,
      priority: 'high',
    },
    {
      id: 'cal-003',
      title: 'Collecte des effectifs T1',
      type: 'deadline',
      status: 'active',
      scope: 'Toutes régions',
      startDate: '2026-10-01',
      endDate: '2026-10-15',
      owner: 'Recensement',
      progress: 54,
      priority: 'high',
    },
    {
      id: 'cal-004',
      title: 'Inspections pédagogiques',
      type: 'inspection',
      status: 'planned',
      scope: 'Régions prioritaires',
      startDate: '2026-11-03',
      endDate: '2026-11-28',
      owner: 'Inspection générale',
      progress: 8,
      priority: 'medium',
    },
    {
      id: 'cal-005',
      title: 'Compositions du 1er trimestre',
      type: 'exam',
      status: 'planned',
      scope: 'National',
      startDate: '2026-12-07',
      endDate: '2026-12-18',
      owner: 'Pédagogie',
      progress: 0,
      priority: 'high',
    },
    {
      id: 'cal-006',
      title: 'Vacances de fin d’année',
      type: 'holiday',
      status: 'planned',
      scope: 'National',
      startDate: '2026-12-21',
      endDate: '2027-01-04',
      owner: 'Administration scolaire',
      progress: 0,
      priority: 'low',
    },
    {
      id: 'cal-007',
      title: 'Validation bulletins T1',
      type: 'deadline',
      status: 'delayed',
      scope: 'Préfectures',
      startDate: '2027-01-05',
      endDate: '2027-01-12',
      owner: 'Directions préfectorales',
      progress: 68,
      priority: 'high',
    },
    {
      id: 'cal-008',
      title: 'Examens blancs BEPC/BAC',
      type: 'exam',
      status: 'planned',
      scope: 'Collège et lycée',
      startDate: '2027-03-09',
      endDate: '2027-03-20',
      owner: 'Examens',
      progress: 5,
      priority: 'medium',
    },
    {
      id: 'cal-009',
      title: 'Clôture année scolaire',
      type: 'academic',
      status: 'planned',
      scope: 'National',
      startDate: '2027-07-10',
      endDate: '2027-07-15',
      owner: 'Administration scolaire',
      progress: 0,
      priority: 'medium',
    },
  ];

  private exportColumns: ExportColumn<CalendarItem>[] = [
    { header: 'Titre', value: (row) => row.title },
    { header: 'Type', value: (row) => this.typeLabel(row.type) },
    { header: 'Statut', value: (row) => this.statusLabel(row.status) },
    { header: 'Périmètre', value: (row) => row.scope },
    { header: 'Début', value: (row) => row.startDate },
    { header: 'Fin', value: (row) => row.endDate },
    { header: 'Responsable', value: (row) => row.owner },
    { header: 'Avancement', value: (row) => `${row.progress}%` },
    { header: 'Priorité', value: (row) => this.priorityLabel(row.priority) },
  ];

  get filteredItems() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.items.filter((item) => {
      const matchesType = !this.selectedType || item.type === this.selectedType;
      const matchesStatus = !this.selectedStatus || item.status === this.selectedStatus;
      const matchesPriority = !this.selectedPriority || item.priority === this.selectedPriority;
      const searchable = this.normalizeSearch([item.title, item.scope, item.owner, this.typeLabel(item.type)].join(' '));

      return matchesType && matchesStatus && matchesPriority && (!search || searchable.includes(search));
    });
  }

  get totals() {
    const rows = this.filteredItems;

    return {
      events: rows.length,
      active: rows.filter((item) => item.status === 'active').length,
      planned: rows.filter((item) => item.status === 'planned').length,
      delayed: rows.filter((item) => item.status === 'delayed').length,
      highPriority: rows.filter((item) => item.priority === 'high').length,
      exams: rows.filter((item) => item.type === 'exam').length,
    };
  }

  resetFilters() {
    this.searchTerm = '';
    this.selectedType = '';
    this.selectedStatus = '';
    this.selectedPriority = '';
  }

  exportRows(format: 'csv' | 'excel' | 'print') {
    if (format === 'csv') {
      downloadCsv('calendrier-scolaire.csv', this.filteredItems, this.exportColumns);
      return;
    }

    if (format === 'excel') {
      downloadExcel('calendrier-scolaire.xls', this.filteredItems, this.exportColumns);
      return;
    }

    printTable('Calendrier scolaire', this.filteredItems, this.exportColumns);
  }

  typeLabel(type: CalendarType) {
    return this.types.find((item) => item.value === type)?.label ?? type;
  }

  typeClass(type: CalendarType) {
    const color = this.types.find((item) => item.value === type)?.color ?? 'primary';
    return `bg-${color}-transparent text-${color}`;
  }

  statusLabel(status: CalendarStatus) {
    const labels: Record<CalendarStatus, string> = {
      planned: 'Planifié',
      active: 'En cours',
      completed: 'Terminé',
      delayed: 'En retard',
    };

    return labels[status];
  }

  statusClass(status: CalendarStatus) {
    const classes: Record<CalendarStatus, string> = {
      planned: 'bg-info-transparent text-info',
      active: 'bg-primary-transparent text-primary',
      completed: 'bg-success-transparent text-success',
      delayed: 'bg-danger-transparent text-danger',
    };

    return classes[status];
  }

  priorityLabel(priority: CalendarItem['priority']) {
    const labels: Record<CalendarItem['priority'], string> = {
      low: 'Faible',
      medium: 'Moyenne',
      high: 'Élevée',
    };

    return labels[priority];
  }

  priorityClass(priority: CalendarItem['priority']) {
    const classes: Record<CalendarItem['priority'], string> = {
      low: 'bg-success-transparent text-success',
      medium: 'bg-warning-transparent text-warning',
      high: 'bg-danger-transparent text-danger',
    };

    return classes[priority];
  }

  formatNumber(value: number) {
    return value.toLocaleString('fr-FR');
  }

  private normalizeSearch(value?: string | null) {
    return (value ?? '')
      .toLocaleLowerCase('fr-FR')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '');
  }
}
