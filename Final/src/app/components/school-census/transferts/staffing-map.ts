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
import {
  StaffingApiService,
  StaffingSeverity,
  TeacherStaffingSnapshot,
} from '../shared/staffing-api.service';

/**
 * Module 2D UI — Carte Leaflet des dotations enseignants par école.
 *
 * Marqueurs colorés par sévérité (voir SEVERITY_COLORS) avec popup
 * compact au clic. Toggle filtre interne via boutons (ALL / UNDER / OVER /
 * CRITICAL) — l'état est conservé dans un signal local pour rester
 * indépendant de la page parente. Le composant emit `selectSchool` quand
 * l'utilisateur clique un marqueur → la page peut highlight la table.
 *
 * Cleanup Leaflet conforme aux conventions du Module 3A : on retire les
 * marqueurs avant un re-render, et on remove() la carte dans ngOnDestroy.
 */
type SeverityFilter = 'ALL' | StaffingSeverity;

const SEVERITY_COLORS: Record<StaffingSeverity, string> = {
  CRITICAL: '#d63031',
  UNDER_STAFFED: '#fdcb6e',
  ADEQUATE: '#16e07a',
  OVER_STAFFED: '#0984e3',
};

@Component({
  selector: 'app-staffing-map',
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './staffing-map.html',
  styleUrl: './staffing-map.scss',
})
export class StaffingMap implements AfterViewInit, OnDestroy {
  private guineaMap = inject(GuineaMapService);

  @ViewChild('mapContainer', { static: true })
  mapContainer!: ElementRef<HTMLDivElement>;

  snapshots = input<TeacherStaffingSnapshot[]>([]);
  schools = input<School[]>([]);
  selectedSchoolId = input<string | null>(null);

  /** Émet l'id école sélectionnée. */
  readonly selectSchool = output<string>();

  readonly filter = signal<SeverityFilter>('ALL');

  readonly visibleCount = computed<number>(() => this.filtered().length);

  readonly filtered = computed<TeacherStaffingSnapshot[]>(() => {
    const f = this.filter();
    const list = this.snapshots() ?? [];
    if (f === 'ALL') return list;
    return list.filter((s) => s.severity === f);
  });

  private map?: L.Map;
  private markersLayer?: L.LayerGroup;
  private mapReady = false;
  private destroyed = false;
  private markerById = new Map<string, L.CircleMarker>();

  constructor() {
    effect(() => {
      // Re-render quand les snapshots changent, le filtre change, ou la
      // sélection change.
      this.filtered();
      this.selectedSchoolId();
      if (this.mapReady && !this.destroyed) {
        this.renderMarkers();
      }
    });
  }

  ngAfterViewInit(): void {
    this.initMap();
    this.mapReady = true;
    this.renderMarkers();
  }

  ngOnDestroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.cleanupMarkers();
    this.map?.remove();
    this.map = undefined;
  }

  setFilter(value: SeverityFilter): void {
    this.filter.set(value);
  }

  filterButtonClass(target: SeverityFilter): string {
    return this.filter() === target
      ? 'btn btn-primary btn-sm'
      : 'btn btn-outline-primary btn-sm';
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
    setTimeout(() => this.map?.invalidateSize(), 0);
  }

  private renderMarkers(): void {
    if (!this.map || !this.markersLayer) return;
    this.cleanupMarkers();
    const schoolsById = new Map<string, School>();
    for (const s of this.schools() ?? []) {
      schoolsById.set(s.id, s);
    }

    const selectedId = this.selectedSchoolId();

    for (const snap of this.filtered()) {
      const school = schoolsById.get(snap.schoolId);
      if (!school) continue;
      const lat = school.latitude;
      const lng = school.longitude;
      if (lat === null || lat === undefined || lng === null || lng === undefined) {
        continue;
      }

      const isSelected = selectedId === snap.schoolId;
      const color = SEVERITY_COLORS[snap.severity];
      const radius = isSelected ? 11 : 7;

      const marker = L.circleMarker([lat, lng], {
        radius,
        color,
        fillColor: color,
        fillOpacity: 0.7,
        weight: isSelected ? 3 : 1,
      });
      marker.bindPopup(this.popupFor(school, snap));
      marker.on('click', () => this.selectSchool.emit(snap.schoolId));
      marker.addTo(this.markersLayer);
      this.markerById.set(snap.schoolId, marker);
    }
  }

  private cleanupMarkers(): void {
    if (this.markersLayer) {
      this.markersLayer.clearLayers();
    }
    this.markerById.clear();
  }

  private popupFor(
    school: School,
    snap: TeacherStaffingSnapshot,
  ): string {
    const ratio = StaffingApiService.toNumber(snap.ratio);
    const ratioStr = ratio === null ? '—' : ratio.toFixed(1);
    return [
      `<strong>${this.escape(school.name)}</strong>`,
      `Élèves : ${snap.studentsCount} · Enseignants : ${snap.teachersCount}`,
      `Ratio : ${ratioStr}`,
      `Gap : ${snap.gap}`,
      `Sévérité : ${this.escape(snap.severity)}`,
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
