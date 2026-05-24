import { CommonModule } from '@angular/common';
import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  OnDestroy,
  ViewChild,
  effect,
  inject,
  input,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import * as L from 'leaflet';

import { EnrollmentApiService, GpiResult } from '../shared/enrollment-api.service';
import { GuineaMapService } from '../shared/guinea-map.service';

/**
 * Carte Leaflet de la Guinée avec coloration par GPI régional.
 *
 * - Charge le GeoJSON via `GuineaMapService` (cache shareReplay).
 * - Match région ⇆ feature.properties.name|ADM1_FR|nom (heuristique tolérante).
 * - Légende fixe avec les seuils UNESCO/IIPE (0.85 / 0.97 / 1.03).
 */
@Component({
  selector: 'app-equite-region-map',
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './equite-region-map.html',
  styleUrl: './equite-region-map.scss',
})
export class EquiteRegionMap implements AfterViewInit, OnDestroy {
  @ViewChild('mapContainer', { static: true })
  mapContainer?: ElementRef<HTMLDivElement>;

  regions = input.required<GpiResult[]>();
  title = input<string>('Carte GPI régional');

  private guineaMap = inject(GuineaMapService);
  private destroyRef = inject(DestroyRef);
  private map?: L.Map;
  private geoLayer?: L.GeoJSON;
  private legend?: L.Control;
  private boundaryLoaded = false;

  constructor() {
    // Re-style la couche quand l'input change.
    effect(() => {
      const rows = this.regions();
      if (this.geoLayer && rows) {
        this.refreshStyles();
      }
    });
  }

  ngAfterViewInit(): void {
    if (!this.mapContainer?.nativeElement) return;
    const cfg = this.guineaMap.config;

    this.map = L.map(this.mapContainer.nativeElement, {
      center: cfg.center,
      zoom: cfg.zoom,
      minZoom: cfg.minZoom,
      maxZoom: cfg.maxZoom,
      maxBounds: cfg.maxBounds,
      maxBoundsViscosity: cfg.maxBoundsViscosity,
      attributionControl: true,
      zoomControl: true,
    });

    L.tileLayer(cfg.tileUrl, {
      attribution: cfg.tileAttribution,
      maxZoom: cfg.maxZoom,
    }).addTo(this.map);

    this.guineaMap
      .loadGuineaBoundary()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (geo) => this.applyGeoJson(geo),
        error: () => {
          // GeoJSON absent : on garde la carte vide (pas bloquant pour le reste de la page).
          this.boundaryLoaded = false;
        },
      });

    this.addLegend();
  }

  ngOnDestroy(): void {
    if (this.map) {
      this.map.remove();
      this.map = undefined;
    }
  }

  private applyGeoJson(geo: GeoJSON.FeatureCollection): void {
    if (!this.map) return;
    this.geoLayer = L.geoJSON(geo, {
      style: (feature) => this.styleForFeature(feature),
      onEachFeature: (feature, layer) => {
        const name = this.regionName(feature);
        const gpi = this.gpiForRegion(name);
        const value =
          gpi !== null && gpi !== undefined ? gpi.toFixed(4) : 'n/a';
        layer.bindTooltip(
          `<strong>${name ?? '—'}</strong><br/>GPI : ${value}`,
          { sticky: true },
        );
      },
    }).addTo(this.map);
    this.boundaryLoaded = true;
    try {
      const bounds = this.geoLayer.getBounds();
      if (bounds.isValid()) {
        this.map.fitBounds(bounds, { padding: [10, 10] });
      }
    } catch {
      // ignore
    }
  }

  private refreshStyles(): void {
    if (!this.geoLayer) return;
    this.geoLayer.setStyle((feature) => this.styleForFeature(feature));
  }

  private styleForFeature(feature: GeoJSON.Feature | undefined): L.PathOptions {
    const name = this.regionName(feature);
    const gpi = this.gpiForRegion(name);
    return {
      color: '#1a3a6e',
      weight: 1,
      opacity: 1,
      fillColor: this.colorForGpi(gpi),
      fillOpacity: gpi === null ? 0.15 : 0.55,
    };
  }

  private regionName(feature: GeoJSON.Feature | undefined): string | null {
    const props = (feature?.properties ?? {}) as Record<string, unknown>;
    const candidates = ['name', 'NAME', 'ADM1_FR', 'admin1Name', 'nom', 'shapeName'];
    for (const key of candidates) {
      const v = props[key];
      if (typeof v === 'string' && v.trim().length > 0) return v.trim();
    }
    return null;
  }

  private gpiForRegion(name: string | null): number | null {
    if (!name) return null;
    const needle = name.toLocaleLowerCase('fr');
    const found = (this.regions() ?? []).find((r) => {
      const hay = (r.entityName ?? '').toLocaleLowerCase('fr');
      return hay && (hay === needle || hay.includes(needle) || needle.includes(hay));
    });
    return found ? EnrollmentApiService.toNumber(found.gpi) : null;
  }

  private colorForGpi(gpi: number | null): string {
    if (gpi === null) return '#e9ecef';
    if (gpi < 0.85) return '#dc3545'; // critique
    if (gpi < 0.97) return '#ffc107'; // alerte filles
    if (gpi <= 1.03) return '#198754'; // parité
    return '#0dcaf0'; // alerte garçons
  }

  private addLegend(): void {
    if (!this.map) return;
    const legend = new L.Control({ position: 'bottomright' });
    legend.onAdd = () => {
      const div = L.DomUtil.create('div', 'equite-map-legend');
      div.innerHTML = `
        <div class="legend-title">Seuils GPI (UNESCO)</div>
        <div><span class="swatch" style="background:#dc3545"></span> &lt; 0.85 critique filles</div>
        <div><span class="swatch" style="background:#ffc107"></span> 0.85 – 0.97 alerte filles</div>
        <div><span class="swatch" style="background:#198754"></span> 0.97 – 1.03 parité</div>
        <div><span class="swatch" style="background:#0dcaf0"></span> &gt; 1.03 alerte garçons</div>
        <div><span class="swatch" style="background:#e9ecef"></span> donnée absente</div>
      `;
      return div;
    };
    legend.addTo(this.map);
    this.legend = legend;
  }
}
