import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
  output,
} from '@angular/core';

export type LayerId =
  | 'gpi-critical-regions'
  | 'capacity-critical-schools'
  | 'staffing-critical-schools'
  | 'infrastructure-gaps'
  | 'zone-type'
  | 'white-zones-enriched';

export interface LayerDescriptor {
  id: LayerId;
  label: string;
  description: string;
  color: string;
  count: number;
}

/**
 * Module 3A — Panneau latéral d'activation des 6 couches.
 *
 * - L'état "actif/inactif" est piloté par le parent via le signal `active`.
 * - Le composant émet `toggle` (id de la couche) ; le parent met à jour son
 *   propre Set<LayerId> de couches actives et déclenche le re-render de la
 *   carte. Permet une logique parent uniforme (tests, persistance future).
 * - Couleurs / libellés vivent dans le parent (signal `layers`) — ce
 *   composant reste purement présentationnel.
 */
@Component({
  selector: 'app-layer-toggle-panel',
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './layer-toggle-panel.html',
  styleUrl: './layer-toggle-panel.scss',
})
export class LayerTogglePanel {
  readonly layers = input.required<LayerDescriptor[]>();
  readonly active = input<Set<LayerId>>(new Set());
  readonly disabled = input<boolean>(false);

  readonly toggle = output<LayerId>();

  readonly totalActive = computed(() => this.active().size);

  isActive(id: LayerId): boolean {
    return this.active().has(id);
  }

  onToggle(id: LayerId): void {
    if (this.disabled()) return;
    this.toggle.emit(id);
  }
}
