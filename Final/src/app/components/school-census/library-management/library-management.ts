import { CommonModule } from '@angular/common';
import { Component, DestroyRef, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { Subject as RxSubject, debounceTime, forkJoin } from 'rxjs';
import { AcademicsApiService } from '../shared/academics-api.service';
import { CensusApiService } from '../shared/census-api.service';
import { downloadCsv, downloadExcel, ExportColumn, printTable } from '../shared/export-utils';
import {
  LibraryApiService,
  LibraryInventoryQuery,
  LibraryInventoryRow,
  LibraryLoanRow,
  LibraryLoanStatus,
  LibraryLoansQuery,
  LibraryStatus,
} from '../shared/library-api.service';
import { CensusPerson, Region, School, Subject } from '../shared/school-census.models';

@Component({
  selector: 'app-library-management',
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './library-management.html',
  styleUrl: './library-management.scss',
})
export class LibraryManagement {
  private academicsApi = inject(AcademicsApiService);
  private censusApi = inject(CensusApiService);
  private destroyRef = inject(DestroyRef);
  private libraryApi = inject(LibraryApiService);
  private remoteFilterChanges = new RxSubject<void>();

  regions: Region[] = [];
  schools: School[] = [];
  students: CensusPerson[] = [];
  inventoryRows: LibraryInventoryRow[] = [];
  loanRows: LibraryLoanRow[] = [];
  loading = false;
  error = '';
  searchTerm = '';
  selectedRegionId = '';
  selectedSchoolId = '';
  selectedSubject = '';
  selectedStatus: LibraryStatus | '' = '';
  selectedLoanStatus: LibraryLoanStatus | '' = '';
  inventoryTotal = 0;
  loanTotal = 0;
  usingFallback = false;

  levels = ['CP1', 'CE2', 'CM2', '7ème', '9ème', 'Terminale'];

  private inventoryColumns: ExportColumn<LibraryInventoryRow>[] = [
    { header: 'Code école', value: (row) => row.code },
    { header: 'Établissement', value: (row) => row.schoolName },
    { header: 'Région', value: (row) => row.region },
    { header: 'Niveau', value: (row) => row.level },
    { header: 'Matière', value: (row) => row.subjectName },
    { header: 'Titre', value: (row) => row.title },
    { header: 'Stock', value: (row) => row.stock },
    { header: 'Prêtés', value: (row) => row.loaned },
    { header: 'Abîmés', value: (row) => row.damaged },
    { header: 'Besoin', value: (row) => row.required },
    { header: 'Couverture', value: (row) => `${row.coverageRate}%` },
    { header: 'Statut', value: (row) => this.statusLabel(row.status) },
    { header: 'Dernier inventaire', value: (row) => row.lastInventory },
  ];

  private loanColumns: ExportColumn<LibraryLoanRow>[] = [
    { header: 'Code élève', value: (row) => row.uniqueCode },
    { header: 'Élève', value: (row) => row.studentName },
    { header: 'Établissement', value: (row) => row.schoolName },
    { header: 'Classe', value: (row) => row.className },
    { header: 'Titre', value: (row) => row.title },
    { header: 'Emprunt', value: (row) => row.borrowedAt },
    { header: 'Retour prévu', value: (row) => row.dueAt },
    { header: 'Statut', value: (row) => this.loanStatusLabel(row.status) },
  ];

  ngOnInit() {
    this.remoteFilterChanges
      .pipe(debounceTime(300), takeUntilDestroyed(this.destroyRef))
      .subscribe(() => this.refreshFromApi());

    this.load();
  }

  get filteredSchools() {
    return this.schools
      .filter((school) => !this.selectedRegionId || school.regionId === this.selectedRegionId)
      .sort((left, right) => left.name.localeCompare(right.name, 'fr-FR'));
  }

  get subjects() {
    return Array.from(new Set(this.inventoryRows.map((row) => row.subjectName))).sort((left, right) =>
      left.localeCompare(right, 'fr-FR'),
    );
  }

  get filteredInventoryRows() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.inventoryRows.filter((row) => {
      const matchesRegion = !this.selectedRegionId || row.regionId === this.selectedRegionId;
      const matchesSchool = !this.selectedSchoolId || row.schoolId === this.selectedSchoolId;
      const matchesSubject = !this.selectedSubject || row.subjectName === this.selectedSubject;
      const matchesStatus = !this.selectedStatus || row.status === this.selectedStatus;
      const searchable = this.normalizeSearch(
        [row.schoolName, row.code, row.region, row.level, row.subjectName, row.title].join(' '),
      );

      return matchesRegion && matchesSchool && matchesSubject && matchesStatus && (!search || searchable.includes(search));
    });
  }

  get filteredLoanRows() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.loanRows.filter((row) => {
      const matchesStatus = !this.selectedLoanStatus || row.status === this.selectedLoanStatus;
      const searchable = this.normalizeSearch(
        [row.studentName, row.uniqueCode, row.schoolName, row.className, row.title].join(' '),
      );

      return matchesStatus && (!search || searchable.includes(search));
    });
  }

  get totals() {
    const rows = this.filteredInventoryRows;
    const stock = rows.reduce((sum, row) => sum + row.stock, 0);
    const required = rows.reduce((sum, row) => sum + row.required, 0);
    const loaned = rows.reduce((sum, row) => sum + row.loaned, 0);
    const damaged = rows.reduce((sum, row) => sum + row.damaged, 0);

    return {
      titles: rows.length,
      stock,
      required,
      loaned,
      damaged,
      missing: Math.max(required - stock, 0),
      coverageRate: required ? Math.round((stock / required) * 100) : 0,
      shortage: rows.filter((row) => row.status === 'shortage').length,
      lateLoans: this.filteredLoanRows.filter((row) => row.status === 'late').length,
    };
  }

  get statusSummaries() {
    return [
      {
        status: 'shortage' as LibraryStatus,
        label: 'Manques critiques',
        count: this.filteredInventoryRows.filter((row) => row.status === 'shortage').length,
        className: 'bg-danger-transparent text-danger',
      },
      {
        status: 'watch' as LibraryStatus,
        label: 'À surveiller',
        count: this.filteredInventoryRows.filter((row) => row.status === 'watch').length,
        className: 'bg-warning-transparent text-warning',
      },
      {
        status: 'sufficient' as LibraryStatus,
        label: 'Stock suffisant',
        count: this.filteredInventoryRows.filter((row) => row.status === 'sufficient').length,
        className: 'bg-success-transparent text-success',
      },
    ];
  }

  get topSubjects() {
    const summaries = new Map<string, { subject: string; stock: number; required: number; missing: number }>();

    this.filteredInventoryRows.forEach((row) => {
      const current = summaries.get(row.subjectName) ?? { subject: row.subjectName, stock: 0, required: 0, missing: 0 };
      current.stock += row.stock;
      current.required += row.required;
      current.missing += Math.max(row.required - row.stock, 0);
      summaries.set(row.subjectName, current);
    });

    return [...summaries.values()].sort((left, right) => right.missing - left.missing).slice(0, 6);
  }

  load() {
    this.loading = true;
    this.error = '';
    this.usingFallback = false;

    forkJoin({
      metadata: this.censusApi.metadata(),
      inventory: this.libraryApi.inventory(this.inventoryQuery()),
      loans: this.libraryApi.loans(this.loansQuery()),
    }).subscribe({
      next: ({ metadata, inventory, loans }) => {
        this.regions = metadata.regions.length ? metadata.regions : this.fallbackRegions();
        this.schools = metadata.schools.length ? metadata.schools : this.fallbackSchools();
        this.students = [];
        this.applyApiRows(inventory.rows, inventory.total, loans.rows, loans.total);
        this.loading = false;
      },
      error: () => this.loadFallback(),
    });
  }

  private loadFallback() {
    this.usingFallback = true;

    forkJoin({
      metadata: this.censusApi.metadata(),
      subjects: this.academicsApi.listSubjects(),
      students: this.censusApi.students(),
    }).subscribe({
      next: ({ metadata, subjects, students }) => {
        this.regions = metadata.regions.length ? metadata.regions : this.fallbackRegions();
        this.schools = metadata.schools.length ? metadata.schools : this.fallbackSchools();
        this.students = students.length ? students : this.fallbackStudents();
        this.inventoryRows = this.buildInventoryRows(subjects.length ? subjects : this.fallbackSubjects());
        this.loanRows = this.buildLoanRows();
        this.inventoryTotal = this.inventoryRows.length;
        this.loanTotal = this.loanRows.length;
        this.loading = false;
      },
      error: () => {
        this.regions = this.fallbackRegions();
        this.schools = this.fallbackSchools();
        this.students = this.fallbackStudents();
        this.inventoryRows = this.buildInventoryRows(this.fallbackSubjects());
        this.loanRows = this.buildLoanRows();
        this.inventoryTotal = this.inventoryRows.length;
        this.loanTotal = this.loanRows.length;
        this.error = 'Données backend indisponibles, affichage de la bibliothèque de démonstration.';
        this.loading = false;
      },
    });
  }

  onRegionChange() {
    this.selectedSchoolId = '';
    this.onRemoteFilterChange();
  }

  onRemoteFilterChange() {
    if (this.usingFallback) {
      return;
    }
    this.remoteFilterChanges.next();
  }

  toggleStatus(status: LibraryStatus) {
    this.selectedStatus = this.selectedStatus === status ? '' : status;
    this.onRemoteFilterChange();
  }

  resetFilters() {
    this.searchTerm = '';
    this.selectedRegionId = '';
    this.selectedSchoolId = '';
    this.selectedSubject = '';
    this.selectedStatus = '';
    this.selectedLoanStatus = '';
    if (!this.usingFallback) {
      this.refreshFromApi();
    }
  }

  exportInventory(format: 'csv' | 'excel' | 'print') {
    if (format === 'csv') {
      downloadCsv('bibliotheque-inventaire.csv', this.filteredInventoryRows, this.inventoryColumns);
      return;
    }

    if (format === 'excel') {
      downloadExcel('bibliotheque-inventaire.xls', this.filteredInventoryRows, this.inventoryColumns);
      return;
    }

    printTable('Bibliothèque - inventaire', this.filteredInventoryRows, this.inventoryColumns);
  }

  exportLoans(format: 'csv' | 'excel' | 'print') {
    if (format === 'csv') {
      downloadCsv('bibliotheque-prets.csv', this.filteredLoanRows, this.loanColumns);
      return;
    }

    if (format === 'excel') {
      downloadExcel('bibliotheque-prets.xls', this.filteredLoanRows, this.loanColumns);
      return;
    }

    printTable('Bibliothèque - prêts', this.filteredLoanRows, this.loanColumns);
  }

  statusLabel(status: LibraryStatus) {
    const labels: Record<LibraryStatus, string> = {
      sufficient: 'Suffisant',
      watch: 'À surveiller',
      shortage: 'Manque',
    };

    return labels[status];
  }

  statusClass(status: LibraryStatus) {
    const classes: Record<LibraryStatus, string> = {
      sufficient: 'bg-success-transparent text-success',
      watch: 'bg-warning-transparent text-warning',
      shortage: 'bg-danger-transparent text-danger',
    };

    return classes[status];
  }

  loanStatusLabel(status: LibraryLoanStatus) {
    const labels: Record<LibraryLoanStatus, string> = {
      borrowed: 'Emprunté',
      late: 'En retard',
      returned: 'Retourné',
    };

    return labels[status];
  }

  loanStatusClass(status: LibraryLoanStatus) {
    const classes: Record<LibraryLoanStatus, string> = {
      borrowed: 'bg-primary-transparent text-primary',
      late: 'bg-danger-transparent text-danger',
      returned: 'bg-success-transparent text-success',
    };

    return classes[status];
  }

  formatNumber(value: number) {
    return value.toLocaleString('fr-FR');
  }

  private refreshFromApi() {
    this.loading = true;
    this.error = '';

    forkJoin({
      inventory: this.libraryApi.inventory(this.inventoryQuery()),
      loans: this.libraryApi.loans(this.loansQuery()),
    }).subscribe({
      next: ({ inventory, loans }) => {
        this.applyApiRows(inventory.rows, inventory.total, loans.rows, loans.total);
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de rafraîchir les données bibliothèque depuis le backend.';
        this.loading = false;
      },
    });
  }

  private applyApiRows(
    inventoryRows: LibraryInventoryRow[],
    inventoryTotal: number,
    loanRows: LibraryLoanRow[],
    loanTotal: number,
  ) {
    this.inventoryRows = inventoryRows;
    this.inventoryTotal = inventoryTotal;
    this.loanRows = loanRows;
    this.loanTotal = loanTotal;
  }

  private inventoryQuery(): LibraryInventoryQuery {
    return {
      search: this.searchTerm.trim() || undefined,
      regionId: this.selectedRegionId || undefined,
      schoolId: this.selectedSchoolId || undefined,
      status: this.selectedStatus || undefined,
      pageSize: 500,
    };
  }

  private loansQuery(): LibraryLoansQuery {
    return {
      search: this.searchTerm.trim() || undefined,
      regionId: this.selectedRegionId || undefined,
      schoolId: this.selectedSchoolId || undefined,
      status: this.selectedLoanStatus || undefined,
      pageSize: 500,
    };
  }

  private buildInventoryRows(subjects: Subject[]): LibraryInventoryRow[] {
    return this.schools.slice(0, 35).flatMap((school, schoolIndex) =>
      subjects.slice(0, 5).map((subject, subjectIndex) => {
        const base = 28 + ((schoolIndex + subjectIndex) % 7) * 8;
        const required = Math.max(this.countStudentsForSchool(school.id), base);
        const stock = Math.max(8, Math.round(required * (0.55 + ((schoolIndex + subjectIndex) % 6) * 0.09)));
        const damaged = (schoolIndex + subjectIndex) % 5;
        const loaned = Math.max(0, Math.min(stock - damaged, Math.round(stock * (0.35 + (subjectIndex % 3) * 0.12))));
        const coverageRate = required ? Math.round((stock / required) * 100) : 0;
        const status: LibraryStatus = coverageRate < 70 ? 'shortage' : coverageRate < 90 ? 'watch' : 'sufficient';
        const level = this.levels[(schoolIndex + subjectIndex) % this.levels.length];

        return {
          id: `${school.id}-${subject.id}-${level}`,
          schoolId: school.id,
          schoolName: school.name,
          code: school.code,
          regionId: school.regionId,
          region: school.region?.name ?? this.regions.find((region) => region.id === school.regionId)?.name ?? 'Région',
          level,
          subjectName: subject.name,
          title: `${subject.name} ${level}`,
          stock,
          loaned,
          damaged,
          required,
          coverageRate,
          status,
          lastInventory: `${String(2 + ((schoolIndex + subjectIndex) % 24)).padStart(2, '0')}/05/2026`,
        };
      }),
    );
  }

  private buildLoanRows(): LibraryLoanRow[] {
    const titles = this.inventoryRows.map((row) => row.title);

    return this.students.slice(0, 90).map((student, index) => {
      const status: LibraryLoanStatus = index % 9 === 0 ? 'late' : index % 4 === 0 ? 'returned' : 'borrowed';

      return {
        id: `loan-${student.id}-${index}`,
        studentName: student.fullName,
        uniqueCode: student.uniqueCode,
        schoolName: student.school?.name ?? 'École non renseignée',
        className: student.classRoom?.name ?? 'Classe non affectée',
        title: titles[index % Math.max(titles.length, 1)] ?? 'Manuel scolaire',
        borrowedAt: `${String(1 + (index % 18)).padStart(2, '0')}/04/2026`,
        dueAt: `${String(3 + (index % 24)).padStart(2, '0')}/05/2026`,
        status,
      };
    });
  }

  private countStudentsForSchool(schoolId: string) {
    return this.students.filter((student) => student.school?.id === schoolId).length;
  }

  private fallbackRegions(): Region[] {
    return [
      { id: 'rg-conakry', code: 'RG-CON', name: 'Conakry' },
      { id: 'rg-kindia', code: 'RG-KIN', name: 'Kindia' },
      { id: 'rg-kankan', code: 'RG-KAN', name: 'Kankan' },
    ];
  }

  private fallbackSchools(): School[] {
    const regions = this.regions.length ? this.regions : this.fallbackRegions();
    const names = ['École Primaire Almamya', 'Collège 2 Octobre', 'Lycée Donka', 'École Application Kindia'];

    return names.map((name, index) => {
      const region = regions[index % regions.length];

      return {
        id: `school-library-${index + 1}`,
        name,
        code: `ECO-${String(index + 1).padStart(3, '0')}`,
        regionId: region.id,
        region,
      };
    });
  }

  private fallbackSubjects(): Subject[] {
    return [
      { id: 'subject-math', code: 'MATH', name: 'Mathématiques', coefficient: 4 },
      { id: 'subject-fr', code: 'FR', name: 'Français', coefficient: 4 },
      { id: 'subject-sc', code: 'SC', name: 'Sciences', coefficient: 3 },
      { id: 'subject-hg', code: 'HG', name: 'Histoire-Géographie', coefficient: 2 },
      { id: 'subject-ang', code: 'ANG', name: 'Anglais', coefficient: 2 },
    ];
  }

  private fallbackStudents(): CensusPerson[] {
    return this.fallbackSchools().flatMap((school, schoolIndex) =>
      Array.from({ length: 18 + schoolIndex * 5 }, (_, index) => ({
        id: `${school.id}-library-student-${index + 1}`,
        type: 'STUDENT',
        uniqueCode: `ELV-${schoolIndex + 1}${String(index + 1).padStart(3, '0')}`,
        firstName: 'Élève',
        lastName: `${index + 1}`,
        fullName: `Élève ${index + 1}`,
        gender: index % 2 ? 'MALE' : 'FEMALE',
        school,
        createdAt: '2026-05-03T00:00:00.000Z',
      })),
    );
  }

  private normalizeSearch(value?: string | null) {
    return (value ?? '')
      .toLocaleLowerCase('fr-FR')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '');
  }
}
