import { Component, input } from '@angular/core';

@Component({
    selector: 'spk-dashboards-card',
    imports: [],
    templateUrl: './spk-dashboards-card.html',
    styleUrl: './spk-dashboards-card.scss'
})
export class SpkDashboardsCard {
  readonly title = input<string>('');
  readonly value = input<string>('');
  readonly sales = input<string>('');
  readonly textcolor = input<string>('');
  readonly Bgcolor = input<string>('');
  readonly status = input<string>('');
  readonly direction = input<string>('');
  readonly icon = input<string>('');
  readonly iconBg = input<string>('');
}


