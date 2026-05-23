import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { AuthService, CLASS_MANAGEMENT_ROLES } from '../../../shared/services/auth.service';
import { ClassRoomPayload, SchoolAdminService } from '../shared/school-admin.service';
import { ClassRoom, School } from '../shared/school-census.models';
import { ExportColumn, downloadCsv, downloadExcel, printTable } from '../shared/export-utils';

@Component({
  selector: 'app-classes',
  imports: [CommonModule, FormsModule, ReactiveFormsModule],
  templateUrl: './classes.html',
  styleUrl: './classes.scss',
})
export class Classes {
  private auth = inject(AuthService);
  private schoolApi = inject(SchoolAdminService);
  private formBuilder = inject(FormBuilder);

  classRooms: ClassRoom[] = [];
  schools: School[] = [];
  editingId = '';
  loading = false;
  saving = false;
  error = '';
  searchTerm = '';
  selectedSchoolId = '';
  selectedRegionId = '';
  selectedLevel = '';
  selectedCapacityStatus = '';

  private classExportColumns: ExportColumn<ClassRoom>[] = [
    { header: 'Classe', value: (classRoom) => classRoom.name },
    { header: 'Niveau', value: (classRoom) => classRoom.level },
    { header: 'Effectif max', value: (classRoom) => classRoom.maxStudents },
    { header: 'Année scolaire', value: (classRoom) => classRoom.schoolYear },
    { header: 'École', value: (classRoom) => classRoom.school?.name },
    { header: 'Région', value: (classRoom) => classRoom.school?.region?.name },
    { header: 'Élèves', value: (classRoom) => classRoom.studentsCount ?? 0 },
    { header: 'Enseignants', value: (classRoom) => classRoom.teachersCount ?? 0 },
  ];

  form = this.formBuilder.group({
    name: ['', [Validators.required, Validators.minLength(1)]],
    level: [''],
    maxStudents: [''],
    schoolYear: [''],
    schoolId: ['', Validators.required],
  });

  get canManageClasses() {
    return this.auth.hasAnyRole(CLASS_MANAGEMENT_ROLES);
  }

  get classLevels() {
    return Array.from(new Set(this.classRooms.map((classRoom) => classRoom.level).filter(Boolean) as string[])).sort();
  }

  get regions() {
    const regions = new Map<string, NonNullable<School['region']>>();
    this.schools.forEach((school) => {
      if (school.region) {
        regions.set(school.region.id, school.region);
      }
    });
    return Array.from(regions.values()).sort((a, b) => a.name.localeCompare(b.name, 'fr-FR'));
  }

