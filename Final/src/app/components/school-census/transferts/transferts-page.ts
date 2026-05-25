import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { catchError, forkJoin, of } from 'rxjs';

import {
  NATIONAL_SCOPE_ROLES,
  REGIONAL_SCOPE_ROLES,
  AuthService,
} from '../../../shared/services/auth.service';
import {
  StaffingApiService,
  TeacherStaffingSnapshot,
  TeacherTransferRecommendation,
} from '../shared/staffing-api.service';
import { AcademicsApiService } from '../shared/academics-api.service';
import { CensusApiService } from '../shared/census-api.service';
import { School } from '../shared/school-census.models';
import { SchoolYear } from '../shared/school-census.models';
import { StaffingKpiCard } from './staffing-kpi-card';
import { StaffingMap } from './staffing-map';
import { StaffingTable } from './staffing-table';
import {
  RecommendationsTable,
  ReviewActionEvent,
} from './recommendations-table';

/**
 * Module 2D UI — Dashboard transferts enseignants.
 *
 * Orchestrateur :
 *  - charge en parallèle l'année scolaire active, la métadata census
 *    (écoles), les snapshots staffing, les recommandations,
 *  - expose 4 KPIs (CRITICAL, OVER_STAFFED, PENDING, EXECUTED),
 *  - affiche carte + table top 20 (côte à côte) + table recommandations,
 *  - propose deux boutons admin (compute / generate) limités à NATIONAL/MINISTRY.
 *
 * State 100% signals — pas de NgRx. Les erreurs de chargement n'arrêtent
 * pas le reste : chaque appel a un catchError → fallback vide.
 */
