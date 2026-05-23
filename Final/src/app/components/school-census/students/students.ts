import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { RouterModule } from '@angular/router';
import { ClassRoom, CensusPerson, School } from '../shared/school-census.models';
import { CensusApiService } from '../shared/census-api.service';
import { openPrintWindow, printPersonCards } from '../shared/card-print-utils';
import { ExportColumn, downloadCsv, downloadExcel, printTable } from '../shared/export-utils';

@Component({
  selector: 'app-students',
  imports: [CommonModule, ReactiveFormsModule, RouterModule],
  templateUrl: './students.html',
  styleUrl: './students.scss',
})
export class Students {
  private api = inject(CensusApiService);
  private formBuilder = inject(FormBuilder);
  private sanitizer = inject(DomSanitizer);

  students: CensusPerson[] = [];
  schools: School[] = [];
  classRooms: ClassRoom[] = [];
  selectedQr: SafeHtml | null = null;
  selectedPerson: CensusPerson | null = null;
  transferPerson: CensusPerson | null = null;
  transferClassRooms: ClassRoom[] = [];
  loading = false;
  saving = false;
  printingCards = false;
  assigningStudentId = '';
  transferring = false;
  error = '';

  private studentExportColumns: ExportColumn<CensusPerson>[] = [
    { header: 'Matricule', value: (student) => student.uniqueCode },
    { header: 'Prénom', value: (student) => student.firstName },
    { header: 'Nom', value: (student) => student.lastName },
    { header: 'Genre', value: (student) => this.genderLabel(student.gender) },
    { header: 'Date de naissance', value: (student) => this.dateLabel(student.birthDate) },
    { header: 'École', value: (student) => student.school.name },
    { header: 'Région', value: (student) => student.school.region?.name },
    { header: 'Classe', value: (student) => student.classRoom?.name ?? 'Non affecté' },
    { header: 'Tuteur', value: (student) => student.guardianName },
    { header: 'Téléphone tuteur', value: (student) => student.guardianPhone },
  ];

  form = this.formBuilder.group({
    firstName: ['', [Validators.required, Validators.minLength(2)]],
    lastName: ['', [Validators.required, Validators.minLength(2)]],
    gender: ['FEMALE', Validators.required],
    birthDate: [''],
    photoUrl: [''],
    guardianName: [''],
    guardianPhone: [''],
    schoolId: ['', Validators.required],
    classRoomId: [''],
  });

