import { CommonModule } from '@angular/common';
import { Component, DestroyRef, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { catchError, of } from 'rxjs';
import { downloadCsv, downloadExcel, ExportColumn, printTable } from '../shared/export-utils';
import { SchoolAdminService } from '../shared/school-admin.service';
import { Region, School } from '../shared/school-census.models';

type InfrastructureStatus = 'good' | 'watch' | 'critical';

interface InfrastructureRow {
  id: string;
  schoolName: string;
  code: string;
  region: string;
  regionId: string;
  prefecture: string;
  commune: string;
  classrooms: number;
  usableClassrooms: number;
  students: number;
  desks: number;
  water: boolean;
  electricity: boolean;
  latrines: number;
  status: InfrastructureStatus;
}

@Component({
  selector: 'app-infrastructure',
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './infrastructure.html',
  styleUrl: './infrastructure.scss',
})
export class Infrastructure {
  private schoolApi = inject(SchoolAdminService);
  private destroyRef = inject(DestroyRef);

  rows: InfrastructureRow[] = [];
  regions: Region[] = [];
  loading = false;
  error = '';
  searchTerm = '';
  selectedRegionId = '';
  selectedStatus = '';
  selectedService = '';

  private exportColumns: ExportColumn<InfrastructureRow>[] = [
    { header: 'Code école', value: (row) => row.code },
    { header: 'Établissement', value: (row) => row.schoolName },
    { header: 'Région', value: (row) => row.region },
    { header: 'Préfecture', value: (row) => row.prefecture },
    { header: 'Commune', value: (row) => row.commune },
    { header: 'Salles', value: (row) => row.classrooms },
    { header: 'Salles utilisables', value: (row) => row.usableClassrooms },
    { header: 'Élèves', value: (row) => row.students },
    { header: 'Tables-bancs', value: (row) => row.desks },
    { header: 'Eau', value: (row) => (row.water ? 'Oui' : 'Non') },
    { header: 'Électricité', value: (row) => (row.electricity ? 'Oui' : 'Non') },
    { header: 'Latrines', value: (row) => row.latrines },
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
        (this.selectedService === 'water' && row.water) ||
        (this.selectedService === 'missing-water' && !row.water) ||
        (this.selectedService === 'electricity' && row.electricity) ||
        (this.selectedService === 'missing-electricity' && !row.electricity);
      const searchable = this.normalizeSearch(
        [row.schoolName, row.code, row.region, row.prefecture, row.commune].join(' '),
      );

      return matchesRegion && matchesStatus && matchesService && (!search || searchable.includes(search));
    });
  }

  get totals() {
    const rows = this.filteredRows;
    const classrooms = rows.reduce((sum, row) => sum + row.classrooms, 0);
    const usableClassrooms = rows.reduce((sum, row) => sum + row.usableClassrooms, 0);
    const students = rows.reduce((sum, row) => sum + row.students, 0);
    const desks = rows.reduce((sum, row) => sum + row.desks, 0);

    return {
      schools: rows.length,
      classrooms,
      usableClassrooms,
      repairClassrooms: Math.max(classrooms - usableClassrooms, 0),
      students,
      desks,
      deskCoverage: students ? Math.round((desks / students) * 100) : 0,
      waterCoverage: rows.length ? Math.round((rows.filter((row) => row.water).length / rows.length) * 100) : 0,
      electricityCoverage: rows.length ? Math.round((rows.filter((row) => row.electricity).length / rows.length) * 100) : 0,
      critical: rows.filter((row) => row.status === 'critical').length,
    };
  }

  load() {
    this.loading = true;
    this.error = '';

    // /api/schools renvoie désormais les champs Phase 10 (eau, élec, toilettes,
    // bâti, affiliation). Plus besoin du fallback heuristique sur counts.
    this.schoolApi.listSchools()
      .pipe(
        catchError(() => of(null)),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((schools) => {
        if (!schools) {
          this.error = 'Impossible de charger les infrastructures scolaires.';
          this.loading = false;
          return;
        }
        const regionsMap = new Map<string, Region>();
        for (const s of schools) {
          if (s.region) regionsMap.set(s.region.id, s.region);
        }
        this.regions = Array.from(regionsMap.values()).sort(
          (a, b) => a.name.localeCompare(b.name, 'fr-FR'),
        );
        this.rows = this.buildInfrastructureRows(schools);
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
      downloadCsv('infrastructures-scolaires.csv', this.filteredRows, this.exportColumns);
      return;
    }

    if (format === 'excel') {
      downloadExcel('infrastructures-scolaires.xls', this.filteredRows, this.exportColumns);
      return;
    }

    printTable('Infrastructures scolaires', this.filteredRows, this.exportColumns);
  }

  statusLabel(status: InfrastructureStatus) {
    const labels: Record<InfrastructureStatus, string> = {
      good: 'Conforme',
      watch: 'À surveiller',
      critical: 'Critique',
    };

    return labels[status];
  }

  statusClass(status: InfrastructureStatus) {
    const classes: Record<InfrastructureStatus, string> = {
      good: 'bg-success-transparent text-success',
      watch: 'bg-warning-transparent text-warning',
      critical: 'bg-danger-transparent text-danger',
    };

    return classes[status];
  }

  formatNumber(value: number) {
    return value.toLocaleString('fr-FR');
  }

  private buildInfrastructureRows(schools: School[]): InfrastructureRow[] {
    return schools.map((school) => {
      // Salles : Phase 10 (classroomsTotal/Usable) prioritaire ; sinon fallback
      // sur counts.classes ou classes.length.
      const classrooms = school.classroomsTotal
        ?? Math.max(school.counts?.classes ?? school.classes?.length ?? 1, 1);
      const usableClassrooms = school.classroomsUsable ?? classrooms;
      const students = school.counts?.students ?? 0;

      // Tables-bancs : encore non tracké au backend → estimation conservatrice
      // basée sur le ratio salle/élève (1 banc pour 2 élèves en zone tendue).
      const desks = Math.max(Math.round(students * 0.65), 0);

      // Eau : Phase 10 explicite ; fallback "présent" si non renseigné
      const water = school.waterSource
        ? school.waterSource !== 'NONE'
        : true;
      // Électricité : idem
      const electricity = school.electricitySource
        ? school.electricitySource !== 'NONE'
        : true;
      // Latrines : somme garçons + filles si Phase 10, sinon estimation
      const latrines = (school.toiletsBoys ?? 0) + (school.toiletsGirls ?? 0)
        || Math.max(1, Math.round(classrooms / 2));

      const deskCoverage = students ? desks / students : 1;
      const buildingDangerous = school.buildingCondition === 'DANGEROUS'
        || school.buildingCondition === 'POOR';
      const status: InfrastructureStatus =
        !water || buildingDangerous || usableClassrooms < classrooms - 1
          ? 'critical'
          : !electricity || deskCoverage < 0.85 || (school.toiletsGirls ?? 1) === 0
            ? 'watch'
            : 'good';

      return {
        id: school.id,
        schoolName: school.name,
        code: school.code,
        region: school.region?.name ?? 'Région non renseignée',
        regionId: school.regionId,
        prefecture: school.prefecture ?? 'Préfecture non renseignée',
        commune: school.commune ?? 'Commune non renseignée',
        classrooms,
        usableClassrooms,
        students,
        desks,
        water,
        electricity,
        latrines,
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
