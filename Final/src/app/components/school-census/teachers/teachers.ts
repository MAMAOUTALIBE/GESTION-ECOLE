import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { RouterModule } from '@angular/router';
import { CensusApiService } from '../shared/census-api.service';
import { CensusPerson, ClassRoom, School } from '../shared/school-census.models';
import { openPrintWindow, printPersonCards } from '../shared/card-print-utils';
import { ExportColumn, downloadCsv, downloadExcel, printTable } from '../shared/export-utils';

@Component({
  selector: 'app-teachers',
  imports: [CommonModule, ReactiveFormsModule, RouterModule],
  templateUrl: './teachers.html',
  styleUrl: './teachers.scss',
})
export class Teachers {
  private api = inject(CensusApiService);
  private formBuilder = inject(FormBuilder);
  private sanitizer = inject(DomSanitizer);

  teachers: CensusPerson[] = [];
  schools: School[] = [];
  classRooms: ClassRoom[] = [];
  selectedCreateClassRoomIds: string[] = [];
  selectedQr: SafeHtml | null = null;
  selectedPerson: CensusPerson | null = null;
  loading = false;
  saving = false;
  printingCards = false;
  assigningTeacherId = '';
  error = '';

  private teacherExportColumns: ExportColumn<CensusPerson>[] = [
    { header: 'Matricule', value: (teacher) => teacher.uniqueCode },
    { header: 'Prénom', value: (teacher) => teacher.firstName },
    { header: 'Nom', value: (teacher) => teacher.lastName },
    { header: 'Genre', value: (teacher) => this.genderLabel(teacher.gender) },
    { header: 'Date de naissance', value: (teacher) => this.dateLabel(teacher.birthDate) },
    { header: 'École', value: (teacher) => teacher.school.name },
    { header: 'Région', value: (teacher) => teacher.school.region?.name },
    { header: 'Téléphone', value: (teacher) => teacher.phone },
    { header: 'Matière', value: (teacher) => teacher.subject },
    { header: 'Diplôme', value: (teacher) => teacher.diploma },
    { header: 'Classes', value: (teacher) => teacher.classes?.map((classRoom) => classRoom.name).join(', ') },
  ];

  form = this.formBuilder.group({
    firstName: ['', [Validators.required, Validators.minLength(2)]],
    lastName: ['', [Validators.required, Validators.minLength(2)]],
    gender: ['MALE', Validators.required],
    birthDate: [''],
    photoUrl: [''],
    phone: [''],
    subject: [''],
    diploma: [''],
    schoolId: ['', Validators.required],
  });

  ngOnInit() {
    this.load();
  }

  load() {
    this.loading = true;
    this.error = '';

    this.api.metadata().subscribe({
      next: (metadata) => {
        this.schools = metadata.schools;
        const firstSchoolId = this.schools[0]?.id;
        if (firstSchoolId && !this.form.controls.schoolId.value) {
          this.form.patchValue({ schoolId: firstSchoolId });
          this.syncClassRooms(firstSchoolId);
        }
      },
      error: () => {
        this.error = 'Impossible de charger les écoles.';
        this.loading = false;
      },
    });

    this.api.teachers().subscribe({
      next: (teachers) => {
        this.teachers = teachers;
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger les enseignants.';
        this.loading = false;
      },
    });
  }

  createTeacher() {
    if (this.form.invalid || this.saving) {
      this.form.markAllAsTouched();
      return;
    }

    this.saving = true;
    this.error = '';
    const value = this.form.getRawValue();

    this.api
      .createTeacher({
        firstName: value.firstName ?? '',
        lastName: value.lastName ?? '',
        gender: (value.gender ?? 'MALE') as any,
        birthDate: value.birthDate || undefined,
        photoUrl: value.photoUrl || undefined,
        phone: value.phone || undefined,
        subject: value.subject || undefined,
        diploma: value.diploma || undefined,
        schoolId: value.schoolId ?? '',
        classRoomIds: this.selectedCreateClassRoomIds,
      })
      .subscribe({
        next: (teacher) => {
          this.teachers = [teacher, ...this.teachers];
          this.showQr(teacher);
          this.form.patchValue({
            firstName: '',
            lastName: '',
            birthDate: '',
            photoUrl: '',
            phone: '',
            subject: '',
            diploma: '',
          });
          this.form.markAsPristine();
          this.selectedCreateClassRoomIds = [];
          this.saving = false;
        },
        error: (error) => {
          this.error =
            this.apiErrorMessage(error) || 'Enregistrement impossible. Vérifiez les informations saisies.';
          this.saving = false;
        },
      });
  }

