import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { forkJoin } from 'rxjs';
import {
  ACADEMIC_VALIDATION_ROLES,
  ACADEMIC_WRITE_ROLES,
  AuthService,
} from '../../../shared/services/auth.service';
import {
  AcademicsApiService,
  AssessmentPayload,
  SchoolYearPayload,
  SubjectPayload,
} from '../shared/academics-api.service';
import { CensusApiService } from '../shared/census-api.service';
import { SchoolAdminService } from '../shared/school-admin.service';
import {
  AcademicPeriod,
  AcademicPeriodType,
  Assessment,
  AssessmentType,
  CensusPerson,
  ClassRoom,
  Grade,
  SchoolYear,
  Subject,
} from '../shared/school-census.models';

interface GradeRow {
  student: CensusPerson;
  score: string;
  appreciation: string;
}

@Component({
  selector: 'app-grades',
  imports: [CommonModule, FormsModule, ReactiveFormsModule],
  templateUrl: './grades.html',
  styleUrl: './grades.scss',
})
export class Grades {
  private auth = inject(AuthService);
  private academicsApi = inject(AcademicsApiService);
  private censusApi = inject(CensusApiService);
  private schoolApi = inject(SchoolAdminService);
  private formBuilder = inject(FormBuilder);

  schoolYears: SchoolYear[] = [];
  subjects: Subject[] = [];
  classRooms: ClassRoom[] = [];
  students: CensusPerson[] = [];
  assessments: Assessment[] = [];
  grades: Grade[] = [];
  gradeRows: GradeRow[] = [];
  selectedAssessmentId = '';
  loading = false;
  saving = false;
  error = '';
  searchTerm = '';

  assessmentTypes: Array<{ value: AssessmentType; label: string }> = [
    { value: 'QUIZ', label: 'Contrôle' },
    { value: 'HOMEWORK', label: 'Devoir' },
    { value: 'COMPOSITION', label: 'Composition' },
    { value: 'NATIONAL_EXAM', label: 'Examen national' },
    { value: 'ORAL', label: 'Oral' },
    { value: 'PROJECT', label: 'Projet' },
    { value: 'OTHER', label: 'Autre' },
  ];

  periodTypes: Array<{ value: AcademicPeriodType; label: string }> = [
    { value: 'TRIMESTER', label: 'Trimestres' },
    { value: 'SEMESTER', label: 'Semestres' },
  ];

  assessmentForm = this.formBuilder.group({
    title: ['', [Validators.required, Validators.minLength(2)]],
    type: ['COMPOSITION' as AssessmentType, Validators.required],
    coefficient: ['1', Validators.required],
    maxScore: ['20', Validators.required],
    assessedAt: [this.todayIso()],
    schoolYearId: ['', Validators.required],
    periodId: ['', Validators.required],
    subjectId: ['', Validators.required],
    classRoomId: ['', Validators.required],
  });

  schoolYearForm = this.formBuilder.group({
    name: [this.defaultSchoolYearName(), [Validators.required, Validators.minLength(4)]],
    startDate: [`${new Date().getFullYear()}-09-01`, Validators.required],
    endDate: [`${new Date().getFullYear() + 1}-07-31`, Validators.required],
    periodType: ['TRIMESTER' as AcademicPeriodType, Validators.required],
    isActive: [true],
  });

  subjectForm = this.formBuilder.group({
    code: ['', [Validators.required, Validators.minLength(2)]],
    name: ['', [Validators.required, Validators.minLength(2)]],
    level: [''],
    coefficient: ['1', Validators.required],
  });

  get canManageNotes() {
    return this.auth.hasAnyRole(ACADEMIC_WRITE_ROLES);
  }

  get canValidateNotes() {
    return this.auth.hasAnyRole(ACADEMIC_VALIDATION_ROLES);
  }

  get selectedAssessment() {
    return this.assessments.find((assessment) => assessment.id === this.selectedAssessmentId) ?? null;
  }

