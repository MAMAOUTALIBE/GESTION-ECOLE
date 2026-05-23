import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { AuthService, SCHOOL_MANAGEMENT_ROLES } from '../../../shared/services/auth.service';
import { AcademicsApiService, SchoolYearPayload } from '../shared/academics-api.service';
import { AcademicPeriodType, SchoolYear } from '../shared/school-census.models';

@Component({
  selector: 'app-school-years',
  imports: [CommonModule, FormsModule, ReactiveFormsModule],
  templateUrl: './school-years.html',
  styleUrl: './school-years.scss',
})
export class SchoolYears {
  private auth = inject(AuthService);
  private academicsApi = inject(AcademicsApiService);
  private formBuilder = inject(FormBuilder);

  schoolYears: SchoolYear[] = [];
  loading = false;
  saving = false;
  error = '';
  searchTerm = '';

  periodTypes: Array<{ value: AcademicPeriodType; label: string }> = [
    { value: 'TRIMESTER', label: 'Trimestres' },
    { value: 'SEMESTER', label: 'Semestres' },
  ];

  form = this.formBuilder.group({
    name: [this.defaultSchoolYearName(), [Validators.required, Validators.minLength(4)]],
    startDate: [`${new Date().getFullYear()}-09-01`, Validators.required],
    endDate: [`${new Date().getFullYear() + 1}-07-31`, Validators.required],
    periodType: ['TRIMESTER' as AcademicPeriodType, Validators.required],
    isActive: [true],
  });

  get canManageSchoolYears() {
    return this.auth.hasAnyRole(SCHOOL_MANAGEMENT_ROLES);
  }

  get filteredSchoolYears() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.schoolYears.filter((schoolYear) => {
      const searchable = this.normalizeSearch(
        [
          schoolYear.name,
          this.periodTypeLabel(schoolYear.periodType),
          schoolYear.periods.map((period) => period.name).join(' '),
        ].join(' '),
      );

      return !search || searchable.includes(search);
    });
  }

  get totals() {
    return {
      years: this.schoolYears.length,
      active: this.schoolYears.filter((schoolYear) => schoolYear.isActive).length,
      periods: this.schoolYears.reduce((sum, schoolYear) => sum + schoolYear.periods.length, 0),
      visible: this.filteredSchoolYears.length,
    };
  }

  ngOnInit() {
    this.load();
  }

  load() {
    this.loading = true;
    this.error = '';

    this.academicsApi.listSchoolYears().subscribe({
      next: (schoolYears) => {
        this.schoolYears = schoolYears;
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger les années scolaires.';
        this.loading = false;
      },
    });
  }

  createSchoolYear() {
    if (!this.canManageSchoolYears || this.form.invalid || this.saving) {
      this.form.markAllAsTouched();
      return;
    }

    const value = this.form.getRawValue();
    const payload: SchoolYearPayload = {
      name: value.name ?? '',
      startDate: this.toIsoDate(value.startDate),
      endDate: this.toIsoDate(value.endDate),
      periodType: value.periodType ?? 'TRIMESTER',
      isActive: value.isActive ?? false,
    };

    this.saving = true;
    this.error = '';

    this.academicsApi.createSchoolYear(payload).subscribe({
      next: (schoolYear) => {
        this.schoolYears = [schoolYear, ...this.schoolYears.filter((item) => item.id !== schoolYear.id)];
        if (schoolYear.isActive) {
          this.schoolYears = this.schoolYears.map((item) =>
            item.id === schoolYear.id ? item : { ...item, isActive: false },
          );
        }
        this.form.reset({
          name: this.defaultSchoolYearName(1),
          startDate: `${new Date().getFullYear() + 1}-09-01`,
          endDate: `${new Date().getFullYear() + 2}-07-31`,
          periodType: 'TRIMESTER',
          isActive: false,
        });
        this.saving = false;
      },
      error: () => {
        this.error = 'Création de l’année scolaire impossible.';
        this.saving = false;
      },
    });
  }

  periodTypeLabel(type: AcademicPeriodType) {
    return this.periodTypes.find((item) => item.value === type)?.label ?? type;
  }

  formatDate(value: string) {
    return new Intl.DateTimeFormat('fr-FR', { dateStyle: 'medium' }).format(new Date(value));
  }

  resetSearch() {
    this.searchTerm = '';
  }

  private defaultSchoolYearName(offset = 0) {
    const year = new Date().getFullYear() + offset;
    return `${year}-${year + 1}`;
  }

  private toIsoDate(value?: string | null) {
    return value ? `${value}T00:00:00.000Z` : '';
  }

  private normalizeSearch(value?: string | null) {
    return (value ?? '')
      .toLocaleLowerCase('fr-FR')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '');
  }
}
