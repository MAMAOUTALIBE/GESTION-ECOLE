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
  ImpactReport,
  Operation,
  ScenarioRead,
  SimulatorApiService,
} from '../shared/simulator-api.service';
import { School, SchoolYear } from '../shared/school-census.models';
import { ImpactReportComponent } from './impact-report';
import {
  OperationsPanel,
  SaveScenarioPayload,
  SimulatorMode,
} from './operations-panel';
import { ScenariosTable } from './scenarios-table';
import { SimulateurMap } from './simulateur-map';

/**
 * Module 3B UI — Page principale du simulateur what-if.
 *
 * Orchestre :
 *  - chargement année active + écoles via census/metadata + academics,
 *  - liste des scénarios existants,
 *  - état des opérations en cours (signal `operations`),
 *  - création + calcul + archive via SimulatorApiService.
 *
 * RBAC :
 *  - Tous les utilisateurs autorisés par la route voient la page.
 *  - Les écritures (createScenario / compute / archive) sont restreintes
 *    aux rôles NATIONAL_ADMIN / MINISTRY_ADMIN / REGIONAL_ADMIN (filtre
 *    backend dans le router + computed `canEdit` côté front pour griser
 *    les boutons).
 */
@Component({
  selector: 'app-simulateur-page',
  imports: [
    CommonModule,
    FormsModule,
    SimulateurMap,
    OperationsPanel,
    ImpactReportComponent,
    ScenariosTable,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './simulateur-page.html',
  styleUrl: './simulateur-page.scss',
})
export class SimulateurPage implements OnInit {
  private simulator = inject(SimulatorApiService);
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
  readonly schools = signal<School[]>([]);
  readonly scenarios = signal<ScenarioRead[]>([]);

  // ---- état scénario en cours ----
  readonly operations = signal<Operation[]>([]);
  readonly mode = signal<SimulatorMode>('view');
  readonly currentScenarioId = signal<string | null>(null);
  readonly impact = signal<ImpactReport | null>(null);

  readonly canEdit = computed<boolean>(() =>
    this.auth.hasAnyRole([...NATIONAL_SCOPE_ROLES, 'REGIONAL_ADMIN']),
  );

  ngOnInit(): void {
    this.loadAll();
  }

  // ---- handlers UI ----
  setMode(mode: SimulatorMode): void {
    this.mode.set(mode);
  }

  onOpAdded(op: Operation): void {
    this.operations.update((list) => [...list, op]);
    // Une nouvelle op rend le scénario précédent obsolète : on remet à zéro
    // l'id "currentScenarioId" et l'impact, l'utilisateur devra re-saver.
    this.currentScenarioId.set(null);
    this.impact.set(null);
  }

  onOpRemoved(index: number): void {
    this.operations.update((list) => {
      const next = list.slice();
      next.splice(index, 1);
      return next;
    });
    this.currentScenarioId.set(null);
    this.impact.set(null);
  }

  startNewScenario(): void {
    this.operations.set([]);
    this.currentScenarioId.set(null);
    this.impact.set(null);
    this.mode.set('view');
  }

  onYearChange(value: string): void {
    this.schoolYearId.set(value || null);
  }

  /** Sauvegarde le scénario (création initiale uniquement — pas d'update). */
  onSave(payload: SaveScenarioPayload): void {
    const sy = this.schoolYearId();
    if (!sy || this.busy() || this.operations().length === 0) return;
    this.busy.set(true);
    this.simulator
      .createScenario({
        name: payload.name,
        description: payload.description,
        baselineSchoolYearId: sy,
        operations: this.operations(),
      })
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        catchError((err) => {
          this.toast.set({
            kind: 'danger',
            message: this.extractError(err, 'Sauvegarde impossible.'),
          });
          this.busy.set(false);
          return of(null);
        }),
      )
      .subscribe((created) => {
        if (created) {
          this.currentScenarioId.set(created.id);
          this.scenarios.update((list) => [created, ...list]);
          this.toast.set({
            kind: 'success',
            message: 'Scénario enregistré.',
          });
        }
        this.busy.set(false);
      });
  }

  /** Lance le calcul d'impact sur le scénario courant. */
  onCompute(): void {
    const id = this.currentScenarioId();
    if (!id || this.busy()) return;
    this.computeScenarioById(id);
  }

  /** Calcul d'impact déclenché depuis la table (par id). */
  computeScenarioFromTable(id: string): void {
    if (this.busy()) return;
    // Charge les ops du scénario depuis la liste (pour les remontrer
    // dans le panneau / sur la carte) puis lance le compute.
    const target = this.scenarios().find((s) => s.id === id);
    if (target) {
      this.loadScenarioIntoEditor(target);
    }
    this.computeScenarioById(id);
  }

  /** Affiche un scénario COMPUTED dans l'éditeur. */
  onView(id: string): void {
    const target = this.scenarios().find((s) => s.id === id);
    if (!target) return;
    this.loadScenarioIntoEditor(target);
    if (target.impactJson) {
      this.impact.set(target.impactJson);
    }
  }

  /** Archive un scénario depuis la table. */
  onArchive(id: string): void {
    if (this.busy()) return;
    this.busy.set(true);
    this.simulator
      .archiveScenario(id)
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        catchError((err) => {
          this.toast.set({
            kind: 'danger',
            message: this.extractError(err, 'Archivage impossible.'),
          });
          this.busy.set(false);
          return of(null);
        }),
      )
      .subscribe((updated) => {
        if (updated) {
          // Le backend masque ARCHIVED par défaut → on retire la ligne.
          this.scenarios.update((list) =>
            list.filter((s) => s.id !== updated.id),
          );
          if (this.currentScenarioId() === updated.id) {
            this.startNewScenario();
          }
          this.toast.set({
            kind: 'success',
            message: 'Scénario archivé.',
          });
        }
        this.busy.set(false);
      });
  }

  dismissToast(): void {
    this.toast.set(null);
  }

  // ---- chargement initial ----
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
      scenarios: this.simulator
        .listScenarios()
        .pipe(catchError(() => of([] as ScenarioRead[]))),
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ meta, schoolYears, scenarios }) => {
          this.schools.set(meta.schools ?? []);
          this.schoolYears.set(schoolYears);
          const active = schoolYears.find((y) => y.isActive) ?? schoolYears[0];
          this.schoolYearId.set(active?.id ?? null);
          this.scenarios.set(scenarios);
          this.loading.set(false);
        },
        error: () => {
          this.error.set('Données indisponibles — backend à vérifier.');
          this.loading.set(false);
        },
      });
  }

  private computeScenarioById(id: string): void {
    this.busy.set(true);
    this.simulator
      .compute(id)
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        catchError((err) => {
          this.toast.set({
            kind: 'danger',
            message: this.extractError(err, 'Calcul d\'impact impossible.'),
          });
          this.busy.set(false);
          return of(null);
        }),
      )
      .subscribe((report) => {
        if (report) {
          this.impact.set(report);
          this.toast.set({
            kind: 'success',
            message: 'Impact calculé.',
          });
          // Met à jour le scénario dans la liste (status COMPUTED).
          this.scenarios.update((list) =>
            list.map((s) =>
              s.id === id
                ? {
                    ...s,
                    status: 'COMPUTED',
                    impactJson: report,
                    computedAt: new Date().toISOString(),
                  }
                : s,
            ),
          );
        }
        this.busy.set(false);
      });
  }

  private loadScenarioIntoEditor(scn: ScenarioRead): void {
    this.currentScenarioId.set(scn.id);
    const json = scn.scenarioJson as { operations?: Operation[] } | null;
    this.operations.set(json?.operations ?? []);
    this.mode.set('view');
  }

  private extractError(err: unknown, fallback: string): string {
    if (err && typeof err === 'object' && 'error' in err) {
      const e = (err as { error?: { detail?: string } }).error;
      if (e?.detail) return e.detail;
    }
    return fallback;
  }
}
