import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
} from '@angular/core';

import type { LayerDescriptor, LayerId } from './layer-toggle-panel';

/**
 * Module 3A — Légende dynamique de la carte de réorganisation.
 *
 * N'affiche QUE les couches actives — la légende d'une carte vide est
 * silencieuse, ce qui évite le bruit cognitif quand on travaille sur
 * une seule couche à la fois.
 */
@Component({
  selector: 'app-reorganisation-legend',
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './reorganisation-legend.html',
  styleUrl: './reorganisation-legend.scss',
})
export class ReorganisationLegend {
  readonly layers = input.required<LayerDescriptor[]>();
  readonly active = input<Set<LayerId>>(new Set());

  readonly visibleLayers = computed<LayerDescriptor[]>(() =>
    this.layers().filter((l) => this.active().has(l.id)),
  );
}
