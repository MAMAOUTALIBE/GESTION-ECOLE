import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ThemeService } from '../../theme/theme.service';

/**
 * Bouton-icône qui cycle `light` -> `dark` -> `auto`.
 *
 * - Affiche un soleil en mode light, une lune en dark, un cercle moitié en auto.
 * - Utilise les icônes Remix Icon déjà chargées par Spruko (`ri-*`).
 * - Standalone Angular, à brancher dans le header existant.
 */
@Component({
  selector: 'app-theme-toggle',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './theme-toggle.component.html',
  styleUrls: ['./theme-toggle.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ThemeToggleComponent {
  private readonly theme = inject(ThemeService);

  readonly mode = this.theme.mode;

  readonly iconClass = computed(() => {
    switch (this.mode()) {
      case 'dark':
        return 'ri-moon-line';
      case 'auto':
        return 'ri-contrast-2-line';
      case 'light':
      default:
        return 'ri-sun-line';
    }
  });

  readonly label = computed(() => {
    switch (this.mode()) {
      case 'dark':
        return 'Thème : sombre';
      case 'auto':
        return 'Thème : automatique';
      case 'light':
      default:
        return 'Thème : clair';
    }
  });

  onToggle(): void {
    this.theme.cycle();
  }
}
