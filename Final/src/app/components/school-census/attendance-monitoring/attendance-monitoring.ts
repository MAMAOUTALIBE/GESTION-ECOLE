import { CommonModule } from '@angular/common';
import { Component, DestroyRef, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { forkJoin, of } from 'rxjs';
import { catchError } from 'rxjs/operators';
import {
  AnalyticsApiService,
  AttendancePoint,
  TopSchoolRow,
} from '../shared/analytics-api.service';
import { CensusApiService } from '../shared/census-api.service';
import { AttendanceRecord, AttendanceStatus, PersonType } from '../shared/school-census.models';

@Component({
  selector: 'app-attendance-monitoring',
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './attendance-monitoring.html',
  styleUrl: './attendance-monitoring.scss',
})
export class AttendanceMonitoring {
  private censusApi = inject(CensusApiService);
  private analyticsApi = inject(AnalyticsApiService);
  private destroyRef = inject(DestroyRef);

  records: AttendanceRecord[] = [];
  loading = false;
  error = '';
  searchTerm = '';
  selectedStatus = '';
  selectedPersonType = '';
  selectedSchool = '';

  // Phase 8 — tendance 7 jours + classement par taux de présence
  trendsLoading = false;
  trendPoints: AttendancePoint[] = [];
  topSchools: TopSchoolRow[] = [];     // 5 meilleurs taux
  bottomSchools: TopSchoolRow[] = [];  // 5 pires (alerte absentéisme)

  statuses: Array<{ value: AttendanceStatus; label: string }> = [
    { value: 'PRESENT', label: 'Présents' },
    { value: 'LATE', label: 'Retards' },
    { value: 'ABSENT', label: 'Absences' },
  ];

  personTypes: Array<{ value: PersonType; label: string }> = [
    { value: 'STUDENT', label: 'Élèves' },
    { value: 'TEACHER', label: 'Enseignants' },
  ];

  ngOnInit() {
    this.load();
    this.loadTrends();
  }

  /** Tendance 7 jours + top/flop écoles (taux de présence). */
  private loadTrends() {
    this.trendsLoading = true;
    forkJoin({
      trends: this.analyticsApi.attendanceTrends(7),
      // 100 = max autorisé par l'endpoint Phase 8 ; on prend top + bottom
      top: this.analyticsApi.topSchools('attendance', 100),
    })
      .pipe(
        catchError(() => of(null)),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((result) => {
        if (result) {
          this.trendPoints = result.trends.points;
          // Le backend retourne déjà les meilleurs ; on tire le top 5 et le
          // flop 5 (rangs ascendants) pour le panneau d'alerte.
          const sorted = [...result.top.rows].sort(
            (a, b) => (a.presenceRateLast7Days ?? 0) - (b.presenceRateLast7Days ?? 0),
          );
          this.bottomSchools = sorted.slice(0, 5);
          this.topSchools = sorted.slice(-5).reverse();
        }
        this.trendsLoading = false;
      });
  }

  /** Taux de présence national agrégé sur les 7 derniers jours. */
  get nationalRate7d(): number {
    const totalPresent = this.trendPoints.reduce((sum, p) => sum + p.present, 0);
    const totalScans = this.trendPoints.reduce((sum, p) => sum + p.total, 0);
    return totalScans ? Math.round((totalPresent / totalScans) * 1000) / 10 : 0;
  }

  /** Tendance flèche : compare la moyenne des 3 premiers jours avec celle des 3 derniers. */
  get trendDirection(): 'up' | 'down' | 'flat' {
    if (this.trendPoints.length < 4) return 'flat';
    const first = this.trendPoints.slice(0, 3);
    const last = this.trendPoints.slice(-3);
    const avg = (pts: AttendancePoint[]) =>
      pts.reduce((s, p) => s + p.presenceRate, 0) / pts.length;
    const diff = avg(last) - avg(first);
    if (diff > 1.5) return 'up';
    if (diff < -1.5) return 'down';
    return 'flat';
  }

  get schools() {
    return Array.from(
      new Set(this.records.map((record) => record.person?.school?.name).filter(Boolean) as string[]),
    ).sort((left, right) => left.localeCompare(right, 'fr-FR'));
  }

  get filteredRecords() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.records.filter((record) => {
      const matchesStatus = !this.selectedStatus || record.status === this.selectedStatus;
      const matchesPersonType = !this.selectedPersonType || record.personType === this.selectedPersonType;
      const matchesSchool = !this.selectedSchool || record.person?.school?.name === this.selectedSchool;
      const searchable = this.normalizeSearch(
        [
          record.person?.fullName,
          record.person?.uniqueCode,
          record.person?.school?.name,
          record.person?.classRoom?.name,
        ].join(' '),
      );

      return matchesStatus && matchesPersonType && matchesSchool && (!search || searchable.includes(search));
    });
  }

  get totals() {
    const rows = this.filteredRecords;

    return {
      records: rows.length,
      present: rows.filter((record) => record.status === 'PRESENT').length,
      late: rows.filter((record) => record.status === 'LATE').length,
      absent: rows.filter((record) => record.status === 'ABSENT').length,
      students: rows.filter((record) => record.personType === 'STUDENT').length,
      teachers: rows.filter((record) => record.personType === 'TEACHER').length,
    };
  }

  load() {
    this.loading = true;
    this.error = '';

    this.censusApi.todayAttendance().subscribe({
      next: (records) => {
        this.records = records;
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger le suivi des présences.';
        this.loading = false;
      },
    });
  }

  resetFilters() {
    this.searchTerm = '';
    this.selectedStatus = '';
    this.selectedPersonType = '';
    this.selectedSchool = '';
  }

  statusLabel(status: AttendanceStatus) {
    const labels: Record<AttendanceStatus, string> = {
      PRESENT: 'Présent',
      LATE: 'Retard',
      ABSENT: 'Absent',
    };

    return labels[status];
  }

  statusClass(status: AttendanceStatus) {
    const classes: Record<AttendanceStatus, string> = {
      PRESENT: 'bg-success-transparent text-success',
      LATE: 'bg-warning-transparent text-warning',
      ABSENT: 'bg-danger-transparent text-danger',
    };

    return classes[status];
  }

  personTypeLabel(personType: PersonType) {
    return personType === 'STUDENT' ? 'Élève' : 'Enseignant';
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
