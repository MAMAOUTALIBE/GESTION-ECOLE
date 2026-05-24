import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { catchError, forkJoin, of } from 'rxjs';

import {
  CartographyApiService,
  GeoJsonFeatureCollection,
} from '../shared/cartography-api.service';
import type {
  LayerDescriptor,
  LayerId,
} from './layer-toggle-panel';
import { LayerTogglePanel } from './layer-toggle-panel';
import {
  ActiveLayerData,
  ReorganisationMap,
} from './reorganisation-map';
import { ReorganisationLegend } from './reorganisation-legend';

/**
 * Module 3A — Page principale de la réorganisation du réseau scolaire.
 *
 * Charge en parallèle les 6 couches cartographiques exposées par le backend,
 * affiche un panneau de toggle latéral et une carte Leaflet qui empile les
 * couches actives. État géré via signals — pas de NgRx.
 *
 * Pour rester non-bloquant lorsqu'une couche est lente ou en erreur :
 * - chaque appel a un catchError → FeatureCollection vide,
 * - le forkJoin n'échoue pas en bloc.
 */
@Component({
  selector: 'app-reorganisation-page',
  imports: [
    CommonModule,
    LayerTogglePanel,
    ReorganisationLegend,
    ReorganisationMap,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './reorganisation-page.html',
  styleUrl: './reorganisation-page.scss',
})
export class ReorganisationPage implements OnInit {
  private cartoApi = inject(CartographyApiService);
  private destroyRef = inject(DestroyRef);

  // --- état UI ---
  readonly loading = signal<boolean>(true);
  readonly error = signal<string | null>(null);
  readonly activeIds = signal<Set<LayerId>>(
    new Set<LayerId>(['gpi-critical-regions', 'capacity-critical-schools']),
  );

  // --- données par couche (FeatureCollection brute) ---
  readonly gpi = signal<GeoJsonFeatureCollection | null>(null);
  readonly capacity = signal<GeoJsonFeatureCollection | null>(null);
  readonly staffing = signal<GeoJsonFeatureCollection | null>(null);
  readonly infra = signal<GeoJsonFeatureCollection | null>(null);
  readonly zone = signal<GeoJsonFeatureCollection | null>(null);
  readonly whiteZones = signal<GeoJsonFeatureCollection | null>(null);

  // --- catalogue des couches (descripteurs : libellé, couleur, count) ---
  readonly layers = computed<LayerDescriptor[]>(() => [
    {
      id: 'gpi-critical-regions',
      label: 'Régions à GPI critique',
      description: 'GPI < 0.85 ou en alerte filles (Module 1B).',
      color: '#d63031',
      count: this.featureCount(this.gpi()),
    },
    {
      id: 'capacity-critical-schools',
      label: 'Écoles en saturation projetée',
      description: 'Saturation > 100 % en année t+1 (Module 2C).',
      color: '#e17055',
      count: this.featureCount(this.capacity()),
    },
    {
      id: 'staffing-critical-schools',
      label: 'Sous-effectif enseignants',
      description: 'UNDER_STAFFED ou CRITICAL (Module 2D).',
      color: '#fdcb6e',
      count: this.featureCount(this.staffing()),
    },
    {
      id: 'infrastructure-gaps',
      label: 'Lacunes infrastructure',
      description: 'Eau, électricité, latrines ou internet manquants.',
      color: '#6c5ce7',
      count: this.featureCount(this.infra()),
    },
    {
      id: 'zone-type',
      label: 'Urbain / rural / péri-urbain',
      description: 'Type de zone par sous-préfecture (Module 1C).',
      color: '#00b894',
      count: this.featureCount(this.zone()),
    },
    {
      id: 'white-zones-enriched',
      label: 'Zones blanches enrichies',
      description: 'Sous-préfectures > 5 km de toute école.',
      color: '#0984e3',
      count: this.featureCount(this.whiteZones()),
    },
  ]);

  readonly activeLayers = computed<ActiveLayerData[]>(() => {
    const active = this.activeIds();
    const out: ActiveLayerData[] = [];
    for (const descriptor of this.layers()) {
      if (!active.has(descriptor.id)) continue;
      const data = this.dataFor(descriptor.id);
      if (data === null) continue;
      out.push({ id: descriptor.id, descriptor, geoJson: data });
    }
    return out;
  });

  readonly totalActive = computed<number>(() => this.activeIds().size);

  ngOnInit(): void {
    this.loadAllLayers();
  }

  refresh(): void {
    this.loadAllLayers();
  }

  toggleLayer(id: LayerId): void {
    const next = new Set(this.activeIds());
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    this.activeIds.set(next);
  }

  private loadAllLayers(): void {
    this.loading.set(true);
    this.error.set(null);

    forkJoin({
      gpi: this.cartoApi
        .getGpiCriticalRegions()
        .pipe(catchError(() => of(this.emptyFc()))),
      capacity: this.cartoApi
        .getCapacityCriticalSchools()
        .pipe(catchError(() => of(this.emptyFc()))),
      staffing: this.cartoApi
        .getStaffingCriticalSchools()
        .pipe(catchError(() => of(this.emptyFc()))),
      infra: this.cartoApi
        .getInfrastructureGaps()
        .pipe(catchError(() => of(this.emptyFc()))),
      zone: this.cartoApi
        .getZoneTypeLayer()
        .pipe(catchError(() => of(this.emptyFc()))),
      whiteZones: this.cartoApi
        .getWhiteZonesEnriched()
        .pipe(catchError(() => of(this.emptyFc()))),
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ gpi, capacity, staffing, infra, zone, whiteZones }) => {
          this.gpi.set(gpi);
          this.capacity.set(capacity);
          this.staffing.set(staffing);
          this.infra.set(infra);
          this.zone.set(zone);
          this.whiteZones.set(whiteZones);
          this.loading.set(false);
        },
        error: () => {
          this.error.set(
            'Couches cartographiques indisponibles — backend ou cache à vérifier.',
          );
          this.loading.set(false);
        },
      });
  }

  private dataFor(id: LayerId): GeoJsonFeatureCollection | null {
    switch (id) {
      case 'gpi-critical-regions':
        return this.gpi();
      case 'capacity-critical-schools':
        return this.capacity();
      case 'staffing-critical-schools':
        return this.staffing();
      case 'infrastructure-gaps':
        return this.infra();
      case 'zone-type':
        return this.zone();
      case 'white-zones-enriched':
        return this.whiteZones();
    }
  }

  private featureCount(fc: GeoJsonFeatureCollection | null): number {
    return fc?.features?.length ?? 0;
  }

  private emptyFc(): GeoJsonFeatureCollection {
    return { type: 'FeatureCollection', features: [] };
  }
}
