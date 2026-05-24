import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { CommonModule } from '@angular/common';

export type GeButtonVariant = 'primary' | 'secondary' | 'ghost';
export type GeButtonSize = 'sm' | 'md' | 'lg';

/**
 * GeButton — composant vitrine du design system (Module 20).
 *
 * Standalone, consomme uniquement les tokens `--ge-*` via la classe
 * `.ge-button` (déclarée dans `styles/_ge-helpers.scss`).
 *
 * Exemple :
 *   <ge-button variant="primary" size="md">Enregistrer</ge-button>
 */
@Component({
  selector: 'ge-button',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <button
      type="button"
      [class]="cssClasses()"
      [disabled]="disabled()"
    >
      <ng-content />
    </button>
  `,
})
export class GeButtonComponent {
  readonly variant = input<GeButtonVariant>('primary');
  readonly size = input<GeButtonSize>('md');
  readonly disabled = input<boolean>(false);

  readonly cssClasses = computed(() => {
    const classes = ['ge-button'];
    const v = this.variant();
    if (v === 'secondary') classes.push('ge-button--secondary');
    if (v === 'ghost') classes.push('ge-button--ghost');
    const s = this.size();
    if (s === 'sm') classes.push('ge-button--sm');
    if (s === 'lg') classes.push('ge-button--lg');
    return classes.join(' ');
  });
}