  get filteredAssessments() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.assessments.filter((assessment) => {
      const searchable = this.normalizeSearch(
        [
          assessment.title,
          assessment.subject?.name,
          assessment.classRoom?.name,
          assessment.classRoom?.school?.name,
          assessment.period?.name,
          assessment.schoolYear?.name,
        ].join(' '),
      );
      return !search || searchable.includes(search);
    });
  }

  get assessmentTotals() {
    return {
      assessments: this.filteredAssessments.length,
      grades: this.filteredAssessments.reduce((sum, assessment) => sum + (assessment.gradesCount ?? 0), 0),
      validated: this.filteredAssessments.filter((assessment) => assessment.status === 'VALIDATED').length,
      pending: this.filteredAssessments.filter((assessment) => assessment.status !== 'VALIDATED').length,
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
      subjects: this.academicsApi.listSubjects(),
      classRooms: this.schoolApi.listClasses(),
      students: this.censusApi.students(),
      assessments: this.academicsApi.listAssessments(),
    }).subscribe({
      next: ({ schoolYears, subjects, classRooms, students, assessments }) => {
        this.schoolYears = schoolYears;
        this.subjects = subjects;
        this.classRooms = classRooms;
        this.students = students;
        this.assessments = assessments;
        this.applyDefaults();
        if (assessments[0]) {
          this.selectAssessment(assessments[0].id);
        }
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger les données pédagogiques.';
        this.loading = false;
      },
    });
  }

  createSchoolYear() {
    if (this.schoolYearForm.invalid) {
      this.schoolYearForm.markAllAsTouched();
      return;
    }

    const value = this.schoolYearForm.getRawValue();
    const payload: SchoolYearPayload = {
      name: value.name ?? '',
      startDate: this.toIsoDate(value.startDate),
      endDate: this.toIsoDate(value.endDate),
      periodType: value.periodType ?? 'TRIMESTER',
      isActive: value.isActive ?? false,
    };

    this.academicsApi.createSchoolYear(payload).subscribe({
      next: (schoolYear) => {
        this.schoolYears = [schoolYear, ...this.schoolYears.filter((item) => item.id !== schoolYear.id)];
        if (schoolYear.isActive) {
          this.schoolYears = this.schoolYears.map((item) =>
            item.id === schoolYear.id ? item : { ...item, isActive: false },
          );
        }
        this.assessmentForm.patchValue({
          schoolYearId: schoolYear.id,
          periodId: schoolYear.periods[0]?.id ?? '',
        });
      },
      error: () => {
        this.error = 'Création de l’année scolaire impossible.';
      },
    });
  }

  createSubject() {
    if (this.subjectForm.invalid) {
      this.subjectForm.markAllAsTouched();
      return;
    }

    const value = this.subjectForm.getRawValue();
    const payload: SubjectPayload = {
      code: value.code ?? '',
      name: value.name ?? '',
      level: this.optionalText(value.level),
      coefficient: this.toNumber(value.coefficient) ?? 1,
    };

    this.academicsApi.createSubject(payload).subscribe({
      next: (subject) => {
        this.subjects = [...this.subjects, subject].sort((a, b) => a.name.localeCompare(b.name, 'fr-FR'));
        this.assessmentForm.patchValue({
          subjectId: subject.id,
          coefficient: subject.coefficient.toString(),
        });
        this.subjectForm.reset({ code: '', name: '', level: '', coefficient: '1' });
      },
      error: () => {
        this.error = 'Création de la matière impossible.';
      },
    });
  }

  syncPeriodForYear() {
    const periods = this.periodsFor(this.assessmentForm.controls.schoolYearId.value);
    this.assessmentForm.patchValue({ periodId: periods[0]?.id ?? '' });
  }

  createAssessment() {
    if (!this.canManageNotes || this.assessmentForm.invalid || this.saving) {
      this.assessmentForm.markAllAsTouched();
      return;
    }

    const value = this.assessmentForm.getRawValue();
    const payload: AssessmentPayload = {
      title: value.title ?? '',
      type: value.type ?? 'COMPOSITION',
      coefficient: this.toNumber(value.coefficient) ?? 1,
      maxScore: this.toNumber(value.maxScore) ?? 20,
      assessedAt: this.optionalDate(value.assessedAt),
      schoolYearId: value.schoolYearId ?? '',
      periodId: value.periodId ?? '',
      subjectId: value.subjectId ?? '',
      classRoomId: value.classRoomId ?? '',
    };

    this.saving = true;
    this.error = '';

    this.academicsApi.createAssessment(payload).subscribe({
      next: (assessment) => {
        this.assessments = [assessment, ...this.assessments];
        this.selectAssessment(assessment.id);
        this.assessmentForm.patchValue({ title: '' });
        this.saving = false;
      },
      error: () => {
        this.error = 'Création de l’évaluation impossible.';
        this.saving = false;
      },
    });
  }

  selectAssessment(id: string) {
    this.selectedAssessmentId = id;
    this.academicsApi.listGrades(id).subscribe({
      next: (grades) => {
        this.grades = grades;
        this.prepareGradeRows();
      },
      error: () => {
        this.error = 'Impossible de charger les notes.';
      },
    });
  }

  saveGrades() {
    const assessment = this.selectedAssessment;
    if (!assessment || this.saving) {
      return;
    }

    const grades = this.gradeRows
      .filter((row) => row.score !== '')
      .map((row) => ({
        studentId: row.student.id,
        score: Number(row.score),
        appreciation: this.optionalText(row.appreciation),
      }));

    if (!grades.length) {
      this.error = 'Saisissez au moins une note.';
      return;
    }

    this.saving = true;
    this.error = '';

    this.academicsApi.saveGrades({ assessmentId: assessment.id, grades }).subscribe({
      next: (savedGrades) => {
        this.grades = savedGrades;
        this.assessments = this.assessments.map((item) =>
          item.id === assessment.id ? { ...item, gradesCount: savedGrades.length } : item,
        );
        this.prepareGradeRows();
        this.saving = false;
      },
      error: () => {
        this.error = 'Enregistrement des notes impossible.';
        this.saving = false;
      },
    });
  }

  updateAssessmentStatus(assessment: Assessment, status: 'SUBMITTED' | 'VALIDATED') {
    if (status === 'VALIDATED' && !this.canValidateNotes) {
      return;
    }

    this.academicsApi.updateAssessmentStatus(assessment.id, status).subscribe({
      next: (updated) => {
        this.assessments = this.assessments.map((item) => (item.id === updated.id ? updated : item));
      },
      error: () => {
        this.error = 'Mise à jour du statut impossible.';
      },
    });
  }

  periodsFor(schoolYearId?: string | null): AcademicPeriod[] {
    return this.schoolYears.find((schoolYear) => schoolYear.id === schoolYearId)?.periods ?? [];
  }

  typeLabel(type: AssessmentType) {
    return this.assessmentTypes.find((option) => option.value === type)?.label ?? type;
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

  private prepareGradeRows() {
    const assessment = this.selectedAssessment;
    if (!assessment) {
      this.gradeRows = [];
      return;
    }

    const gradesByStudent = new Map(this.grades.map((grade) => [grade.studentId, grade]));
    this.gradeRows = this.students
      .filter((student) => student.classRoom?.id === assessment.classRoomId)
      .sort((left, right) => left.fullName.localeCompare(right.fullName, 'fr-FR'))
      .map((student) => {
        const grade = gradesByStudent.get(student.id);
        return {
          student,
          score: grade?.score?.toString() ?? '',
          appreciation: grade?.appreciation ?? '',
        };
      });
  }

  private applyDefaults() {
    const schoolYear = this.schoolYears.find((item) => item.isActive) ?? this.schoolYears[0];
    this.assessmentForm.patchValue({
      schoolYearId: schoolYear?.id ?? '',
      periodId: schoolYear?.periods[0]?.id ?? '',
      subjectId: this.subjects[0]?.id ?? '',
      classRoomId: this.classRooms[0]?.id ?? '',
    });
  }

  private todayIso() {
    return new Date().toISOString().slice(0, 10);
  }

  private defaultSchoolYearName() {
    const year = new Date().getFullYear();
    return `${year}-${year + 1}`;
  }

  private optionalText(value?: string | null) {
    const trimmed = value?.trim();
    return trimmed || null;
  }

  private optionalDate(value?: string | null) {
    const trimmed = value?.trim();
    return trimmed ? this.toIsoDate(trimmed) : null;
  }

  private toIsoDate(value?: string | null) {
    return value ? `${value}T00:00:00.000Z` : '';
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
