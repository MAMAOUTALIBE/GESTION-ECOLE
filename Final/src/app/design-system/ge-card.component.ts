import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { CommonModule } from '@angular/common';

export type GeCardElevation = 0 | 1 | 2 | 3;

/**
 * GeCard — surface conteneur du design system (Module 20).
 *
 * Le slot principal accepte n'importe quel contenu via `<ng-content>`.
 * L'élévation contrôle la profondeur de l'ombre portée.
 */
@Component({
  selector: 'ge-card',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div [class]="cssClasses()">
      <ng-content />
    </div>
  `,
})
export class GeCardComponent {
  readonly elevation = input<GeCardElevation>(1);

  readonly cssClasses = computed(() => {
    const classes = ['ge-card'];
    const e = this.elevation();
    if (e === 2) classes.push('ge-card--raised');
    if (e === 3) classes.push('ge-card--floating');
    return classes.join(' ');
  });
}
