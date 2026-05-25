import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import {
  ACADEMIC_VALIDATION_ROLES,
  ACADEMIC_WRITE_ROLES,
  AuthService,
} from '../../../shared/services/auth.service';
import { AcademicsApiService, ParentPayload } from '../shared/academics-api.service';
import { CensusApiService } from '../shared/census-api.service';
import { CensusPerson, ParentContact, ParentRelationType } from '../shared/school-census.models';
import { ExportColumn, downloadCsv, downloadExcel, printTable } from '../shared/export-utils';
import { PrivacyService } from '../../../shared/privacy/privacy.service';
import { RedactedNamePipe } from '../../../shared/privacy/redacted-name.pipe';
import { PrivacyBannerComponent } from '../../../shared/privacy/privacy-banner.component';
import { TranslateModule } from '@ngx-translate/core';

@Component({
  selector: 'app-parents',
  imports: [
    CommonModule,
    FormsModule,
    ReactiveFormsModule,
    RedactedNamePipe,
    PrivacyBannerComponent,
    TranslateModule,
  ],
  templateUrl: './parents.html',
  styleUrl: './parents.scss',
})
export class Parents {
  private auth = inject(AuthService);
  private academicsApi = inject(AcademicsApiService);
  private censusApi = inject(CensusApiService);
  private formBuilder = inject(FormBuilder);
  protected privacy = inject(PrivacyService);

  parents: ParentContact[] = [];
  students: CensusPerson[] = [];
  editingId = '';
  loading = false;
  saving = false;
  error = '';
  searchTerm = '';
  selectedRelation = '';

  relationOptions: Array<{ value: ParentRelationType; label: string }> = [
    { value: 'FATHER', label: 'Père' },
    { value: 'MOTHER', label: 'Mère' },
    { value: 'LEGAL_GUARDIAN', label: 'Tuteur légal' },
    { value: 'EMERGENCY_CONTACT', label: 'Urgence' },
    { value: 'OTHER', label: 'Autre' },
  ];

  private parentExportColumns: ExportColumn<ParentContact>[] = [
    { header: 'Parent', value: (parent) => this.privacy.displayName(parent, this.parentTarget(parent)) },
    { header: 'Téléphone', value: (parent) => this.privacy.canSeeFullName(this.parentTarget(parent)) ? parent.phone : '' },
    { header: 'Email', value: (parent) => this.privacy.canSeeFullName(this.parentTarget(parent)) ? parent.email : '' },
    { header: 'Profession', value: (parent) => parent.profession },
    { header: 'Langue', value: (parent) => parent.preferredLanguage },
    { header: 'Élèves', value: (parent) => this.linkedStudents(parent) },
    { header: 'Relations', value: (parent) => parent.students.map((link) => this.relationLabel(link.relation)).join(', ') },
  ];

  parentTarget(parent: ParentContact) {
    // Pour un parent, le scope d'autorisation est défini par l'école/région
    // du premier élève lié (au moins un élève dans le scope user = autorisation).
    // v1 : on prend le premier lien comme target ; v2 itérera sur tous les liens.
    const firstLink = parent.students[0];
    return {
      schoolId: firstLink?.student.school?.id,
      regionId: firstLink?.student.school?.region?.id,
    };
  }

  form = this.formBuilder.group({
    firstName: ['', [Validators.required, Validators.minLength(2)]],
    lastName: ['', [Validators.required, Validators.minLength(2)]],
    phone: ['', [Validators.required, Validators.minLength(6)]],
    email: [''],
    profession: [''],
    address: [''],
    preferredLanguage: ['Français'],
    studentId: ['', Validators.required],
    relation: ['LEGAL_GUARDIAN' as ParentRelationType, Validators.required],
    isPrimary: [true],
    isEmergencyContact: [false],
  });

  get canManageParents() {
    return this.auth.hasAnyRole(ACADEMIC_WRITE_ROLES);
  }

  get canDeleteParents() {
    return this.auth.hasAnyRole(ACADEMIC_VALIDATION_ROLES);
  }

