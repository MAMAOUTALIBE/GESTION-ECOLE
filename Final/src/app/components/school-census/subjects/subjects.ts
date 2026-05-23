import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import {
  ACADEMIC_VALIDATION_ROLES,
  AuthService,
} from '../../../shared/services/auth.service';
import { AcademicsApiService, SubjectPayload } from '../shared/academics-api.service';
import { Subject } from '../shared/school-census.models';

@Component({
  selector: 'app-subjects',
  imports: [CommonModule, FormsModule, ReactiveFormsModule],
  templateUrl: './subjects.html',
  styleUrl: './subjects.scss',
})
export class Subjects {
  private auth = inject(AuthService);
  private academicsApi = inject(AcademicsApiService);
  private formBuilder = inject(FormBuilder);

  subjects: Subject[] = [];
  loading = false;
  saving = false;
  error = '';
  searchTerm = '';
  selectedLevel = '';

  form = this.formBuilder.group({
    code: ['', [Validators.required, Validators.minLength(2)]],
    name: ['', [Validators.required, Validators.minLength(2)]],
    level: [''],
    coefficient: ['1', Validators.required],
  });

  get canManageSubjects() {
    return this.auth.hasAnyRole(ACADEMIC_VALIDATION_ROLES);
  }

  get levels() {
    return Array.from(new Set(this.subjects.map((subject) => subject.level).filter(Boolean) as string[])).sort(
      (left, right) => left.localeCompare(right, 'fr-FR'),
    );
  }

  get filteredSubjects() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.subjects.filter((subject) => {
      const matchesLevel = !this.selectedLevel || subject.level === this.selectedLevel;
      const searchable = this.normalizeSearch([subject.code, subject.name, subject.level].join(' '));

      return matchesLevel && (!search || searchable.includes(search));
    });
  }

  get totals() {
    return {
      subjects: this.subjects.length,
      levels: this.levels.length,
      averageCoefficient: this.subjects.length
        ? Math.round((this.subjects.reduce((sum, subject) => sum + subject.coefficient, 0) / this.subjects.length) * 10) /
          10
        : 0,
      visible: this.filteredSubjects.length,
    };
  }

  ngOnInit() {
    this.load();
  }

  load() {
    this.loading = true;
    this.error = '';

    this.academicsApi.listSubjects().subscribe({
      next: (subjects) => {
        this.subjects = subjects;
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger les matières.';
        this.loading = false;
      },
    });
  }

  createSubject() {
    if (!this.canManageSubjects || this.form.invalid || this.saving) {
      this.form.markAllAsTouched();
      return;
    }

    const value = this.form.getRawValue();
    const payload: SubjectPayload = {
      code: (value.code ?? '').trim().toUpperCase(),
      name: value.name ?? '',
      level: this.optionalText(value.level),
      coefficient: this.toNumber(value.coefficient) ?? 1,
    };

    this.saving = true;
    this.error = '';

    this.academicsApi.createSubject(payload).subscribe({
      next: (subject) => {
        this.subjects = [...this.subjects, subject].sort((left, right) =>
          `${left.level ?? ''}${left.name}`.localeCompare(`${right.level ?? ''}${right.name}`, 'fr-FR'),
        );
        this.form.reset({ code: '', name: '', level: '', coefficient: '1' });
        this.saving = false;
      },
      error: () => {
        this.error = 'Création de la matière impossible.';
        this.saving = false;
      },
    });
  }

  resetFilters() {
    this.searchTerm = '';
    this.selectedLevel = '';
  }

  formatCoefficient(value: number) {
    return value.toLocaleString('fr-FR', { maximumFractionDigits: 1 });
  }

  private optionalText(value?: string | null) {
    const trimmed = value?.trim();
    return trimmed || null;
  }

  private toNumber(value?: string | null) {
    if (value === null || value === undefined || value === '') {
      return null;
    }

    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  private normalizeSearch(value?: string | null) {
    return (value ?? '')
      .toLocaleLowerCase('fr-FR')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '');
  }
}