  transferForm = this.formBuilder.group({
    toSchoolId: ['', Validators.required],
    toClassRoomId: [''],
    reason: [''],
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

    this.api.students().subscribe({
      next: (students) => {
        this.students = students;
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger les élèves.';
        this.loading = false;
      },
    });
  }

  onSchoolChange() {
    const schoolId = this.form.controls.schoolId.value ?? '';
    this.syncClassRooms(schoolId);
    this.form.patchValue({ classRoomId: '' });
  }

  createStudent() {
    if (this.form.invalid || this.saving) {
      this.form.markAllAsTouched();
      return;
    }

    this.saving = true;
    this.error = '';
    const value = this.form.getRawValue();

    this.api
      .createStudent({
        firstName: value.firstName ?? '',
        lastName: value.lastName ?? '',
        gender: (value.gender ?? 'FEMALE') as any,
        birthDate: value.birthDate || undefined,
        photoUrl: value.photoUrl || undefined,
        guardianName: value.guardianName || undefined,
        guardianPhone: value.guardianPhone || undefined,
        schoolId: value.schoolId ?? '',
        classRoomId: value.classRoomId || undefined,
      })
      .subscribe({
        next: (student) => {
          this.students = [student, ...this.students];
          this.showQr(student);
          this.form.patchValue({
            firstName: '',
            lastName: '',
            birthDate: '',
            photoUrl: '',
            guardianName: '',
            guardianPhone: '',
            classRoomId: '',
          });
          this.form.markAsPristine();
          this.saving = false;
        },
        error: (error) => {
          this.error =
            this.apiErrorMessage(error) || 'Enregistrement impossible. Vérifiez les informations saisies.';
          this.saving = false;
        },
      });
  }

  showQr(person: CensusPerson) {
    this.selectedPerson = person;
    if (person.qrSvg) {
      this.selectedQr = this.sanitizer.bypassSecurityTrustHtml(person.qrSvg);
      return;
    }

    this.api.student(person.id).subscribe({
      next: (student) => {
        this.selectedPerson = student;
        this.selectedQr = student.qrSvg ? this.sanitizer.bypassSecurityTrustHtml(student.qrSvg) : null;
      },
      error: () => {
        this.error = 'Impossible de charger le QR code.';
      },
    });
  }

  classesForSchool(schoolId?: string) {
    return this.schools.find((school) => school.id === schoolId)?.classes ?? [];
  }

  assignClass(student: CensusPerson, classRoomId: string) {
    if (this.assigningStudentId) {
      return;
    }

    this.assigningStudentId = student.id;
    this.error = '';
    this.api.assignStudentClass(student.id, classRoomId || undefined).subscribe({
      next: (updated) => {
        this.students = this.students.map((item) => (item.id === updated.id ? updated : item));
        if (this.selectedPerson?.id === updated.id) {
          this.selectedPerson = updated;
        }
        this.assigningStudentId = '';
      },
      error: () => {
        this.error = 'Affectation impossible. Vérifiez la classe sélectionnée.';
        this.assigningStudentId = '';
      },
    });
  }

  prepareTransfer(student: CensusPerson) {
    this.transferPerson = student;
    this.transferForm.reset({
      toSchoolId: student.school.id,
      toClassRoomId: student.classRoom?.id ?? '',
      reason: '',
    });
    this.syncTransferClassRooms(student.school.id);
  }

  onTransferSchoolChange() {
    const schoolId = this.transferForm.controls.toSchoolId.value ?? '';
    this.transferForm.patchValue({ toClassRoomId: '' });
    this.syncTransferClassRooms(schoolId);
  }

  transferStudent() {
    if (!this.transferPerson || this.transferForm.invalid || this.transferring) {
      this.transferForm.markAllAsTouched();
      return;
    }

    const value = this.transferForm.getRawValue();
    this.transferring = true;
    this.error = '';
    this.api
      .transferStudent(this.transferPerson.id, {
        toSchoolId: value.toSchoolId ?? '',
        toClassRoomId: value.toClassRoomId || undefined,
        reason: value.reason || undefined,
      })
      .subscribe({
        next: (updated) => {
          this.students = this.students.map((item) => (item.id === updated.id ? updated : item));
          this.transferPerson = updated;
          this.transferring = false;
        },
        error: () => {
          this.error = 'Transfert impossible. Vérifiez l’école et la classe de destination.';
          this.transferring = false;
        },
      });
  }

  exportCsv() {
    downloadCsv('eleves-recenses.csv', this.students, this.studentExportColumns);
  }

  exportExcel() {
    downloadExcel('eleves-recenses.xls', this.students, this.studentExportColumns);
  }

  printReport() {
    printTable('Liste des élèves recensés', this.students, this.studentExportColumns);
  }

  printCards() {
    if (this.printingCards) {
      return;
    }

    const printWindow = openPrintWindow('Cartes scolaires élèves');
    if (!printWindow) {
      return;
    }

    this.printingCards = true;
    this.error = '';
    this.api.studentCards().subscribe({
      next: (students) => {
        printPersonCards('Cartes scolaires élèves', students, 'STUDENT', printWindow);
        this.printingCards = false;
      },
      error: () => {
        this.error = 'Impossible de préparer les cartes scolaires.';
        printWindow.close();
        this.printingCards = false;
      },
    });
  }

  private syncClassRooms(schoolId: string) {
    this.classRooms = this.schools.find((school) => school.id === schoolId)?.classes ?? [];
  }

  private syncTransferClassRooms(schoolId: string) {
    this.transferClassRooms = this.classesForSchool(schoolId);
  }

  private genderLabel(gender: string) {
    const labels: Record<string, string> = {
      FEMALE: 'Fille',
      MALE: 'Garçon',
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

  private apiErrorMessage(error: any) {
    const message = error?.error?.message;
    return Array.isArray(message) ? message.join(' ') : message;
  }
}
