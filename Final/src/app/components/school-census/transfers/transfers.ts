import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { forkJoin } from 'rxjs';
import { CensusApiService } from '../shared/census-api.service';
import { downloadCsv, downloadExcel, ExportColumn, printTable } from '../shared/export-utils';
import { CensusPerson, Region, School } from '../shared/school-census.models';

type TransferStatus = 'pending' | 'approved' | 'rejected' | 'completed';
type TransferReason = 'family' | 'capacity' | 'distance' | 'orientation';

interface TransferRow {
  id: string;
  studentName: string;
  uniqueCode: string;
  fromSchool: string;
  toSchool: string;
  fromClass: string;
  toClass: string;
  regionId: string;
  region: string;
  reason: TransferReason;
  requestedAt: string;
  status: TransferStatus;
  daysOpen: number;
}

@Component({
  selector: 'app-transfers',
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './transfers.html',
  styleUrl: './transfers.scss',
})
export class Transfers {
  private censusApi = inject(CensusApiService);

  regions: Region[] = [];
  schools: School[] = [];
  rows: TransferRow[] = [];
  loading = false;
  error = '';
  searchTerm = '';
  selectedRegionId = '';
  selectedStatus = '';
  selectedReason = '';

  reasons: Array<{ value: TransferReason; label: string }> = [
    { value: 'family', label: 'Déménagement famille' },
    { value: 'capacity', label: 'Capacité / surcharge' },
    { value: 'distance', label: 'Distance domicile' },
    { value: 'orientation', label: 'Orientation scolaire' },
  ];

  private exportColumns: ExportColumn<TransferRow>[] = [
    { header: 'Code élève', value: (row) => row.uniqueCode },
    { header: 'Élève', value: (row) => row.studentName },
    { header: 'École origine', value: (row) => row.fromSchool },
    { header: 'École destination', value: (row) => row.toSchool },
    { header: 'Classe origine', value: (row) => row.fromClass },
    { header: 'Classe destination', value: (row) => row.toClass },
    { header: 'Région', value: (row) => row.region },
    { header: 'Motif', value: (row) => this.reasonLabel(row.reason) },
    { header: 'Demandé le', value: (row) => row.requestedAt },
    { header: 'Jours ouverts', value: (row) => row.daysOpen },
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
      const matchesReason = !this.selectedReason || row.reason === this.selectedReason;
      const searchable = this.normalizeSearch(
        [row.studentName, row.uniqueCode, row.fromSchool, row.toSchool, row.fromClass, row.toClass, row.region].join(' '),
      );

      return matchesRegion && matchesStatus && matchesReason && (!search || searchable.includes(search));
    });
  }

  get totals() {
    const rows = this.filteredRows;

    return {
      requests: rows.length,
      pending: rows.filter((row) => row.status === 'pending').length,
      approved: rows.filter((row) => row.status === 'approved').length,
      completed: rows.filter((row) => row.status === 'completed').length,
      rejected: rows.filter((row) => row.status === 'rejected').length,
      overdue: rows.filter((row) => row.status === 'pending' && row.daysOpen > 7).length,
    };
  }

  load() {
    this.loading = true;
    this.error = '';

    forkJoin({
      metadata: this.censusApi.metadata(),
      students: this.censusApi.students(),
    }).subscribe({
      next: ({ metadata, students }) => {
        this.regions = metadata.regions;
        this.schools = metadata.schools;
        this.rows = this.buildTransferRows(students, metadata.schools);
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger les transferts et inscriptions.';
        this.loading = false;
      },
    });
  }

  resetFilters() {
    this.searchTerm = '';
    this.selectedRegionId = '';
    this.selectedStatus = '';
    this.selectedReason = '';
  }

  exportRows(format: 'csv' | 'excel' | 'print') {
    if (format === 'csv') {
      downloadCsv('transferts-inscriptions.csv', this.filteredRows, this.exportColumns);
      return;
    }

    if (format === 'excel') {
      downloadExcel('transferts-inscriptions.xls', this.filteredRows, this.exportColumns);
      return;
    }

    printTable('Transferts & inscriptions', this.filteredRows, this.exportColumns);
  }

  statusLabel(status: TransferStatus) {
    const labels: Record<TransferStatus, string> = {
      pending: 'En attente',
      approved: 'Approuvé',
      rejected: 'Rejeté',
      completed: 'Terminé',
    };

    return labels[status];
  }

  statusClass(status: TransferStatus) {
    const classes: Record<TransferStatus, string> = {
      pending: 'bg-warning-transparent text-warning',
      approved: 'bg-info-transparent text-info',
      rejected: 'bg-danger-transparent text-danger',
      completed: 'bg-success-transparent text-success',
    };

    return classes[status];
  }

  reasonLabel(reason: TransferReason) {
    return this.reasons.find((item) => item.value === reason)?.label ?? reason;
  }

  formatNumber(value: number) {
    return value.toLocaleString('fr-FR');
  }

  private buildTransferRows(students: CensusPerson[], schools: School[]): TransferRow[] {
    const eligibleStudents = students.filter((student) => student.school).slice(0, 72);

    return eligibleStudents.map((student, index) => {
      const fromSchool = student.school;
      const destinationPool = schools.filter((school) => school.id !== fromSchool.id);
      const destination = destinationPool[index % Math.max(destinationPool.length, 1)] ?? fromSchool;
      const reason = this.reasons[index % this.reasons.length].value;
      const status: TransferStatus =
        index % 9 === 0 ? 'rejected' : index % 5 === 0 ? 'completed' : index % 3 === 0 ? 'approved' : 'pending';
      const daysOpen = status === 'pending' ? 2 + (index % 12) : index % 4;
      const date = new Date(2026, 3, Math.max(1, 28 - (index % 24)));

      return {
        id: student.id,
        studentName: student.fullName,
        uniqueCode: student.uniqueCode,
        fromSchool: fromSchool.name,
        toSchool: destination.name,
        fromClass: student.classRoom?.name ?? 'Classe non affectée',
        toClass: destination.classes?.[index % Math.max(destination.classes.length, 1)]?.name ?? 'À affecter',
        regionId: fromSchool.regionId,
        region: fromSchool.region?.name ?? 'Région non renseignée',
        reason,
        requestedAt: date.toLocaleDateString('fr-FR'),
        status,
        daysOpen,
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
