import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { CensusApiService } from '../shared/census-api.service';
import { CensusPerson, ClassRoom, School } from '../shared/school-census.models';

@Component({
  selector: 'app-teacher-assignments',
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './teacher-assignments.html',
  styleUrl: './teacher-assignments.scss',
})
export class TeacherAssignments {
  private censusApi = inject(CensusApiService);

  teachers: CensusPerson[] = [];
  schools: School[] = [];
  selectedTeacherId = '';
  selectedSchoolId = '';
  selectedClassRoomIds: string[] = [];
  searchTerm = '';
  loading = false;
  saving = false;
  error = '';

  ngOnInit() {
    this.load();
  }

  get selectedTeacher() {
    return this.teachers.find((teacher) => teacher.id === this.selectedTeacherId) ?? null;
  }

  get selectedSchool() {
    return this.schools.find((school) => school.id === this.selectedSchoolId) ?? null;
  }

  get availableClasses(): ClassRoom[] {
    return (this.selectedSchool?.classes ?? []).sort((left, right) => left.name.localeCompare(right.name, 'fr-FR'));
  }

  get filteredTeachers() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.teachers.filter((teacher) => {
      const matchesSchool = !this.selectedSchoolId || teacher.school.id === this.selectedSchoolId;
      const searchable = this.normalizeSearch(
        [teacher.fullName, teacher.uniqueCode, teacher.subject, teacher.school.name, teacher.classes?.map((item) => item.name).join(' ')].join(
          ' ',
        ),
      );

      return matchesSchool && (!search || searchable.includes(search));
    });
  }

  get totals() {
    const rows = this.filteredTeachers;
    return {
      teachers: rows.length,
      assigned: rows.filter((teacher) => teacher.classes?.length).length,
      unassigned: rows.filter((teacher) => !teacher.classes?.length).length,
      assignments: rows.reduce((sum, teacher) => sum + (teacher.classes?.length ?? 0), 0),
    };
  }

  load() {
    this.loading = true;
    this.error = '';

    this.censusApi.metadata().subscribe({
      next: (metadata) => {
        this.schools = metadata.schools;
        if (!this.selectedSchoolId) {
          this.selectedSchoolId = this.schools[0]?.id ?? '';
        }
        this.loadTeachers();
      },
      error: () => {
        this.error = 'Impossible de charger les établissements.';
        this.loading = false;
      },
    });
  }

  loadTeachers() {
    this.censusApi.teachers().subscribe({
      next: (teachers) => {
        this.teachers = teachers;
        const currentTeacher = this.selectedTeacherId
          ? teachers.find((teacher) => teacher.id === this.selectedTeacherId)
          : null;
        this.selectTeacher(currentTeacher ?? this.filteredTeachers[0] ?? teachers[0] ?? null);
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger les enseignants.';
        this.loading = false;
      },
    });
  }

  onSchoolChange() {
    this.selectedTeacherId = '';
    this.selectedClassRoomIds = [];
    this.selectTeacher(this.filteredTeachers[0] ?? null);
  }

  selectTeacher(teacher: CensusPerson | null) {
    this.selectedTeacherId = teacher?.id ?? '';
    if (teacher && teacher.school.id !== this.selectedSchoolId) {
      this.selectedSchoolId = teacher.school.id;
    }
    this.selectedClassRoomIds = teacher?.classes?.map((classRoom) => classRoom.id) ?? [];
  }

  toggleClass(classRoomId: string, checked: boolean) {
    if (checked) {
      this.selectedClassRoomIds = Array.from(new Set([...this.selectedClassRoomIds, classRoomId]));
      return;
    }

    this.selectedClassRoomIds = this.selectedClassRoomIds.filter((id) => id !== classRoomId);
  }

  saveAssignments() {
    const teacher = this.selectedTeacher;
    if (!teacher || this.saving) {
      return;
    }

    this.saving = true;
    this.error = '';

    this.censusApi.assignTeacherClasses(teacher.id, this.selectedClassRoomIds).subscribe({
      next: (updated) => {
        this.teachers = this.teachers.map((item) => (item.id === updated.id ? updated : item));
        this.selectTeacher(updated);
        this.saving = false;
      },
      error: () => {
        this.error = 'Affectation impossible. Vérifiez les classes sélectionnées.';
        this.saving = false;
      },
    });
  }

  isClassSelected(classRoomId: string) {
    return this.selectedClassRoomIds.includes(classRoomId);
  }

  resetFilters() {
    this.searchTerm = '';
    this.selectedSchoolId = this.schools[0]?.id ?? '';
    this.onSchoolChange();
  }

  formatNumber(value: number) {
    return value.toLocaleString('fr-FR');
  }

  private normalizeSearch(value?: string | null) {
    return (value ?? '')
      .toLocaleLowerCase('fr-FR')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '');
  }
}
