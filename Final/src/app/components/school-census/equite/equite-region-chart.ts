import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { ApexOptions } from 'ng-apexcharts';

import { SpkApexcharts } from '../../../@spk/charts/spk-apexcharts/spk-apexcharts';
import { EnrollmentApiService, GpiResult } from '../shared/enrollment-api.service';

/**
 * Bar chart horizontal des GPI régionaux avec lignes seuils
 * UNESCO/IIPE (0.85 critique filles, 0.97 parité acceptable basse,
 * 1.03 parité acceptable haute).
 *
 * Composant 100 % présentationnel : prend une liste de `GpiResult` et
 * la transforme en options ApexCharts via un `computed()` signal.
 */
@Component({
  selector: 'app-equite-region-chart',
  imports: [CommonModule, SpkApexcharts],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './equite-region-chart.html',
  styleUrl: './equite-region-chart.scss',
})
export class EquiteRegionChart {
  rows = input.required<GpiResult[]>();
  title = input<string>('GPI par région');

  readonly options = computed<ApexOptions>(() => {
    const rows = this.rows() ?? [];
    const sorted = [...rows].sort((a, b) => {
      const av = EnrollmentApiService.toNumber(a.gpi) ?? 0;
      const bv = EnrollmentApiService.toNumber(b.gpi) ?? 0;
      return av - bv;
    });
    const categories = sorted.map((r) => r.entityName ?? r.entityId ?? '—');
    const data = sorted.map((r) => EnrollmentApiService.toNumber(r.gpi) ?? 0);
    const colors = sorted.map((r) => this.severityColor(r.severity));

    return {
      series: [{ name: 'GPI', data }],
      chart: {
        type: 'bar',
        height: Math.max(280, 28 * Math.max(categories.length, 4)),
        toolbar: { show: false },
        fontFamily: 'inherit',
      },
      plotOptions: {
        bar: {
          horizontal: true,
          borderRadius: 4,
          distributed: true,
          dataLabels: { position: 'top' },
        },
      },
      colors,
      dataLabels: {
        enabled: true,
        offsetX: 28,
        style: { fontSize: '11px', colors: ['#6c757d'] },
        formatter: (val: number) => val.toFixed(2),
      },
      xaxis: {
        categories,
        title: { text: 'Indice de parité filles/garçons (GPI)' },
        min: 0,
        max: 1.2,
      },
      yaxis: { labels: { style: { fontSize: '12px' } } },
      legend: { show: false },
      annotations: {
        xaxis: [
          {
            x: 0.85,
            borderColor: '#dc3545',
            strokeDashArray: 4,
            label: {
              borderColor: '#dc3545',
              style: { color: '#fff', background: '#dc3545', fontSize: '10px' },
              text: 'Seuil critique 0.85',
            },
          },
          {
            x: 0.97,
            borderColor: '#198754',
            strokeDashArray: 4,
            label: {
              borderColor: '#198754',
              style: { color: '#fff', background: '#198754', fontSize: '10px' },
              text: 'Parité 0.97',
            },
          },
        ],
      },
      tooltip: {
        y: { formatter: (val: number) => `${val.toFixed(4)} (GPI)` },
      },
    };
  });

  readonly hasData = computed(() => (this.rows() ?? []).length > 0);

  private severityColor(severity: GpiResult['severity']): string {
    switch (severity) {
      case 'CRITICAL_GIRLS':
        return '#dc3545';
      case 'WARNING_GIRLS':
        return '#ffc107';
      case 'WARNING_BOYS':
        return '#0dcaf0';
      case 'NORMAL':
        return '#198754';
      default:
        return '#6c757d';
    }
  }
}
