import { CommonModule } from '@angular/common';
import { Component, DestroyRef, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { forkJoin, of } from 'rxjs';
import { catchError } from 'rxjs/operators';
import {
  AnalyticsApiService,
  CohortLevelStats,
  CohortReport,
  EquityResponse,
  EquityRow,
  PolicySimulationResponse,
} from '../shared/analytics-api.service';

@Component({
  selector: 'app-policy-decision',
  imports: [CommonModule, FormsModule],
  templateUrl: './policy-decision.html',
  styleUrl: './policy-decision.scss',
})
export class PolicyDecision {
  private analyticsApi = inject(AnalyticsApiService);
  private destroyRef = inject(DestroyRef);

  loadingDashboard = false;
  loadingSimulation = false;
  error = '';

  cohort?: CohortReport;
  equity?: EquityResponse;
  simulation?: PolicySimulationResponse;

  // Simulator form (valeurs par défaut = scénario raisonnable)
  scenario = {
    addSchools: 50,
    addTeachers: 200,
    addClassrooms: 150,
    targetGirlsToiletsCoverage: 80,
    targetElectricityCoverage: 90,
    horizonYears: 5,
  };

  ngOnInit() {
    this.loadDashboard();
  }

  // =======================================================================
  // Cohort + Equity (chargés en parallèle au montage)
  // =======================================================================
  loadDashboard() {
    this.loadingDashboard = true;
    this.error = '';
    forkJoin({
      cohort: this.analyticsApi.cohorts(),
      equity: this.analyticsApi.equity(),
    })
      .pipe(
        catchError(() => of(null)),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((result) => {
        if (!result) {
          this.error =
            "Données analytiques indisponibles — vérifier que le backend est démarré.";
        } else {
          this.cohort = result.cohort;
          this.equity = result.equity;
        }
        this.loadingDashboard = false;
      });
  }

  // =======================================================================
  // Policy simulator (déclenché par le bouton « Simuler »)
  // =======================================================================
  simulate() {
    this.loadingSimulation = true;
    this.analyticsApi
      .policySimulator({
        addSchools: this.scenario.addSchools,
        addTeachers: this.scenario.addTeachers,
        addClassrooms: this.scenario.addClassrooms,
        targetGirlsToiletsCoverage: this.scenario.targetGirlsToiletsCoverage || null,
        targetElectricityCoverage: this.scenario.targetElectricityCoverage || null,
        horizonYears: this.scenario.horizonYears,
      })
      .pipe(
        catchError(() => of(null)),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((result) => {
        this.simulation = result ?? undefined;
        this.loadingSimulation = false;
      });
  }

  resetScenario() {
    this.scenario = {
      addSchools: 50,
      addTeachers: 200,
      addClassrooms: 150,
      targetGirlsToiletsCoverage: 80,
      targetElectricityCoverage: 90,
      horizonYears: 5,
    };
    this.simulation = undefined;
  }

  // =======================================================================
  // Helpers de présentation
  // =======================================================================
  formatNumber(value?: number | null): string {
    return (value ?? 0).toLocaleString('fr-FR');
  }

  formatCurrencyUSD(value?: number | null): string {
    if (value === null || value === undefined) return '—';
    return `${Math.round(value).toLocaleString('fr-FR')} USD`;
  }

  formatPercent(value?: number | null): string {
    if (value === null || value === undefined) return '—';
    return `${value.toFixed(1)}%`;
  }

  /** Codes couleur basés sur le GPI : 0.97-1.03 → équité, sinon attention. */
  gpiClass(value: number): string {
    if (value >= 0.97 && value <= 1.03) return 'text-success fw-semibold';
    if (value >= 0.90 && value < 0.97) return 'text-warning fw-semibold';
    return 'text-danger fw-semibold';
  }

  coverageClass(value: number): string {
    if (value >= 80) return 'bg-success-transparent text-success';
    if (value >= 50) return 'bg-warning-transparent text-warning';
    return 'bg-danger-transparent text-danger';
  }

  /** Le delta côté simulation : amélioration vs dégradation visuel. */
  deltaClass(interpretation: string): string {
    if (interpretation.startsWith('Amélioration')) return 'text-success';
    if (interpretation.startsWith('Dégradation')) return 'text-danger';
    return 'text-muted';
  }

  cohortRows(): CohortLevelStats[] {
    return this.cohort?.levels ?? [];
  }

  equityRows(): EquityRow[] {
    return this.equity?.rows ?? [];
  }
}
