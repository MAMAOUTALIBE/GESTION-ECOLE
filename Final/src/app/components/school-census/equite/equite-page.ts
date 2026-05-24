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
import { FormsModule } from '@angular/forms';
import { catchError, forkJoin, of } from 'rxjs';

import { CensusApiService } from '../shared/census-api.service';
import {
  AggregateResponse,
  CriticalSchool,
  EnrollmentApiService,
  GpiResult,
  GpiSeverity,
  UrbanRuralGap,
  ZoneAggregate,
} from '../shared/enrollment-api.service';
import { CensusMetadata, Region } from '../shared/school-census.models';
import { EquiteCriticalSchoolsTable } from './equite-critical-schools-table';
import { EquiteKpiCard } from './equite-kpi-card';
import { EquiteRegionChart } from './equite-region-chart';
import { EquiteRegionMap } from './equite-region-map';
import { EquiteZoneDonut } from './equite-zone-donut';

/**
 * Module 1D — Dashboard Équité.
 *
 * Centralise les KPI de parité (GPI national, écart urbain/rural, top écoles
 * critiques) et offre une vue territoriale (carte régionale + bar chart).
 *
 * Architecture :
 * - Signals locaux pour le state (loading, error, données).
 * - forkJoin pour grouper les 5 appels initiaux (économie de ronds-trips).
 * - Aucune mutation backend depuis cet écran (read-only).
 */
