import { CommonModule } from '@angular/common';
import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  OnDestroy,
  ViewChild,
  computed,
  effect,
  inject,
  input,
} from '@angular/core';
import * as L from 'leaflet';

import {
  GeoJsonFeatureCollection,
} from '../shared/cartography-api.service';
import { GuineaMapService } from '../shared/guinea-map.service';
import type { LayerDescriptor, LayerId } from './layer-toggle-panel';

export interface ActiveLayerData {
  id: LayerId;
  descriptor: LayerDescriptor;
  geoJson: GeoJsonFeatureCollection;
}

/**
 * Module 3A — Carte Leaflet pour la réorganisation du réseau.
 *
 * Reçoit la liste des couches actives via `activeLayers` (input). À chaque
 * changement, on retire toutes les couches précédentes et on rajoute les
 * nouvelles via `GuineaMapService.addGeoJsonLayer`. Cette approche
 * "remove all + re-add" est volontairement simple : on évite la
 * synchronisation incrémentale fragile pour 6 couches max.
 *
 * Pourquoi un effect() plutôt qu'un computed() ?
 * -----------------------------------------------
 * Les changements doivent provoquer un side-effect Leaflet (mutation DOM
 * via L.geoJSON.addTo). Les `effect` Angular sont conçus pour ça : ils se
 * relancent quand un signal d'entrée change.
 */
@Component({
  selector: 'app-reorganisation-map',
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './reorganisation-map.html',
  styleUrl: './reorganisation-map.scss',
})
export class ReorganisationMap implements AfterViewInit, OnDestroy {
  private guineaMap = inject(GuineaMapService);

  @ViewChild('mapContainer', { static: true })
  mapContainer!: ElementRef<HTMLDivElement>;

  readonly activeLayers = input<ActiveLayerData[]>([]);

  readonly totalFeatures = computed<number>(() =>
    this.activeLayers().reduce(
      (sum, l) => sum + (l.geoJson.features?.length ?? 0),
      0,
    ),
  );

  private map?: L.Map;
  private leafletLayers: L.Layer[] = [];
  private mapReady = false;
  private destroyed = false;

  constructor() {
    // Effect : à chaque changement de activeLayers, on re-render les couches.
    effect(() => {
      const layers = this.activeLayers();
      if (this.mapReady && !this.destroyed) {
        this.renderLayers(layers);
      }
    });

    // Note : pas de hook DestroyRef ici — ngOnDestroy fait déjà le cleanup.
    // Double remove() leaflet provoque l'erreur "Map container is being
    // reused" (cf. tests Angular qui partagent le DOM entre fixtures).
  }

  ngAfterViewInit(): void {
    this.initMap();
    this.mapReady = true;
    // Premier rendu manuel — effect() s'attache au prochain changement.
    this.renderLayers(this.activeLayers());
  }

  ngOnDestroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.cleanupLayers();
    this.map?.remove();
    this.map = undefined;
  }

  private initMap(): void {
    const cfg = this.guineaMap.config;
    this.map = L.map(this.mapContainer.nativeElement, {
      scrollWheelZoom: false,
      zoomControl: true,
      minZoom: cfg.minZoom,
      maxZoom: cfg.maxZoom,
      maxBounds: cfg.maxBounds,
      maxBoundsViscosity: cfg.maxBoundsViscosity,
    }).setView(cfg.center, cfg.zoom);

    L.tileLayer(cfg.tileUrl, {
      maxZoom: cfg.maxZoom,
      minZoom: cfg.minZoom,
      attribution: cfg.tileAttribution,
    }).addTo(this.map);

    this.guineaMap.loadGuineaBoundary().subscribe({
      next: (geojson) => {
        if (!this.map) return;
        L.geoJSON(geojson, {
          style: () => cfg.borderStyle,
          interactive: false,
        }).addTo(this.map);
      },
      error: () => undefined,
    });
    // Invalide la taille pour le cas où le container n'est pas encore mesuré
    setTimeout(() => this.map?.invalidateSize(), 0);
  }

  private renderLayers(active: ActiveLayerData[]): void {
    if (!this.map) return;
    this.cleanupLayers();

    for (const layer of active) {
      const features = layer.geoJson.features ?? [];
      if (features.length === 0) continue;
      const leafletLayer = this.guineaMap.addGeoJsonLayer(
        this.map,
        layer.geoJson as unknown as GeoJSON.FeatureCollection,
        {
          radius: layer.id === 'zone-type' ? 6 : 9,
          color: layer.descriptor.color,
          fillColor: layer.descriptor.color,
          fillOpacity: 0.55,
          weight: 2,
        },
        layer.id,
      );
      leafletLayer.bindPopup((feat) => this.popupFor(layer.id, feat as any));
      this.leafletLayers.push(leafletLayer);
    }
  }

  private cleanupLayers(): void {
    if (!this.map) return;
    for (const lyr of this.leafletLayers) {
      this.map.removeLayer(lyr);
    }
    this.leafletLayers = [];
  }

  private popupFor(layerId: LayerId, feature: GeoJSON.Feature): string {
    const props = (feature.properties ?? {}) as Record<string, unknown>;
    const safe = (v: unknown): string => this.escape(String(v ?? '—'));
    switch (layerId) {
      case 'gpi-critical-regions':
        return `
          <strong>${safe(props['regionName'])}</strong><br>
          GPI : ${safe(props['gpi'])} (${safe(props['severity'])})<br>
          Filles : ${safe(props['girlsCount'])} · Garçons : ${safe(props['boysCount'])}
        `;
      case 'capacity-critical-schools':
        return `
          <strong>${safe(props['name'])}</strong> (${safe(props['code'])})<br>
          Capacité : ${safe(props['capacity'])}<br>
          Demande : ${safe(props['demand'])} (saturation ${safe(props['saturationPct'])} %)<br>
          Année projetée : ${safe(props['projectedYear'])}
        `;
      case 'staffing-critical-schools':
        return `
          <strong>${safe(props['name'])}</strong> (${safe(props['code'])})<br>
          Élèves : ${safe(props['studentsCount'])}<br>
          Enseignants : ${safe(props['teachersCount'])} (ratio ${safe(props['ratio'])})<br>
          Manque : ${safe(props['gap'])}
        `;
      case 'infrastructure-gaps': {
        const gaps = Array.isArray(props['gaps']) ? (props['gaps'] as string[]).join(', ') : '—';
        return `
          <strong>${safe(props['name'])}</strong> (${safe(props['code'])})<br>
          Lacunes : ${this.escape(gaps)}
        `;
      }
      case 'zone-type':
        return `
          <strong>${safe(props['subPrefectureName'])}</strong><br>
          Zone : ${safe(props['zoneType'])}<br>
          Écoles : ${safe(props['schoolCount'])}
        `;
      case 'white-zones-enriched':
        return `
          <strong>${safe(props['subPrefectureName'])}</strong><br>
          École la plus proche : ${safe(props['nearestSchoolKm'])} km<br>
          Population estimée : ${safe(props['estimatedPopulation'])}
        `;
    }
  }

  private escape(value: string): string {
    return value
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
}
