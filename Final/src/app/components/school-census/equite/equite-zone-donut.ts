import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { ApexOptions } from 'ng-apexcharts';

import { SpkApexcharts } from '../../../@spk/charts/spk-apexcharts/spk-apexcharts';
import { ZoneAggregate } from '../shared/enrollment-api.service';

const ZONE_LABEL: Record<ZoneAggregate['zoneType'], string> = {
  URBAN: 'Urbain',
  RURAL: 'Rural',
  PERI_URBAN: 'Péri-urbain',
};

/**
 * Donut ApexCharts : effectifs par zone × genre (Filles vs Garçons).
 * Affiche un total par zone et permet de visualiser d'un coup la
 * distribution urbain / rural / péri-urbain.
 */
@Component({
  selector: 'app-equite-zone-donut',
  imports: [CommonModule, SpkApexcharts],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './equite-zone-donut.html',
  styleUrl: './equite-zone-donut.scss',
})
export class EquiteZoneDonut {
  zones = input.required<ZoneAggregate[]>();
  title = input<string>('Effectifs par zone');

  readonly hasData = computed(() =>
    (this.zones() ?? []).some((z) => (z.total ?? 0) > 0),
  );

  readonly totalLabel = computed<string>(() => {
    const total = (this.zones() ?? []).reduce(
      (acc, z) => acc + (z.total ?? 0),
      0,
    );
    return total.toLocaleString('fr-FR');
  });

  readonly options = computed<ApexOptions>(() => {
    const zones = this.zones() ?? [];
    const series = zones.map((z) => z.total ?? 0);
    const labels = zones.map((z) => ZONE_LABEL[z.zoneType] ?? z.zoneType);

    return {
      series,
      labels,
      chart: {
        type: 'donut',
        height: 320,
        fontFamily: 'inherit',
      },
      colors: ['#0d6efd', '#198754', '#ffc107'],
      legend: { position: 'bottom' },
      dataLabels: {
        enabled: true,
        formatter: (val: number) => `${val.toFixed(1)}%`,
      },
      plotOptions: {
        pie: {
          donut: {
            size: '65%',
            labels: {
              show: true,
              total: {
                show: true,
                label: 'Total élèves',
                formatter: () => this.totalLabel(),
              },
            },
          },
        },
      },
      tooltip: {
        y: { formatter: (val: number) => val.toLocaleString('fr-FR') },
      },
    };
  });
}
