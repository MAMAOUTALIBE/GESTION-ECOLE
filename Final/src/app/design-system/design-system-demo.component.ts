import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { GeButtonComponent } from './ge-button.component';
import { GeCardComponent } from './ge-card.component';
import { GeBadgeComponent } from './ge-badge.component';
import { ThemeToggleComponent } from '../shared/components/theme-toggle/theme-toggle.component';
import { LanguageSwitcherComponent } from '../shared/components/language-switcher/language-switcher.component';
import { ThemeService } from '../shared/theme/theme.service';

/**
 * Page vitrine `/design-system` — Module 20.
 *
 * Affiche les tokens, composants Ge*, ainsi que les contrôles ThemeToggle
 * et LanguageSwitcher pour validation visuelle (light vs dark).
 */
@Component({
  selector: 'app-design-system-demo',
  standalone: true,
  imports: [
    CommonModule,
    GeButtonComponent,
    GeCardComponent,
    GeBadgeComponent,
    ThemeToggleComponent,
    LanguageSwitcherComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: [
    `
      :host {
        display: block;
        padding: var(--ge-space-6);
        background-color: var(--ge-color-surface);
        color: var(--ge-color-text);
        min-height: 100vh;
        font-family: var(--ge-font-sans);
      }
      h1, h2 {
        font-family: var(--ge-font-display);
        color: var(--ge-color-accent);
      }
      h1 { font-size: var(--ge-font-size-2xl); margin-bottom: var(--ge-space-2); }
      h2 { font-size: var(--ge-font-size-xl); margin-top: var(--ge-space-8); }
      .ge-toolbar {
        display: flex;
        align-items: center;
        gap: var(--ge-space-3);
        margin-bottom: var(--ge-space-6);
        flex-wrap: wrap;
      }
      .ge-swatches {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
        gap: var(--ge-space-4);
        margin-top: var(--ge-space-4);
      }
      .ge-swatch {
        border-radius: var(--ge-radius-md);
        padding: var(--ge-space-4);
        color: var(--ge-color-text-inverse);
        font-size: var(--ge-font-size-sm);
        font-weight: var(--ge-font-weight-medium);
        box-shadow: var(--ge-shadow-1);
      }
      .ge-row { display: flex; gap: var(--ge-space-3); align-items: center; flex-wrap: wrap; margin-top: var(--ge-space-3); }
    `,
  ],
  template: `
    <h1>GE-Design — Vitrine</h1>
    <p>Mode courant : <strong>{{ currentTheme() }}</strong></p>

    <div class="ge-toolbar">
      <app-theme-toggle />
      <app-language-switcher />
    </div>

    <h2>Palette</h2>
    <div class="ge-swatches">
      <div class="ge-swatch" style="background:var(--ge-color-primary)">Ocre Sahel<br>--ge-color-primary</div>
      <div class="ge-swatch" style="background:var(--ge-color-secondary)">Vert savane<br>--ge-color-secondary</div>
      <div class="ge-swatch" style="background:var(--ge-color-accent)">Indigo nuit<br>--ge-color-accent</div>
      <div class="ge-swatch" style="background:var(--ge-color-surface); color:var(--ge-color-text); border:1px solid var(--ge-color-border)">Blanc kaolin<br>--ge-color-surface</div>
    </div>

    <h2>Boutons</h2>
    <div class="ge-row">
      <ge-button variant="primary">Primary</ge-button>
      <ge-button variant="secondary">Secondary</ge-button>
      <ge-button variant="ghost">Ghost</ge-button>
      <ge-button variant="primary" size="sm">Small</ge-button>
      <ge-button variant="primary" size="lg">Large</ge-button>
      <ge-button variant="primary" [disabled]="true">Disabled</ge-button>
    </div>

    <h2>Cartes</h2>
    <div class="ge-row">
      <ge-card [elevation]="1"><strong>Carte standard</strong><p>Élévation 1</p></ge-card>
      <ge-card [elevation]="2"><strong>Carte relevée</strong><p>Élévation 2</p></ge-card>
      <ge-card [elevation]="3"><strong>Carte flottante</strong><p>Élévation 3</p></ge-card>
    </div>

    <h2>Badges</h2>
    <div class="ge-row">
      <ge-badge variant="success">Validé</ge-badge>
      <ge-badge variant="warning">En attente</ge-badge>
      <ge-badge variant="danger">Rejeté</ge-badge>
      <ge-badge variant="info">Info</ge-badge>
    </div>
  `,
})
export class DesignSystemDemoComponent {
  private readonly theme = inject(ThemeService);
  readonly currentTheme = this.theme.currentTheme;
}