  get filteredParents() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.parents.filter((parent) => {
      const matchesRelation =
        !this.selectedRelation || parent.students.some((link) => link.relation === this.selectedRelation);
      const canSee = this.privacy.canSeeFullName(this.parentTarget(parent));
      const searchable = this.normalizeSearch(
        [
          canSee ? parent.fullName : this.privacy.displayName(parent, this.parentTarget(parent)),
          canSee ? parent.phone : '',
          canSee ? parent.email : '',
          parent.profession,
          canSee ? parent.address : '',
          parent.preferredLanguage,
          this.linkedStudents(parent),
        ].join(' '),
      );

      return matchesRelation && (!search || searchable.includes(search));
    });
  }

  get parentTotals() {
    const rows = this.filteredParents;
    return {
      parents: rows.length,
      linkedStudents: new Set(rows.flatMap((parent) => parent.students.map((link) => link.student.id))).size,
      emergencyContacts: rows.filter((parent) => parent.students.some((link) => link.isEmergencyContact)).length,
      verified: rows.filter((parent) => parent.otpVerifiedAt).length,
    };
  }

  ngOnInit() {
    this.load();
  }

  load() {
    this.loading = true;
    this.error = '';

    this.censusApi.students().subscribe({
      next: (students) => {
        this.students = students;
        const firstStudentId = students[0]?.id;
        if (firstStudentId && !this.form.controls.studentId.value) {
          this.form.patchValue({ studentId: firstStudentId });
        }
      },
      error: () => {
        this.error = 'Impossible de charger les élèves.';
      },
    });

    this.academicsApi.listParents().subscribe({
      next: (parents) => {
        this.parents = parents;
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger les parents.';
        this.loading = false;
      },
    });
  }

  saveParent() {
    if (!this.canManageParents || this.form.invalid || this.saving) {
      this.form.markAllAsTouched();
      return;
    }

    const value = this.form.getRawValue();
    this.saving = true;
    this.error = '';

    const request = this.editingId
      ? this.academicsApi.updateParent(this.editingId, {
          firstName: value.firstName ?? '',
          lastName: value.lastName ?? '',
          phone: value.phone ?? '',
          email: this.optionalText(value.email),
          profession: this.optionalText(value.profession),
          address: this.optionalText(value.address),
          preferredLanguage: this.optionalText(value.preferredLanguage),
        })
      : this.academicsApi.createParent(this.toCreatePayload(value));

    request.subscribe({
      next: (parent) => {
        this.parents = this.editingId
          ? this.parents.map((item) => (item.id === parent.id ? parent : item))
          : [parent, ...this.parents];
        this.resetForm();
        this.saving = false;
      },
      error: () => {
        this.error = 'Enregistrement impossible. Vérifiez le téléphone et les liens élève.';
        this.saving = false;
      },
    });
  }

  editParent(parent: ParentContact) {
    if (!this.canManageParents) {
      return;
    }

    const firstLink = parent.students[0];
    this.editingId = parent.id;
    this.form.patchValue({
      firstName: parent.firstName,
      lastName: parent.lastName,
      phone: parent.phone,
      email: parent.email ?? '',
      profession: parent.profession ?? '',
      address: parent.address ?? '',
      preferredLanguage: parent.preferredLanguage ?? 'Français',
      studentId: firstLink?.student.id ?? this.students[0]?.id ?? '',
      relation: firstLink?.relation ?? 'LEGAL_GUARDIAN',
      isPrimary: firstLink?.isPrimary ?? true,
      isEmergencyContact: firstLink?.isEmergencyContact ?? false,
    });
  }

  deleteParent(parent: ParentContact) {
    const label = this.privacy.displayName(parent, this.parentTarget(parent)) || 'ce contact';
    if (!this.canDeleteParents || !window.confirm(`Supprimer ${label} ?`)) {
      return;
    }

    this.academicsApi.deleteParent(parent.id).subscribe({
      next: () => {
        this.parents = this.parents.filter((item) => item.id !== parent.id);
        if (this.editingId === parent.id) {
          this.resetForm();
        }
      },
      error: () => {
        this.error = 'Suppression impossible pour ce parent.';
      },
    });
  }

  resetForm() {
    this.editingId = '';
    this.form.reset({
      firstName: '',
      lastName: '',
      phone: '',
      email: '',
      profession: '',
      address: '',
      preferredLanguage: 'Français',
      studentId: this.students[0]?.id ?? '',
      relation: 'LEGAL_GUARDIAN',
      isPrimary: true,
      isEmergencyContact: false,
    });
  }

  resetFilters() {
    this.searchTerm = '';
    this.selectedRelation = '';
  }

  exportCsv() {
    downloadCsv('parents.csv', this.filteredParents, this.parentExportColumns);
  }

  exportExcel() {
    downloadExcel('parents.xls', this.filteredParents, this.parentExportColumns);
  }

  printReport() {
    printTable('Liste des parents et tuteurs', this.filteredParents, this.parentExportColumns);
  }

  linkedStudents(parent: ParentContact) {
    return parent.students
      .map((link) =>
        this.privacy.displayName(link.student, {
          schoolId: link.student.school?.id,
          regionId: link.student.school?.region?.id,
        }),
      )
      .join(', ');
  }

  relationLabel(relation: ParentRelationType) {
    return this.relationOptions.find((option) => option.value === relation)?.label ?? relation;
  }

  private toCreatePayload(value: ReturnType<typeof this.form.getRawValue>): ParentPayload {
    return {
      firstName: value.firstName ?? '',
      lastName: value.lastName ?? '',
      phone: value.phone ?? '',
      email: this.optionalText(value.email),
      profession: this.optionalText(value.profession),
      address: this.optionalText(value.address),
      preferredLanguage: this.optionalText(value.preferredLanguage),
      links: [
        {
          studentId: value.studentId ?? '',
          relation: value.relation ?? 'LEGAL_GUARDIAN',
          isPrimary: value.isPrimary ?? false,
          isEmergencyContact: value.isEmergencyContact ?? false,
        },
      ],
    };
  }

  private optionalText(value?: string | null) {
    const trimmed = value?.trim();
    return trimmed || null;
  }

  private normalizeSearch(value?: string | null) {
    return (value ?? '')
      .toLocaleLowerCase('fr-FR')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '');
  }
}