@Component({
  selector: 'app-equite-page',
  imports: [
    CommonModule,
    FormsModule,
    EquiteKpiCard,
    EquiteCriticalSchoolsTable,
    EquiteRegionChart,
    EquiteRegionMap,
    EquiteZoneDonut,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './equite-page.html',
  styleUrl: './equite-page.scss',
})
export class EquitePage implements OnInit {
  private enrollmentApi = inject(EnrollmentApiService);
  private censusApi = inject(CensusApiService);
  private destroyRef = inject(DestroyRef);

  // --- state (signals) ---
  readonly loading = signal(true);
  readonly error = signal<string | null>(null);
  readonly metadata = signal<CensusMetadata | null>(null);
  readonly nationalGpi = signal<GpiResult | null>(null);
  readonly regionalGpi = signal<GpiResult[]>([]);
  readonly criticalSchools = signal<CriticalSchool[]>([]);
  readonly aggregate = signal<AggregateResponse | null>(null);
  readonly urbanRuralGap = signal<UrbanRuralGap | null>(null);
  readonly selectedSchoolYearId = signal<string | null>(null);

  // --- derived ---
  readonly nationalGpiValue = computed<number | null>(() =>
    EnrollmentApiService.toNumber(this.nationalGpi()?.gpi ?? null),
  );

  readonly nationalGpiLabel = computed<string>(() => {
    const v = this.nationalGpiValue();
    return v === null ? '—' : v.toFixed(4);
  });

  readonly nationalGpiBadge = computed<string>(() =>
    this.severityLabel(this.nationalGpi()?.severity ?? null),
  );

  readonly urbanRuralDelta = computed<string>(() => {
    const v = EnrollmentApiService.toNumber(this.urbanRuralGap()?.deltaGpi ?? null);
    return v === null ? '—' : v.toFixed(4);
  });

  readonly urbanRuralSeverity = computed<GpiSeverity | null>(() => {
    const v = EnrollmentApiService.toNumber(this.urbanRuralGap()?.deltaGpi ?? null);
    if (v === null) return null;
    if (v >= 0.1) return 'CRITICAL_GIRLS';
    if (v >= 0.05) return 'WARNING_GIRLS';
    return 'NORMAL';
  });

  readonly zoneRows = computed<ZoneAggregate[]>(() => this.aggregate()?.byZoneType ?? []);

  readonly totalGirls = computed<number>(() => this.nationalGpi()?.girlsCount ?? 0);
  readonly totalBoys = computed<number>(() => this.nationalGpi()?.boysCount ?? 0);

  readonly totalGirlsLabel = computed<string>(() =>
    this.totalGirls().toLocaleString('fr-FR'),
  );
  readonly totalBoysLabel = computed<string>(() =>
    this.totalBoys().toLocaleString('fr-FR'),
  );

  ngOnInit(): void {
    this.loadMetadataThenData();
  }

  refresh(): void {
    this.loadMetadataThenData();
  }

  onSchoolYearChange(schoolYearId: string): void {
    this.selectedSchoolYearId.set(schoolYearId || null);
    this.loadData();
  }

  private loadMetadataThenData(): void {
    this.loading.set(true);
    this.error.set(null);
    this.censusApi
      .metadata()
      .pipe(
        catchError(() => of<CensusMetadata | null>(null)),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((meta) => {
        this.metadata.set(meta);
        // Sélection par défaut : l'année scolaire la plus récente si dispo via metadata.
        const sy = this.pickDefaultSchoolYear(meta);
        this.selectedSchoolYearId.set(sy);
        this.loadData();
      });
  }

  private loadData(): void {
    const schoolYearId = this.selectedSchoolYearId();
    this.loading.set(true);
    this.error.set(null);

    forkJoin({
      national: this.enrollmentApi
        .getNationalGpi(schoolYearId ?? undefined)
        .pipe(catchError(() => of<GpiResult | null>(null))),
      critical: schoolYearId
        ? this.enrollmentApi
            .getCriticalSchools(schoolYearId, 10)
            .pipe(catchError(() => of<CriticalSchool[]>([])))
        : of<CriticalSchool[]>([]),
      aggregate: schoolYearId
        ? this.enrollmentApi
            .getAggregateByZone(schoolYearId)
            .pipe(catchError(() => of<AggregateResponse | null>(null)))
        : of<AggregateResponse | null>(null),
      gap: schoolYearId
        ? this.enrollmentApi
            .getUrbanRuralGap(schoolYearId)
            .pipe(catchError(() => of<UrbanRuralGap | null>(null)))
        : of<UrbanRuralGap | null>(null),
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ national, critical, aggregate, gap }) => {
          this.nationalGpi.set(national);
          this.criticalSchools.set(this.enrichCriticalSchools(critical));
          this.aggregate.set(aggregate);
          this.urbanRuralGap.set(gap);
          this.loadRegionalGpi(schoolYearId);
        },
        error: () => {
          this.error.set(
            "Données d'équité indisponibles — backend ou cache à vérifier.",
          );
          this.loading.set(false);
        },
      });
  }

  /**
   * Charge un GPI par région en parallèle. Si le backend ne renvoie rien
   * pour une région, on l'ignore silencieusement (NORMAL par défaut).
   */
  private loadRegionalGpi(schoolYearId: string | null): void {
    const regions = this.metadata()?.regions ?? [];
    if (!regions.length) {
      this.regionalGpi.set([]);
      this.loading.set(false);
      return;
    }
    const calls = regions.map((r) =>
      this.enrollmentApi
        .getRegionalGpi(r.id, schoolYearId ?? undefined)
        .pipe(
          catchError(() =>
            of<GpiResult | null>({
              scope: 'REGIONAL',
              entityId: r.id,
              schoolYearId: schoolYearId ?? '',
              girlsCount: 0,
              boysCount: 0,
              gpi: null,
              severity: 'NORMAL',
              computedAt: new Date().toISOString(),
              entityName: r.name,
            }),
          ),
        ),
    );

    forkJoin(calls)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (results) => {
          const rows: GpiResult[] = results
            .filter((r): r is GpiResult => r !== null)
            .map((r) => ({
              ...r,
              entityName:
                r.entityName ??
                regions.find((reg) => reg.id === r.entityId)?.name ??
                r.entityId ??
                null,
            }));
          this.regionalGpi.set(rows);
          this.loading.set(false);
        },
        error: () => {
          this.regionalGpi.set([]);
          this.loading.set(false);
        },
      });
  }

  private enrichCriticalSchools(rows: CriticalSchool[]): CriticalSchool[] {
    const schools = this.metadata()?.schools ?? [];
    return rows.map((row) => {
      if (row.entityName) return row;
      const found = schools.find((s) => s.id === row.entityId);
      return found ? { ...row, entityName: found.name } : row;
    });
  }

  private pickDefaultSchoolYear(meta: CensusMetadata | null): string | null {
    if (!meta) return null;
    // metadata expose les régions/écoles mais pas (encore) les années scolaires.
    // On laisse le backend choisir la "plus récente" — paramètre optionnel.
    return null;
  }

  private severityLabel(sev: GpiSeverity | null): string {
    switch (sev) {
      case 'CRITICAL_GIRLS':
        return 'Critique filles';
      case 'WARNING_GIRLS':
        return 'Alerte filles';
      case 'WARNING_BOYS':
        return 'Alerte garçons';
      case 'NORMAL':
        return 'Parité';
      default:
        return '—';
    }
  }

  readonly regions = computed<Region[]>(() => this.metadata()?.regions ?? []);
}
