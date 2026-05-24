import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { CommonModule } from '@angular/common';

export type GeBadgeVariant = 'success' | 'warning' | 'danger' | 'info';

/**
 * GeBadge — pastille statut du design system (Module 20).
 *
 * Variantes alignées avec les couleurs sémantiques GE :
 *  - `success` -> vert savane
 *  - `warning` -> ocre Sahel
 *  - `danger`  -> rouge
 *  - `info`    -> indigo nuit
 */
@Component({
  selector: 'ge-badge',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <span [class]="cssClasses()">
      <ng-content />
    </span>
  `,
})
export class GeBadgeComponent {
  readonly variant = input<GeBadgeVariant>('info');

  readonly cssClasses = computed(() => `ge-badge ge-badge--${this.variant()}`);
}