  get filteredClassRooms() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.classRooms.filter((classRoom) => {
      const matchesSchool = !this.selectedSchoolId || classRoom.schoolId === this.selectedSchoolId;
      const matchesRegion = !this.selectedRegionId || classRoom.school?.regionId === this.selectedRegionId;
      const matchesLevel = !this.selectedLevel || classRoom.level === this.selectedLevel;
      const matchesCapacity =
        !this.selectedCapacityStatus ||
        (this.selectedCapacityStatus === 'available' &&
          this.hasDeclaredCapacity(classRoom) &&
          !this.isFull(classRoom) &&
          !this.isOverloaded(classRoom)) ||
        (this.selectedCapacityStatus === 'full' && this.isFull(classRoom)) ||
        (this.selectedCapacityStatus === 'overloaded' && this.isOverloaded(classRoom)) ||
        (this.selectedCapacityStatus === 'missing' && !this.hasDeclaredCapacity(classRoom));
      const searchable = this.normalizeSearch(
        [
          classRoom.name,
          classRoom.level,
          classRoom.schoolYear,
          classRoom.school?.name,
          classRoom.school?.code,
          classRoom.school?.region?.name,
          classRoom.school?.prefecture,
          classRoom.school?.commune,
        ].join(' '),
      );

      return matchesSchool && matchesRegion && matchesLevel && matchesCapacity && (!search || searchable.includes(search));
    });
  }

  get classTotals() {
    const rows = this.filteredClassRooms;
    const capacity = rows.reduce((sum, classRoom) => sum + (classRoom.maxStudents ?? 0), 0);
    const students = rows.reduce((sum, classRoom) => sum + (classRoom.studentsCount ?? 0), 0);

    return {
      classes: rows.length,
      students,
      teachers: rows.reduce((sum, classRoom) => sum + (classRoom.teachersCount ?? 0), 0),
      capacity,
      fillRate: capacity ? Math.round((students / capacity) * 100) : 0,
      overloaded: rows.filter((classRoom) => this.isOverloaded(classRoom)).length,
    };
  }

  ngOnInit() {
    this.load();
  }

  load() {
    this.loading = true;
    this.error = '';

    this.schoolApi.listSchools().subscribe({
      next: (schools) => {
        this.schools = schools;
        const firstSchoolId = schools[0]?.id;
        if (firstSchoolId && !this.form.controls.schoolId.value) {
          this.form.patchValue({ schoolId: firstSchoolId });
        }
      },
      error: () => {
        this.error = 'Impossible de charger les écoles.';
      },
    });

    this.schoolApi.listClasses().subscribe({
      next: (classRooms) => {
        this.classRooms = classRooms;
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger les classes.';
        this.loading = false;
      },
    });
  }

  saveClass() {
    if (!this.canManageClasses || this.form.invalid || this.saving) {
      this.form.markAllAsTouched();
      return;
    }

    this.saving = true;
    this.error = '';
    const value = this.form.getRawValue();
    const payload: ClassRoomPayload = {
      name: value.name ?? '',
      level: this.optionalText(value.level),
      maxStudents: this.toNumber(value.maxStudents),
      schoolYear: this.optionalText(value.schoolYear),
      schoolId: value.schoolId ?? '',
    };
    const request = this.editingId
      ? this.schoolApi.updateClass(this.editingId, payload)
      : this.schoolApi.createClass(payload);

    request.subscribe({
      next: (classRoom) => {
        this.classRooms = this.editingId
          ? this.classRooms.map((item) => (item.id === classRoom.id ? classRoom : item))
          : [classRoom, ...this.classRooms];
        this.resetForm();
        this.saving = false;
      },
      error: () => {
        this.error = 'Enregistrement impossible. Vérifiez le nom de classe et l’école.';
        this.saving = false;
      },
    });
  }

  editClass(classRoom: ClassRoom) {
    if (!this.canManageClasses) {
      return;
    }

    this.editingId = classRoom.id;
    this.form.patchValue({
      name: classRoom.name,
      level: classRoom.level ?? '',
      maxStudents: classRoom.maxStudents?.toString() ?? '',
      schoolYear: classRoom.schoolYear ?? '',
      schoolId: classRoom.schoolId,
    });
  }

  deleteClass(classRoom: ClassRoom) {
    if (!this.canManageClasses || !window.confirm(`Supprimer la classe ${classRoom.name} ?`)) {
      return;
    }

    this.schoolApi.deleteClass(classRoom.id).subscribe({
      next: () => {
        this.classRooms = this.classRooms.filter((item) => item.id !== classRoom.id);
        if (this.editingId === classRoom.id) {
          this.resetForm();
        }
      },
      error: () => {
        this.error = 'Suppression impossible : cette classe contient déjà des élèves.';
      },
    });
  }

  resetForm() {
    this.editingId = '';
    this.form.reset({
      name: '',
      level: '',
      maxStudents: '',
      schoolYear: '',
      schoolId: this.schools[0]?.id ?? '',
    });
  }

  resetFilters() {
    this.searchTerm = '';
    this.selectedSchoolId = '';
    this.selectedRegionId = '';
    this.selectedLevel = '';
    this.selectedCapacityStatus = '';
  }

  exportCsv() {
    downloadCsv('classes.csv', this.filteredClassRooms, this.classExportColumns);
  }

  exportExcel() {
    downloadExcel('classes.xls', this.filteredClassRooms, this.classExportColumns);
  }

  printReport() {
    printTable('Liste des classes', this.filteredClassRooms, this.classExportColumns);
  }

  capacityRate(classRoom: ClassRoom) {
    if (!classRoom.maxStudents) {
      return 0;
    }
    return Math.min(100, Math.round(((classRoom.studentsCount ?? 0) / classRoom.maxStudents) * 100));
  }

  capacityBarClass(classRoom: ClassRoom) {
    if (this.isOverloaded(classRoom)) {
      return 'bg-danger';
    }
    if (this.isFull(classRoom)) {
      return 'bg-warning';
    }
    return 'bg-success';
  }

  isOverloaded(classRoom: ClassRoom) {
    return this.hasDeclaredCapacity(classRoom) && (classRoom.studentsCount ?? 0) > (classRoom.maxStudents ?? 0);
  }

  isFull(classRoom: ClassRoom) {
    return this.hasDeclaredCapacity(classRoom) && (classRoom.studentsCount ?? 0) === classRoom.maxStudents;
  }

  private hasDeclaredCapacity(classRoom: ClassRoom) {
    return classRoom.maxStudents !== null && classRoom.maxStudents !== undefined && classRoom.maxStudents > 0;
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
