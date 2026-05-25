import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
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
  AuthService,
  NATIONAL_SCOPE_ROLES,
} from '../../../shared/services/auth.service';
import { AcademicsApiService } from '../shared/academics-api.service';
import { CensusApiService } from '../shared/census-api.service';
import {
  InvestmentApiService,
  InvestmentScoreRead,
  PriorityCategory,
} from '../shared/investment-api.service';
import { Region, SchoolYear } from '../shared/school-census.models';
import { InvestmentDetailPanel } from './investment-detail-panel';
import { InvestmentKpiCard } from './investment-kpi-card';
import { InvestmentTable } from './investment-table';

/**
 * Module 3C UI — Page principale du dashboard Priorités investissements.
 *
 * Orchestrateur :
 *  - charge en parallèle l'année active, les régions (metadata census)
 *    et le top 100 des priorités,
 *  - expose 4 KPIs (count par catégorie),
 *  - filtres : catégorie (chips) + région (select),
 *  - table top + panneau détail droite,
 *  - bouton "Recalculer" (NATIONAL/MINISTRY uniquement).
 *
 * State 100% signals — pas de NgRx. Les erreurs de chargement n'arrêtent
 * pas le reste : chaque appel a un catchError → fallback vide.
 */
@Component({
  selector: 'app-investissements-page',
  imports: [
    CommonModule,
    FormsModule,
    InvestmentKpiCard,
    InvestmentTable,
    InvestmentDetailPanel,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './investissements-page.html',
  styleUrl: './investissements-page.scss',
})
export class InvestissementsPage implements OnInit {
  private investmentApi = inject(InvestmentApiService);
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
  readonly schoolYears = signal<SchoolYear[]>([]);
  readonly schoolYearId = signal<string | null>(null);
  readonly regions = signal<Region[]>([]);
  readonly scores = signal<InvestmentScoreRead[]>([]);

  // ---- filtres ----
  readonly selectedCategory = signal<PriorityCategory | null>(null);
  readonly selectedRegionId = signal<string | null>(null);

  // ---- sélection détail ----
  readonly selectedSchoolId = signal<string | null>(null);

  // ---- droits ----
  readonly canCompute = computed<boolean>(() =>
    this.auth.hasAnyRole(NATIONAL_SCOPE_ROLES),
  );

  // ---- KPIs (count par catégorie) ----
  readonly tresHauteCount = computed<number>(
    () =>
      this.scores().filter((s) => s.priorityCategory === 'TRES_HAUTE').length,
  );
  readonly hauteCount = computed<number>(
    () => this.scores().filter((s) => s.priorityCategory === 'HAUTE').length,
  );
  readonly moyenneCount = computed<number>(
    () => this.scores().filter((s) => s.priorityCategory === 'MOYENNE').length,
  );
  readonly basseCount = computed<number>(
    () => this.scores().filter((s) => s.priorityCategory === 'BASSE').length,
  );

  // ---- table filtrée ----
  readonly filteredScores = computed<InvestmentScoreRead[]>(() => {
    const cat = this.selectedCategory();
    const reg = this.selectedRegionId();
    return this.scores().filter((s) => {
      if (cat && s.priorityCategory !== cat) return false;
      if (reg && s.regionId !== reg) return false;
      return true;
    });
  });

  // ---- score actif (pour le panneau) ----
  readonly selectedScore = computed<InvestmentScoreRead | null>(() => {
    const id = this.selectedSchoolId();
    if (!id) return null;
    return this.scores().find((s) => s.schoolId === id) ?? null;
  });

  ngOnInit(): void {
    this.loadAll();
  }

  // ---- handlers UI ----
  onYearChange(value: string): void {
    this.schoolYearId.set(value || null);
    this.reloadScores();
  }

  /** Toggle catégorie : un second click sur la même catégorie la désélectionne. */
  onCategoryToggle(category: PriorityCategory): void {
    this.selectedCategory.update((curr) =>
      curr === category ? null : category,
    );
  }

  onRegionChange(regionId: string): void {
    this.selectedRegionId.set(regionId || null);
  }

  onSelectSchool(schoolId: string): void {
    this.selectedSchoolId.set(schoolId);
  }

  onCloseDetail(): void {
    this.selectedSchoolId.set(null);
  }

  resetFilters(): void {
    this.selectedCategory.set(null);
    this.selectedRegionId.set(null);
  }

  dismissToast(): void {
    this.toast.set(null);
  }

  /** Lance un recalcul global (NATIONAL/MINISTRY). */
  computeScores(): void {
    const sy = this.schoolYearId();
    if (!sy || this.busy()) return;
    this.busy.set(true);
    this.investmentApi
      .computeScores(sy)
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        catchError((err) => {
          this.toast.set({
            kind: 'danger',
            message: this.extractError(err, 'Échec du recalcul.'),
          });
          this.busy.set(false);
          return of(null);
        }),
      )
      .subscribe((resp) => {
        if (resp) {
          this.toast.set({
            kind: 'success',
            message: `Recalcul terminé (${resp.scoresComputed} écoles).`,
          });
          this.reloadScores();
        }
        this.busy.set(false);
      });
  }

  // ---- chargement initial ----
  private loadAll(): void {
    this.loading.set(true);
    this.error.set(null);
    forkJoin({
      meta: this.censusApi.metadata().pipe(
        catchError(() =>
          of({
            regions: [] as Region[],
            prefectures: [],
            subPrefectures: [],
            schools: [],
            roles: [],
          }),
        ),
      ),
      schoolYears: this.academicsApi
        .listSchoolYears()
        .pipe(catchError(() => of([] as SchoolYear[]))),
      scores: this.investmentApi
        .topPriorities(100)
        .pipe(catchError(() => of([] as InvestmentScoreRead[]))),
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ meta, schoolYears, scores }) => {
          this.regions.set(meta.regions ?? []);
          this.schoolYears.set(schoolYears);
          const active = schoolYears.find((y) => y.isActive) ?? schoolYears[0];
          this.schoolYearId.set(active?.id ?? null);
          this.scores.set(scores);
          this.loading.set(false);
        },
        error: () => {
          this.error.set('Données indisponibles — backend à vérifier.');
          this.loading.set(false);
        },
      });
  }

  private reloadScores(): void {
    const sy = this.schoolYearId();
    this.investmentApi
      .topPriorities(100, sy)
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        catchError(() => of([] as InvestmentScoreRead[])),
      )
      .subscribe((scores) => {
        this.scores.set(scores);
        // Si l'école sélectionnée n'est plus dans la nouvelle liste, on
        // ferme le panneau pour éviter un score périmé.
        const sel = this.selectedSchoolId();
        if (sel && !scores.some((s) => s.schoolId === sel)) {
          this.selectedSchoolId.set(null);
        }
      });
  }

  private extractError(err: unknown, fallback: string): string {
    if (err && typeof err === 'object' && 'error' in err) {
      const e = (err as { error?: { detail?: string } }).error;
      if (e?.detail) return e.detail;
    }
    return fallback;
  }
}
