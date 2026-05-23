import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { forkJoin } from 'rxjs';
import { ACADEMIC_VALIDATION_ROLES, AuthService } from '../../../shared/services/auth.service';
import { AcademicsApiService } from '../shared/academics-api.service';
import { SchoolAdminService } from '../shared/school-admin.service';
import { AcademicPeriod, ClassRoom, ReportCard, SchoolYear } from '../shared/school-census.models';
import { ExportColumn, downloadCsv, downloadExcel, printTable } from '../shared/export-utils';

@Component({
  selector: 'app-report-cards',
  imports: [CommonModule, FormsModule],
  templateUrl: './report-cards.html',
  styleUrl: './report-cards.scss',
})
export class ReportCards {
  private auth = inject(AuthService);
  private academicsApi = inject(AcademicsApiService);
  private schoolApi = inject(SchoolAdminService);

  schoolYears: SchoolYear[] = [];
  classRooms: ClassRoom[] = [];
  reportCards: ReportCard[] = [];
  selectedSchoolYearId = '';
  selectedPeriodId = '';
  selectedClassRoomId = '';
  searchTerm = '';
  loading = false;
  generating = false;
  error = '';

  private reportExportColumns: ExportColumn<ReportCard>[] = [
    { header: 'Élève', value: (card) => card.student.fullName },
    { header: 'Matricule', value: (card) => card.student.uniqueCode },
    { header: 'École', value: (card) => card.student.school?.name },
    { header: 'Classe', value: (card) => card.classRoom?.name ?? card.student.classRoom?.name },
    { header: 'Année', value: (card) => card.schoolYear.name },
    { header: 'Période', value: (card) => card.period.name },
    { header: 'Moyenne', value: (card) => card.average },
    { header: 'Rang', value: (card) => card.rank },
    { header: 'Effectif', value: (card) => card.totalStudents },
    { header: 'Statut', value: (card) => card.status },
    { header: 'Code vérification', value: (card) => card.verificationCode },
  ];

  get canGenerateReportCards() {
    return this.auth.hasAnyRole(ACADEMIC_VALIDATION_ROLES);
  }

  get periods(): AcademicPeriod[] {
    return this.schoolYears.find((schoolYear) => schoolYear.id === this.selectedSchoolYearId)?.periods ?? [];
  }

  get filteredReportCards() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.reportCards.filter((card) => {
      const matchesSchoolYear = !this.selectedSchoolYearId || card.schoolYearId === this.selectedSchoolYearId;
      const matchesPeriod = !this.selectedPeriodId || card.periodId === this.selectedPeriodId;
      const matchesClass = !this.selectedClassRoomId || card.classRoomId === this.selectedClassRoomId;
      const searchable = this.normalizeSearch(
        [
          card.student.fullName,
          card.student.uniqueCode,
          card.student.school?.name,
          card.classRoom?.name,
          card.schoolYear.name,
          card.period.name,
          card.verificationCode,
        ].join(' '),
      );

      return matchesSchoolYear && matchesPeriod && matchesClass && (!search || searchable.includes(search));
    });
  }

  get reportTotals() {
    const rows = this.filteredReportCards;
    const averages = rows.map((card) => card.average).filter((average): average is number => average !== null && average !== undefined);
    return {
      reportCards: rows.length,
      validated: rows.filter((card) => card.status === 'VALIDATED').length,
      pending: rows.filter((card) => card.status !== 'VALIDATED').length,
      average: averages.length
        ? Math.round((averages.reduce((sum, average) => sum + average, 0) / averages.length) * 100) / 100
        : 0,
    };
  }

  ngOnInit() {
    this.load();
  }

  load() {
    this.loading = true;
    this.error = '';

    forkJoin({
      schoolYears: this.academicsApi.listSchoolYears(),
      classRooms: this.schoolApi.listClasses(),
      reportCards: this.academicsApi.listReportCards(),
    }).subscribe({
      next: ({ schoolYears, classRooms, reportCards }) => {
        this.schoolYears = schoolYears;
        this.classRooms = classRooms;
        this.reportCards = reportCards;
        const activeYear = schoolYears.find((schoolYear) => schoolYear.isActive) ?? schoolYears[0];
        this.selectedSchoolYearId = activeYear?.id ?? '';
        this.selectedPeriodId = activeYear?.periods[0]?.id ?? '';
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger les bulletins.';
        this.loading = false;
      },
    });
  }

  syncPeriodForYear() {
    this.selectedPeriodId = this.periods[0]?.id ?? '';
  }

  generateReportCards() {
    if (!this.canGenerateReportCards || !this.selectedSchoolYearId || !this.selectedPeriodId || this.generating) {
      return;
    }

    this.generating = true;
    this.error = '';

    this.academicsApi
      .generateReportCards({
        schoolYearId: this.selectedSchoolYearId,
        periodId: this.selectedPeriodId,
        classRoomId: this.selectedClassRoomId || null,
      })
      .subscribe({
        next: (generated) => {
          const generatedIds = new Set(generated.map((card) => card.id));
          this.reportCards = [
            ...generated,
            ...this.reportCards.filter((card) => !generatedIds.has(card.id)),
          ];
          this.generating = false;
        },
        error: () => {
          this.error = 'Génération des bulletins impossible.';
          this.generating = false;
        },
      });
  }

  validateReportCard(card: ReportCard) {
    if (!this.canGenerateReportCards) {
      return;
    }

    this.academicsApi.updateReportCardStatus(card.id, 'VALIDATED').subscribe({
      next: (updated) => {
        this.reportCards = this.reportCards.map((item) => (item.id === updated.id ? updated : item));
      },
      error: () => {
        this.error = 'Validation du bulletin impossible.';
      },
    });
  }

  exportCsv() {
    downloadCsv('bulletins.csv', this.filteredReportCards, this.reportExportColumns);
  }

  exportExcel() {
    downloadExcel('bulletins.xls', this.filteredReportCards, this.reportExportColumns);
  }

  printReport() {
    printTable('Bulletins scolaires', this.filteredReportCards, this.reportExportColumns);
  }

  statusClass(status: string) {
    if (status === 'VALIDATED') {
      return 'bg-success-transparent';
    }
    if (status === 'SUBMITTED') {
      return 'bg-warning-transparent';
    }
    if (status === 'REJECTED') {
      return 'bg-danger-transparent';
    }
    return 'bg-secondary-transparent';
  }

  private normalizeSearch(value?: string | null) {
    return (value ?? '')
      .toLocaleLowerCase('fr-FR')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '');
  }
}
