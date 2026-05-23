import { CommonModule } from '@angular/common';
import { Component, DestroyRef, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { catchError, forkJoin, of } from 'rxjs';
import {
  Anomaly,
  AssistantChatResponse,
  DropoutRiskRow,
  DropoutSummary,
  EnrollmentForecast,
  IntelligenceApiService,
  SiteRecommendation,
} from '../shared/intelligence-api.service';

@Component({
  selector: 'app-ministerial-dashboard',
  imports: [CommonModule, FormsModule],
  templateUrl: './ministerial-dashboard.html',
  styleUrl: './ministerial-dashboard.scss',
})
export class MinisterialDashboard {
  private intelligence = inject(IntelligenceApiService);
  private destroyRef = inject(DestroyRef);

  loading = true;
  error = '';

  dropout?: DropoutSummary;
  forecast?: EnrollmentForecast;
  anomalies: Anomaly[] = [];
  recommendations: SiteRecommendation[] = [];

  // Assistant LLM
  question = '';
  asking = false;
  conversation: { who: 'user' | 'ai'; text: string; tools?: string[] }[] = [];

  ngOnInit() {
    this.refresh();
  }

  refresh() {
    this.loading = true;
    this.error = '';

    forkJoin({
      dropout: this.intelligence.dropoutRisk({ limit: 10, minScore: 50 }),
      forecast: this.intelligence.enrollmentForecast(5),
      anomalies: this.intelligence.scanAnomalies(8),
      recos: this.intelligence.siteRecommendations(5),
    })
      .pipe(catchError(() => of(null)), takeUntilDestroyed(this.destroyRef))
      .subscribe((r) => {
        if (!r) {
          this.error = 'Impossible de charger le tableau de bord ministériel.';
        } else {
          this.dropout = r.dropout;
          this.forecast = r.forecast;
          this.anomalies = r.anomalies;
          this.recommendations = r.recos.recommendations;
        }
        this.loading = false;
      });
  }

  ask() {
    const q = this.question.trim();
    if (!q || this.asking) return;
    this.asking = true;
    this.conversation.push({ who: 'user', text: q });
    this.question = '';

    this.intelligence.chat({ message: q })
      .pipe(catchError((err) =>
        of<AssistantChatResponse>({
          reply: 'Erreur : ' + (err?.error?.detail ?? 'service indisponible.'),
          citations: [], toolsUsed: [],
        }),
      ), takeUntilDestroyed(this.destroyRef))
      .subscribe((res) => {
        this.conversation.push({ who: 'ai', text: res.reply, tools: res.toolsUsed });
        this.asking = false;
      });
  }

  riskBadgeClass(level: DropoutRiskRow['riskLevel']) {
    return {
      critical: 'bg-danger-transparent text-danger',
      high: 'bg-warning-transparent text-warning',
      medium: 'bg-info-transparent text-info',
      low: 'bg-success-transparent text-success',
    }[level] ?? '';
  }

  severityClass(sev: 'low' | 'medium' | 'high') {
    return ({
      high: 'bg-danger-transparent text-danger',
      medium: 'bg-warning-transparent text-warning',
      low: 'bg-info-transparent text-info',
    } as const)[sev] ?? '';
  }

  formatNumber(n: number | undefined | null) {
    return (n ?? 0).toLocaleString('fr-FR');
  }
}
