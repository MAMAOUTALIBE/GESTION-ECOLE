import { CommonModule } from '@angular/common';
import { Component, DestroyRef, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { catchError, forkJoin, of } from 'rxjs';
import { AcademicsApiService } from '../shared/academics-api.service';
import { CensusApiService } from '../shared/census-api.service';
import { downloadCsv, downloadExcel, ExportColumn, printTable } from '../shared/export-utils';
import { SchoolLifeApiService, TimetableSlotRow as ApiSlotRow } from '../shared/schoollife-api.service';
import { CensusPerson, ClassRoom, Region, School, Subject } from '../shared/school-census.models';

type SchoolDay = 'monday' | 'tuesday' | 'wednesday' | 'thursday' | 'friday' | 'saturday';
type TimetableStatus = 'draft' | 'published' | 'conflict';

interface TimetableSlot {
  id: string;
  day: SchoolDay;
  startTime: string;
  endTime: string;
  schoolId: string;
  schoolName: string;
  regionId: string;
  region: string;
  classRoomId: string;
  classRoomName: string;
  level: string;
  subjectId: string;
  subjectName: string;
  teacherName: string;
  roomName: string;
  status: TimetableStatus;
  weeklyHours: number;
}

@Component({
  selector: 'app-timetable',
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './timetable.html',
  styleUrl: './timetable.scss',
})
export class Timetable {
  private censusApi = inject(CensusApiService);
  private academicsApi = inject(AcademicsApiService);
  private schoolLifeApi = inject(SchoolLifeApiService);
  private destroyRef = inject(DestroyRef);

  regions: Region[] = [];
  schools: School[] = [];
  subjects: Subject[] = [];
  teachers: CensusPerson[] = [];
  slots: TimetableSlot[] = [];
  loading = false;
  error = '';
  searchTerm = '';
  selectedRegionId = '';
  selectedSchoolId = '';
  selectedClassRoomId = '';
  selectedDay = '';
  selectedStatus = '';

  days: Array<{ value: SchoolDay; label: string; short: string }> = [
    { value: 'monday', label: 'Lundi', short: 'Lun' },
    { value: 'tuesday', label: 'Mardi', short: 'Mar' },
    { value: 'wednesday', label: 'Mercredi', short: 'Mer' },
    { value: 'thursday', label: 'Jeudi', short: 'Jeu' },
    { value: 'friday', label: 'Vendredi', short: 'Ven' },
    { value: 'saturday', label: 'Samedi', short: 'Sam' },
  ];

  private exportColumns: ExportColumn<TimetableSlot>[] = [
    { header: 'Jour', value: (slot) => this.dayLabel(slot.day) },
    { header: 'Début', value: (slot) => slot.startTime },
    { header: 'Fin', value: (slot) => slot.endTime },
    { header: 'Établissement', value: (slot) => slot.schoolName },
    { header: 'Région', value: (slot) => slot.region },
    { header: 'Classe', value: (slot) => slot.classRoomName },
    { header: 'Niveau', value: (slot) => slot.level },
    { header: 'Matière', value: (slot) => slot.subjectName },
    { header: 'Enseignant', value: (slot) => slot.teacherName },
    { header: 'Salle', value: (slot) => slot.roomName },
    { header: 'Statut', value: (slot) => this.statusLabel(slot.status) },
  ];

  ngOnInit() {
    this.load();
  }

  get filteredSchools() {
    return this.schools
      .filter((school) => !this.selectedRegionId || school.regionId === this.selectedRegionId)
      .sort((left, right) => left.name.localeCompare(right.name, 'fr-FR'));
  }

  get availableClassRooms() {
    const classes = this.schools.flatMap((school) =>
      (school.classes ?? []).map((classRoom) => ({
        ...classRoom,
        school,
      })),
    );

    return classes
      .filter((classRoom) => !this.selectedSchoolId || classRoom.schoolId === this.selectedSchoolId)
      .sort((left, right) => left.name.localeCompare(right.name, 'fr-FR'));
  }

  get filteredSlots() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.slots.filter((slot) => {
      const matchesRegion = !this.selectedRegionId || slot.regionId === this.selectedRegionId;
      const matchesSchool = !this.selectedSchoolId || slot.schoolId === this.selectedSchoolId;
      const matchesClass = !this.selectedClassRoomId || slot.classRoomId === this.selectedClassRoomId;
      const matchesDay = !this.selectedDay || slot.day === this.selectedDay;
      const matchesStatus = !this.selectedStatus || slot.status === this.selectedStatus;
      const searchable = this.normalizeSearch(
        [
          slot.schoolName,
          slot.region,
          slot.classRoomName,
          slot.level,
          slot.subjectName,
          slot.teacherName,
          slot.roomName,
          this.dayLabel(slot.day),
        ].join(' '),
      );

      return (
        matchesRegion &&
        matchesSchool &&
        matchesClass &&
        matchesDay &&
        matchesStatus &&
        (!search || searchable.includes(search))
      );
    });
  }

  get totals() {
    const slots = this.filteredSlots;
    const classIds = new Set(slots.map((slot) => slot.classRoomId));
    const teacherNames = new Set(slots.map((slot) => slot.teacherName));

    return {
      slots: slots.length,
      classes: classIds.size,
      teachers: teacherNames.size,
      published: slots.filter((slot) => slot.status === 'published').length,
      conflicts: slots.filter((slot) => slot.status === 'conflict').length,
      weeklyHours: slots.reduce((sum, slot) => sum + slot.weeklyHours, 0),
      publicationRate: slots.length ? Math.round((slots.filter((slot) => slot.status === 'published').length / slots.length) * 100) : 0,
    };
  }

  get daySummaries() {
    return this.days.map((day) => {
      const slots = this.filteredSlots.filter((slot) => slot.day === day.value);
      const conflicts = slots.filter((slot) => slot.status === 'conflict').length;

      return {
        ...day,
        slots: slots.length,
        hours: slots.reduce((sum, slot) => sum + slot.weeklyHours, 0),
        conflicts,
      };
    });
  }

  get subjectSummaries() {
    const summaries = new Map<string, { subject: string; hours: number; slots: number }>();

    this.filteredSlots.forEach((slot) => {
      const current = summaries.get(slot.subjectName) ?? { subject: slot.subjectName, hours: 0, slots: 0 };
      current.hours += slot.weeklyHours;
      current.slots += 1;
      summaries.set(slot.subjectName, current);
    });

    return [...summaries.values()].sort((left, right) => right.hours - left.hours).slice(0, 6);
  }

  load() {
    this.loading = true;
    this.error = '';

    forkJoin({
      metadata: this.censusApi.metadata(),
      subjects: this.academicsApi.listSubjects(),
      teachers: this.censusApi.teachers(),
      apiSlots: this.schoolLifeApi.listTimetable({ limit: 2000 }),
    })
      .pipe(catchError(() => of(null)), takeUntilDestroyed(this.destroyRef))
      .subscribe((result) => {
        if (!result) {
          this.regions = this.fallbackRegions();
          this.schools = this.fallbackSchools();
          this.subjects = this.fallbackSubjects();
          this.teachers = this.fallbackTeachers();
          this.slots = this.buildSlots();
          this.error = 'Données backend indisponibles, affichage des emplois du temps de démonstration.';
          this.loading = false;
          return;
        }
        this.regions = result.metadata.regions.length ? result.metadata.regions : this.fallbackRegions();
        this.schools = result.metadata.schools.length ? this.ensureClasses(result.metadata.schools) : this.fallbackSchools();
        this.subjects = result.subjects.length ? result.subjects : this.fallbackSubjects();
        this.teachers = result.teachers.length ? result.teachers : this.fallbackTeachers();
        // Mappe les vrais créneaux backend ; complète si vide avec la synthèse
        this.slots = result.apiSlots.length
          ? this.apiSlotsToTimetable(result.apiSlots)
          : this.buildSlots();
        this.loading = false;
      });
  }

  private apiSlotsToTimetable(apiSlots: ApiSlotRow[]): TimetableSlot[] {
    // Index pour résoudre school + region par classRoomId
    const classToSchool = new Map<string, School>();
    for (const sch of this.schools) {
      for (const c of sch.classes ?? []) {
        classToSchool.set(c.id, sch);
      }
    }
    const dayMap: Record<string, SchoolDay> = {
      MONDAY: 'monday', TUESDAY: 'tuesday', WEDNESDAY: 'wednesday',
      THURSDAY: 'thursday', FRIDAY: 'friday', SATURDAY: 'saturday',
    };
    return apiSlots.map((s) => {
      const school = classToSchool.get(s.classRoomId);
      const start = (s.startTime || '08:00').slice(0, 5);
      const end = (s.endTime || '09:00').slice(0, 5);
      const [sh, sm] = start.split(':').map(Number);
      const [eh, em] = end.split(':').map(Number);
      const weeklyHours = Math.max(1, ((eh - sh) * 60 + (em - sm)) / 60);
      return {
        id: s.id,
        day: dayMap[s.dayOfWeek] ?? 'monday',
        startTime: start,
        endTime: end,
        schoolId: school?.id ?? '',
        schoolName: school?.name ?? '—',
        regionId: school?.regionId ?? '',
        region: school?.region?.name ?? 'Région N/A',
        classRoomId: s.classRoomId,
        classRoomName: s.classRoom?.name ?? '—',
        level: s.classRoom?.level ?? '—',
        subjectId: s.subjectId ?? '',
        subjectName: s.subject?.name ?? '—',
        teacherName: s.teacher
          ? `${s.teacher.firstName} ${s.teacher.lastName}`
          : '—',
        roomName: s.room ?? '—',
        status: 'published' as const,
        weeklyHours,
      };
    });
  }

  onRegionChange() {
    this.selectedSchoolId = '';
    this.selectedClassRoomId = '';
  }

  onSchoolChange() {
    this.selectedClassRoomId = '';
  }

  resetFilters() {
    this.searchTerm = '';
    this.selectedRegionId = '';
    this.selectedSchoolId = '';
    this.selectedClassRoomId = '';
    this.selectedDay = '';
    this.selectedStatus = '';
  }

  exportRows(format: 'csv' | 'excel' | 'print') {
    if (format === 'csv') {
      downloadCsv('emplois-du-temps.csv', this.filteredSlots, this.exportColumns);
      return;
    }

    if (format === 'excel') {
      downloadExcel('emplois-du-temps.xls', this.filteredSlots, this.exportColumns);
      return;
    }

    printTable('Emplois du temps', this.filteredSlots, this.exportColumns);
  }

  dayLabel(day: SchoolDay) {
    return this.days.find((item) => item.value === day)?.label ?? day;
  }

  statusLabel(status: TimetableStatus) {
    const labels: Record<TimetableStatus, string> = {
      draft: 'Brouillon',
      published: 'Publié',
      conflict: 'Conflit',
    };

    return labels[status];
  }

  statusClass(status: TimetableStatus) {
    const classes: Record<TimetableStatus, string> = {
      draft: 'bg-warning-transparent text-warning',
      published: 'bg-success-transparent text-success',
      conflict: 'bg-danger-transparent text-danger',
    };

    return classes[status];
  }

  formatNumber(value: number) {
    return value.toLocaleString('fr-FR');
  }

  private buildSlots(): TimetableSlot[] {
    const hours = [
      ['08:00', '09:00'],
      ['09:00', '10:00'],
      ['10:15', '11:15'],
      ['11:15', '12:15'],
      ['14:00', '15:00'],
      ['15:00', '16:00'],
    ];
    const classes = this.schools.flatMap((school) =>
      (school.classes ?? []).map((classRoom) => ({
        school,
        classRoom,
      })),
    );

    return classes.slice(0, 18).flatMap(({ school, classRoom }, classIndex) =>
      this.days.slice(0, classIndex % 4 === 0 ? 6 : 5).flatMap((day, dayIndex) =>
        hours.slice(0, classIndex % 3 === 0 ? 5 : 4).map(([startTime, endTime], hourIndex) => {
          const subject = this.subjects[(classIndex + dayIndex + hourIndex) % this.subjects.length];
          const teacher = this.teachers[(classIndex + hourIndex + dayIndex) % this.teachers.length];
          const status: TimetableStatus =
            classIndex % 5 === 0 && hourIndex === 2 ? 'conflict' : classIndex % 4 === 0 ? 'draft' : 'published';

          return {
            id: `${classRoom.id}-${day.value}-${hourIndex}`,
            day: day.value,
            startTime,
            endTime,
            schoolId: school.id,
            schoolName: school.name,
            regionId: school.regionId,
            region: school.region?.name ?? this.regions.find((region) => region.id === school.regionId)?.name ?? 'Région',
            classRoomId: classRoom.id,
            classRoomName: classRoom.name,
            level: classRoom.level ?? this.inferLevel(classRoom.name),
            subjectId: subject.id,
            subjectName: subject.name,
            teacherName: teacher.fullName,
            roomName: `Salle ${1 + ((classIndex + hourIndex) % 8)}`,
            status,
            weeklyHours: 1,
          };
        }),
      ),
    );
  }

  private ensureClasses(schools: School[]): School[] {
    return schools.map((school, index) => {
      if (school.classes?.length) {
        return school;
      }

      return {
        ...school,
        classes: this.fallbackClassRooms(school.id, index),
      };
    });
  }

  private fallbackRegions(): Region[] {
    return [
      { id: 'rg-conakry', code: 'RG-CON', name: 'Conakry' },
      { id: 'rg-kindia', code: 'RG-KIN', name: 'Kindia' },
      { id: 'rg-labe', code: 'RG-LAB', name: 'Labé' },
    ];
  }

  private fallbackSchools(): School[] {
    const regions = this.regions.length ? this.regions : this.fallbackRegions();
    const names = ['École Primaire Almamya', 'Collège 2 Octobre', 'Lycée Donka', 'École Application Kindia'];

    return names.map((name, index) => {
      const region = regions[index % regions.length];

      return {
        id: `school-timetable-${index + 1}`,
        name,
        code: `ECO-${String(index + 1).padStart(3, '0')}`,
        regionId: region.id,
        region,
        classes: this.fallbackClassRooms(`school-timetable-${index + 1}`, index),
      };
    });
  }

  private fallbackClassRooms(schoolId: string, schoolIndex: number): ClassRoom[] {
    const levels = ['CP1', 'CE2', 'CM2', '7ème', '9ème', 'Terminale'];

    return levels.slice(0, 4).map((level, index) => ({
      id: `${schoolId}-class-${index + 1}`,
      name: `${level} ${String.fromCharCode(65 + ((schoolIndex + index) % 3))}`,
      level,
      schoolId,
      studentsCount: 28 + index * 6,
      teachersCount: 1 + (index % 2),
    }));
  }

  private fallbackSubjects(): Subject[] {
    return [
      { id: 'subject-math', code: 'MATH', name: 'Mathématiques', coefficient: 4 },
      { id: 'subject-fr', code: 'FR', name: 'Français', coefficient: 4 },
      { id: 'subject-hg', code: 'HG', name: 'Histoire-Géographie', coefficient: 2 },
      { id: 'subject-svte', code: 'SVTE', name: 'Sciences', coefficient: 3 },
      { id: 'subject-ang', code: 'ANG', name: 'Anglais', coefficient: 2 },
      { id: 'subject-eps', code: 'EPS', name: 'Éducation physique', coefficient: 1 },
    ];
  }

  private fallbackTeachers(): CensusPerson[] {
    return ['Aminata Diallo', 'Moussa Camara', 'Fatoumata Barry', 'Ibrahima Condé', 'Mariama Bah'].map(
      (fullName, index) =>
        ({
          id: `teacher-timetable-${index + 1}`,
          type: 'TEACHER',
          uniqueCode: `ENS-${String(index + 1).padStart(4, '0')}`,
          firstName: fullName.split(' ')[0],
          lastName: fullName.split(' ').slice(1).join(' '),
          fullName,
          gender: index % 2 ? 'MALE' : 'FEMALE',
          school: this.schools[index % Math.max(this.schools.length, 1)] ?? this.fallbackSchools()[0],
          createdAt: '2026-05-02T00:00:00.000Z',
        }) as CensusPerson,
    );
  }

  private inferLevel(className: string) {
    return className.split(' ')[0] || 'Niveau';
  }

  private normalizeSearch(value?: string | null) {
    return (value ?? '')
      .toLocaleLowerCase('fr-FR')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '');
  }
}
