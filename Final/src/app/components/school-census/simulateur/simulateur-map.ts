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
  output,
  signal,
} from '@angular/core';
import * as L from 'leaflet';

import { GuineaMapService } from '../shared/guinea-map.service';
import { School } from '../shared/school-census.models';
import { Operation } from '../shared/simulator-api.service';
import { SimulatorMode } from './operations-panel';

/**
 * Module 3B UI — Carte Leaflet du simulateur what-if.
 *
 * Comportement selon le mode (input `mode`) :
 *   - view   : marqueurs cliquables, popup d'info.
 *   - create : clic carte → output `opAdded` ({ type: CREATE_SCHOOL, lat, lon, ... }).
 *              On crée une école par défaut "Nouvelle école" / capacité 200.
 *   - close  : clic marqueur → output `opAdded` ({ type: CLOSE_SCHOOL, ... }).
 *   - merge  : clic marqueur → ajoute à une sélection interne ; un bouton
 *              "Fusionner" valide quand ≥ 2 écoles cochées.
 *
 * Pour rester fluide, on n'affiche que les écoles géolocalisées. Le cursor
 * et la couleur des marqueurs sont calculés selon le mode pour donner
 * un feedback visuel immédiat.
 *
 * Cleanup Leaflet : ngOnDestroy retire les marqueurs et appelle map.remove().
 */
const NEUTRAL_COLOR = '#1a3a6e';
const PENDING_CLOSE_COLOR = '#ff2e57';
const PENDING_CREATE_COLOR = '#16e07a';
const PENDING_MERGE_COLOR = '#0984e3';

@Component({
  selector: 'app-simulateur-map',
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './simulateur-map.html',
  styleUrl: './simulateur-map.scss',
})
export class SimulateurMap implements AfterViewInit, OnDestroy {
  private guineaMap = inject(GuineaMapService);

  @ViewChild('mapContainer', { static: true })
  mapContainer!: ElementRef<HTMLDivElement>;

  schools = input<School[]>([]);
  operations = input<Operation[]>([]);
  mode = input<SimulatorMode>('view');

  /** Émet une nouvelle opération à ajouter au scénario. */
  readonly opAdded = output<Operation>();

  /** Sélection en cours pour le mode merge (≥ 2 écoles). */
  readonly mergeSelection = signal<string[]>([]);

  readonly mergeCount = computed<number>(() => this.mergeSelection().length);

  /** Map cursor selon le mode (utile pour le CSS). */
  readonly cursorClass = computed<string>(() => {
    switch (this.mode()) {
      case 'create':
        return 'sim-cursor-create';
      case 'close':
        return 'sim-cursor-close';
      case 'merge':
        return 'sim-cursor-merge';
      default:
        return 'sim-cursor-view';
    }
  });

  private map?: L.Map;
  private markersLayer?: L.LayerGroup;
  private mapClickHandler?: (e: L.LeafletMouseEvent) => void;
  private mapReady = false;
  private destroyed = false;
  /** ids écoles fermées (CLOSE_SCHOOL ou source d'un MERGE) — affichés grisés. */
  private closedIds = new Set<string>();

  constructor() {
    effect(() => {
      // Re-render quand les schools, ops, mode ou sélection mergent changent.
      this.schools();
      this.operations();
      this.mode();
      this.mergeSelection();
      if (this.mapReady && !this.destroyed) {
        this.recomputeClosed();
        this.renderMarkers();
      }
    });
  }

  ngAfterViewInit(): void {
    this.initMap();
    this.mapReady = true;
    this.recomputeClosed();
    this.renderMarkers();
  }

  ngOnDestroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    if (this.map && this.mapClickHandler) {
      this.map.off('click', this.mapClickHandler);
    }
    this.markersLayer?.clearLayers();
    this.map?.remove();
    this.map = undefined;
  }

  /** Valide la fusion en cours (≥ 2 écoles cochées). */
  confirmMerge(): void {
    const selected = this.mergeSelection();
    if (selected.length < 2) return;
    const sources = (this.schools() ?? []).filter((s) =>
      selected.includes(s.id),
    );
    // Centroïde simple pour positionner la fusion (moyenne arithmétique).
    const lats = sources
      .map((s) => s.latitude ?? null)
      .filter((v): v is number => v !== null);
    const lons = sources
      .map((s) => s.longitude ?? null)
      .filter((v): v is number => v !== null);
    if (lats.length === 0 || lons.length === 0) return;
    const lat = lats.reduce((a, b) => a + b, 0) / lats.length;
    const lon = lons.reduce((a, b) => a + b, 0) / lons.length;
    const targetName = `Fusion (${sources.length} écoles)`;
    this.opAdded.emit({
      type: 'MERGE_SCHOOLS',
      sourceSchoolIds: selected,
      targetName,
      lat,
      lon,
      subPrefectureId: sources[0]?.subPrefectureId ?? null,
    });
    this.mergeSelection.set([]);
  }

  cancelMerge(): void {
    this.mergeSelection.set([]);
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

    this.markersLayer = L.layerGroup().addTo(this.map);

    // Click sur la carte : utile en mode create.
    this.mapClickHandler = (e: L.LeafletMouseEvent) => this.handleMapClick(e);
    this.map.on('click', this.mapClickHandler);

    setTimeout(() => this.map?.invalidateSize(), 0);
  }

  private handleMapClick(e: L.LeafletMouseEvent): void {
    if (this.mode() !== 'create') return;
    // Filtre : Leaflet remonte aussi les clicks sur marqueurs ; on garde
    // seulement les clics sur fond de carte.
    const target = (e.originalEvent.target as HTMLElement | null) ?? null;
    if (target && target.classList.contains('leaflet-marker-icon')) {
      return;
    }
    this.opAdded.emit({
      type: 'CREATE_SCHOOL',
      name: 'Nouvelle école',
      lat: Number(e.latlng.lat.toFixed(5)),
      lon: Number(e.latlng.lng.toFixed(5)),
      capacity: 200,
      subPrefectureId: null,
    });
  }

  private renderMarkers(): void {
    if (!this.map || !this.markersLayer) return;
    this.markersLayer.clearLayers();

    const mergeSelected = new Set<string>(this.mergeSelection());
    const newSchoolsFromOps = (this.operations() ?? []).filter(
      (o) => o.type === 'CREATE_SCHOOL' || o.type === 'MERGE_SCHOOLS',
    );

    // 1) Écoles existantes
    for (const school of this.schools() ?? []) {
      const lat = school.latitude;
      const lng = school.longitude;
      if (lat === null || lat === undefined || lng === null || lng === undefined) {
        continue;
      }
      const isClosed = this.closedIds.has(school.id);
      const isMerging = mergeSelected.has(school.id);
      const color = isClosed
        ? '#888'
        : isMerging
          ? PENDING_MERGE_COLOR
          : NEUTRAL_COLOR;
      const radius = isMerging ? 9 : 6;
      const opacity = isClosed ? 0.35 : 0.85;

      const marker = L.circleMarker([lat, lng], {
        radius,
        color,
        fillColor: color,
        fillOpacity: opacity,
        weight: isMerging ? 3 : 1,
        // En mode close/merge, on veut que le marker soit "cliquable" et
        // que le click ne déclenche pas le click carte.
        interactive: true,
      });
      marker.bindPopup(this.popupFor(school));
      marker.on('click', (ev) => {
        L.DomEvent.stopPropagation(ev);
        this.handleMarkerClick(school);
      });
      marker.addTo(this.markersLayer);
    }

    // 2) Écoles "fantômes" issues d'ops CREATE / MERGE (couleur verte)
    let pendingIndex = 0;
    for (const op of newSchoolsFromOps) {
      const lat = op.type === 'CREATE_SCHOOL' ? op.lat : op.lat;
      const lon = op.type === 'CREATE_SCHOOL' ? op.lon : op.lon;
      const marker = L.circleMarker([lat, lon], {
        radius: 8,
        color: PENDING_CREATE_COLOR,
        fillColor: PENDING_CREATE_COLOR,
        fillOpacity: 0.7,
        weight: 2,
        className: 'sim-pending-marker',
        interactive: true,
      });
      const labelName =
        op.type === 'CREATE_SCHOOL' ? op.name : op.targetName;
      marker.bindPopup(`<strong>${this.escape(labelName)}</strong><br>Opération simulée`);
      marker.addTo(this.markersLayer);
      pendingIndex += 1;
    }

    if (pendingIndex >= 0) {
      // no-op, garde le linter content sur l'usage de pendingIndex.
    }
  }

  private handleMarkerClick(school: School): void {
    const m = this.mode();
    if (m === 'close') {
      this.opAdded.emit({ type: 'CLOSE_SCHOOL', schoolId: school.id });
      return;
    }
    if (m === 'merge') {
      const selected = new Set(this.mergeSelection());
      if (selected.has(school.id)) {
        selected.delete(school.id);
      } else {
        selected.add(school.id);
      }
      this.mergeSelection.set([...selected]);
    }
  }

  private recomputeClosed(): void {
    this.closedIds.clear();
    for (const op of this.operations() ?? []) {
      if (op.type === 'CLOSE_SCHOOL') {
        this.closedIds.add(op.schoolId);
      } else if (op.type === 'MERGE_SCHOOLS') {
        for (const id of op.sourceSchoolIds) {
          this.closedIds.add(id);
        }
      }
    }
  }

  private popupFor(school: School): string {
    const closed = this.closedIds.has(school.id);
    const status = closed ? ' (fermée par scénario)' : '';
    return [
      `<strong>${this.escape(school.name)}${status}</strong>`,
      `Code : ${this.escape(school.code)}`,
      `Région : ${this.escape(school.region?.name ?? '—')}`,
    ].join('<br>');
  }

  private escape(value: string): string {
    return value
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
}
