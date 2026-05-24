import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { NetworkService } from '../../network.service';

/**
 * OfflineBannerComponent — Module 16 PWA
 *
 * Standalone banner that displays a small fixed bar at the top of the
 * viewport whenever `window.navigator.onLine === false`. Hidden otherwise.
 *
 * Color palette is intentionally neutral and additive — it does not
 * override Spruko theme tokens.
 */
@Component({
  selector: 'app-offline-banner',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './offline-banner.component.html',
  styleUrls: ['./offline-banner.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class OfflineBannerComponent {
  private readonly network = inject(NetworkService);
  readonly online$ = this.network.online$;
}