  onSchoolChange() {
    const schoolId = this.form.controls.schoolId.value ?? '';
    this.syncClassRooms(schoolId);
    this.selectedCreateClassRoomIds = [];
  }

  onCreateClassesChange(event: Event) {
    this.selectedCreateClassRoomIds = this.selectedClassIds(event.target as HTMLSelectElement);
  }

  showQr(person: CensusPerson) {
    this.selectedPerson = person;
    if (person.qrSvg) {
      this.selectedQr = this.sanitizer.bypassSecurityTrustHtml(person.qrSvg);
      return;
    }

    this.api.teacher(person.id).subscribe({
      next: (teacher) => {
        this.selectedPerson = teacher;
        this.selectedQr = teacher.qrSvg ? this.sanitizer.bypassSecurityTrustHtml(teacher.qrSvg) : null;
      },
      error: () => {
        this.error = 'Impossible de charger le QR code.';
      },
    });
  }

  exportCsv() {
    downloadCsv('enseignants-recenses.csv', this.teachers, this.teacherExportColumns);
  }

  exportExcel() {
    downloadExcel('enseignants-recenses.xls', this.teachers, this.teacherExportColumns);
  }

  printReport() {
    printTable('Liste des enseignants recensés', this.teachers, this.teacherExportColumns);
  }

  classesForSchool(schoolId?: string) {
    return this.schools.find((school) => school.id === schoolId)?.classes ?? [];
  }

  selectedClassIds(select: HTMLSelectElement) {
    return Array.from(select.selectedOptions).map((option) => option.value);
  }

  hasTeacherClass(teacher: CensusPerson, classRoomId: string) {
    return Boolean(teacher.classes?.some((classRoom) => classRoom.id === classRoomId));
  }

  assignClasses(teacher: CensusPerson, classRoomIds: string[]) {
    if (this.assigningTeacherId) {
      return;
    }

    this.assigningTeacherId = teacher.id;
    this.error = '';
    this.api.assignTeacherClasses(teacher.id, classRoomIds).subscribe({
      next: (updated) => {
        this.teachers = this.teachers.map((item) => (item.id === updated.id ? updated : item));
        if (this.selectedPerson?.id === updated.id) {
          this.selectedPerson = updated;
        }
        this.assigningTeacherId = '';
      },
      error: () => {
        this.error = 'Affectation impossible. Vérifiez les classes sélectionnées.';
        this.assigningTeacherId = '';
      },
    });
  }

  printCards() {
    if (this.printingCards) {
      return;
    }

    const printWindow = openPrintWindow('Cartes enseignants');
    if (!printWindow) {
      return;
    }

    this.printingCards = true;
    this.error = '';
    this.api.teacherCards().subscribe({
      next: (teachers) => {
        printPersonCards('Cartes enseignants', teachers, 'TEACHER', printWindow);
        this.printingCards = false;
      },
      error: () => {
        this.error = 'Impossible de préparer les cartes enseignants.';
        printWindow.close();
        this.printingCards = false;
      },
    });
  }

  private genderLabel(gender: string) {
    const labels: Record<string, string> = {
      FEMALE: 'Femme',
      MALE: 'Homme',
      OTHER: 'Autre',
    };
    return labels[gender] ?? gender;
  }

  private dateLabel(value?: string | null) {
    if (!value) {
      return '';
    }
    return new Intl.DateTimeFormat('fr-FR').format(new Date(value));
  }

  private syncClassRooms(schoolId: string) {
    this.classRooms = this.classesForSchool(schoolId);
  }

  private apiErrorMessage(error: any) {
    const message = error?.error?.message;
    return Array.isArray(message) ? message.join(' ') : message;
  }
}