@Component({
  selector: 'app-transferts-page',
  imports: [
    CommonModule,
    StaffingKpiCard,
    StaffingMap,
    StaffingTable,
    RecommendationsTable,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './transferts-page.html',
  styleUrl: './transferts-page.scss',
})
export class TransfertsPage implements OnInit {
  private staffingApi = inject(StaffingApiService);
  private academicsApi = inject(AcademicsApiService);
  private censusApi = inject(CensusApiService);
  private auth = inject(AuthService);
  private destroyRef = inject(DestroyRef);

  // ---- état UI ----
  readonly loading = signal<boolean>(true);
  readonly busy = signal<boolean>(false);
  readonly error = signal<string | null>(null);
  readonly toast = signal<{ kind: 'success' | 'danger'; message: string } | null>(
    null,
  );

  // ---- données ----
  readonly schoolYearId = signal<string | null>(null);
  readonly schoolYears = signal<SchoolYear[]>([]);
  readonly schools = signal<School[]>([]);
  readonly snapshots = signal<TeacherStaffingSnapshot[]>([]);
  readonly recommendations = signal<TeacherTransferRecommendation[]>([]);
  readonly selectedSchoolId = signal<string | null>(null);

  // ---- droits ----
  readonly canTriggerJobs = computed<boolean>(() =>
    this.auth.hasAnyRole(NATIONAL_SCOPE_ROLES),
  );

  readonly canReview = computed<boolean>(() =>
    this.auth.hasAnyRole([...NATIONAL_SCOPE_ROLES, ...REGIONAL_SCOPE_ROLES]),
  );

  // ---- KPIs ----
  readonly criticalCount = computed<number>(
    () => this.snapshots().filter((s) => s.severity === 'CRITICAL').length,
  );
  readonly overStaffedCount = computed<number>(
    () => this.snapshots().filter((s) => s.severity === 'OVER_STAFFED').length,
  );
  readonly underStaffedCount = computed<number>(
    () => this.snapshots().filter((s) => s.severity === 'UNDER_STAFFED').length,
  );
  readonly pendingRecoCount = computed<number>(
    () => this.recommendations().filter((r) => r.status === 'PENDING').length,
  );
  readonly executedRecoCount = computed<number>(
    () => this.recommendations().filter((r) => r.status === 'EXECUTED').length,
  );

  ngOnInit(): void {
    this.loadAll();
  }

  refresh(): void {
    this.loadAll();
  }

  onSelectSchool(schoolId: string): void {
    this.selectedSchoolId.set(schoolId);
  }

  onReview(event: ReviewActionEvent): void {
    if (this.busy()) return;
    this.busy.set(true);
    this.staffingApi
      .reviewRecommendation(event.recommendationId, {
        status: event.targetStatus,
        reviewNote: event.reviewNote,
      })
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        catchError((err) => {
          this.toast.set({
            kind: 'danger',
            message: this.extractError(err, 'Mise à jour impossible'),
          });
          this.busy.set(false);
          return of(null);
        }),
      )
      .subscribe((updated) => {
        if (updated) {
          const list = this.recommendations().map((r) =>
            r.id === updated.id ? updated : r,
          );
          this.recommendations.set(list);
          this.toast.set({
            kind: 'success',
            message: 'Recommandation mise à jour avec succès.',
          });
        }
        this.busy.set(false);
      });
  }

  computeStaffing(): void {
    const sy = this.schoolYearId();
    if (!sy || this.busy()) return;
    this.busy.set(true);
    this.staffingApi
      .computeStaffing(sy)
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        catchError((err) => {
          this.toast.set({
            kind: 'danger',
            message: this.extractError(err, 'Échec du recalcul staffing'),
          });
          this.busy.set(false);
          return of(null);
        }),
      )
      .subscribe((resp) => {
        if (resp) {
          const n = resp.snapshots ?? 0;
          this.toast.set({
            kind: 'success',
            message: `Staffing recalculé (${n} snapshots).`,
          });
          this.reloadSnapshots();
        }
        this.busy.set(false);
      });
  }

  generateRecommendations(): void {
    const sy = this.schoolYearId();
    if (!sy || this.busy()) return;
    this.busy.set(true);
    this.staffingApi
      .generateRecommendations(sy)
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        catchError((err) => {
          this.toast.set({
            kind: 'danger',
            message: this.extractError(
              err,
              'Échec de génération des recommandations',
            ),
          });
          this.busy.set(false);
          return of(null);
        }),
      )
      .subscribe((resp) => {
        if (resp) {
          const n = resp.recommendations ?? 0;
          this.toast.set({
            kind: 'success',
            message: `${n} recommandations générées.`,
          });
          this.reloadRecommendations();
        }
        this.busy.set(false);
      });
  }

  dismissToast(): void {
    this.toast.set(null);
  }

  private loadAll(): void {
    this.loading.set(true);
    this.error.set(null);

    forkJoin({
      meta: this.censusApi.metadata().pipe(
        catchError(() =>
          of({
            regions: [],
            prefectures: [],
            subPrefectures: [],
            schools: [] as School[],
            roles: [],
          }),
        ),
      ),
      schoolYears: this.academicsApi
        .listSchoolYears()
        .pipe(catchError(() => of([] as SchoolYear[]))),
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ meta, schoolYears }) => {
          this.schools.set(meta.schools ?? []);
          this.schoolYears.set(schoolYears);
          // Année active = isActive=true, sinon premier élément.
          const active = schoolYears.find((y) => y.isActive) ?? schoolYears[0];
          this.schoolYearId.set(active?.id ?? null);
          this.reloadStaffingAndRecommendations();
        },
        error: () => {
          this.error.set('Métadonnées indisponibles — backend à vérifier.');
          this.loading.set(false);
        },
      });
  }

  private reloadStaffingAndRecommendations(): void {
    const sy = this.schoolYearId();
    forkJoin({
      snapshots: this.staffingApi
        .listStaffing({ schoolYearId: sy, limit: 1000 })
        .pipe(catchError(() => of([] as TeacherStaffingSnapshot[]))),
      recommendations: this.staffingApi
        .listRecommendations({ schoolYearId: sy, limit: 500 })
        .pipe(catchError(() => of([] as TeacherTransferRecommendation[]))),
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(({ snapshots, recommendations }) => {
        this.snapshots.set(snapshots);
        this.recommendations.set(recommendations);
        this.loading.set(false);
      });
  }

  private reloadSnapshots(): void {
    const sy = this.schoolYearId();
    this.staffingApi
      .listStaffing({ schoolYearId: sy, limit: 1000 })
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        catchError(() => of([] as TeacherStaffingSnapshot[])),
      )
      .subscribe((snapshots) => this.snapshots.set(snapshots));
  }

  private reloadRecommendations(): void {
    const sy = this.schoolYearId();
    this.staffingApi
      .listRecommendations({ schoolYearId: sy, limit: 500 })
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        catchError(() => of([] as TeacherTransferRecommendation[])),
      )
      .subscribe((list) => this.recommendations.set(list));
  }

  private extractError(err: unknown, fallback: string): string {
    if (err && typeof err === 'object' && 'error' in err) {
      const e = (err as { error?: { detail?: string } }).error;
      if (e?.detail) return e.detail;
    }
    return fallback;
  }
}
