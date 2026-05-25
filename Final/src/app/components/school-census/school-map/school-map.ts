import { CommonModule } from '@angular/common';
import {
  AfterViewInit,
  Component,
  DestroyRef,
  OnDestroy,
  OnInit,
  inject,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import * as L from 'leaflet';
import { interval } from 'rxjs';
import { AnalyticsApiService } from '../shared/analytics-api.service';
import { CensusApiService } from '../shared/census-api.service';
import { GuineaMapService, SchoolAlert } from '../shared/guinea-map.service';
import { SchoolAdminService } from '../shared/school-admin.service';
import { Region, School } from '../shared/school-census.models';

interface RegionCoverage {
  id: string;
  name: string;
  schools: number;
  geolocated: number;
  students: number;
  teachers: number;
  gpsRate: number;
}

interface CensusTotals {
  students: number;
  teachers: number;
  classes: number;
}

interface AlertSummary {
  critical: number;
  warning: number;
  normal: number;
}

const REFRESH_INTERVAL_MS = 5 * 60 * 1000;
const PRESENCE_LOOKUP_LIMIT = 100;

@Component({
  selector: 'app-school-map',
  imports: [CommonModule, FormsModule],
  templateUrl: './school-map.html',
  styleUrl: './school-map.scss',
})
export class SchoolMap implements OnInit, AfterViewInit, OnDestroy {
  private schoolApi = inject(SchoolAdminService);
  private censusApi = inject(CensusApiService);
  private analyticsApi = inject(AnalyticsApiService);
  private guineaMap = inject(GuineaMapService);
  private destroyRef = inject(DestroyRef);
  private map?: L.Map;
  private markerLayer = L.layerGroup();
  private boundaryLayer?: L.GeoJSON;
  private legendControl?: L.Control;
  private markerBySchoolId = new Map<string, L.Marker>();
  /** Cache des taux de présence (renvoyés par /api/analytics/top-schools). */
  private presenceBySchoolId = new Map<string, number>();
  /** Cache des alertes calculées par école (clé = id). */
  private alertBySchoolId = new Map<string, SchoolAlert>();
  private viewInitialized = false;

  schools: School[] = [];
  filteredSchools: School[] = [];
  regionCoverage: RegionCoverage[] = [];
  selectedSchool?: School;
  selectedRegionId = '';
  selectedType = '';
  searchTerm = '';
  loading = false;
  error = '';

  /** Stats nationales servies par /api/census/dashboard (indépendantes des filtres locaux). */
  censusTotals: CensusTotals = { students: 0, teachers: 0, classes: 0 };

  ngOnInit() {
    this.loadAll();
    // Auto-rafraîchissement toutes les 5 minutes pour suivre l'évolution
    // des alertes en quasi-temps-réel sans rechargement de page.
    interval(REFRESH_INTERVAL_MS)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => this.loadAll());
  }

  private loadAll() {
    this.loadSchoolsWithAlerts();
    this.loadCensusTotals();
  }

  ngAfterViewInit() {
    this.viewInitialized = true;
    this.renderMap();
  }

  ngOnDestroy() {
    this.map?.remove();
  }

  get regions(): Region[] {
    const regions = new Map<string, Region>();
    this.schools.forEach((school) => {
      if (school.region) {
        regions.set(school.region.id, school.region);
      }
    });
    return Array.from(regions.values()).sort((left, right) => left.name.localeCompare(right.name));
  }

  get schoolTypes(): string[] {
    return Array.from(new Set(this.schools.map((school) => school.type).filter(Boolean) as string[])).sort();
  }

  get geolocatedSchools() {
    return this.filteredSchools.filter((school) => this.hasCoordinates(school));
  }

  get missingGpsSchools() {
    return this.filteredSchools.filter((school) => !this.hasCoordinates(school));
  }

  get totals() {
    return {
      schools: this.filteredSchools.length,
      geolocated: this.geolocatedSchools.length,
      missingGps: this.missingGpsSchools.length,
      students: this.filteredSchools.reduce((sum, school) => sum + (school.counts?.students ?? 0), 0),
      teachers: this.filteredSchools.reduce((sum, school) => sum + (school.counts?.teachers ?? 0), 0),
      classes: this.filteredSchools.reduce((sum, school) => sum + (school.counts?.classes ?? 0), 0),
    };
  }

  get gpsRate() {
    return this.totals.schools ? Math.round((this.totals.geolocated / this.totals.schools) * 100) : 0;
  }

  /** Synthèse des alertes pour l'overlay au-dessus de la carte (suit les filtres). */
  get alertSummary(): AlertSummary {
    const summary: AlertSummary = { critical: 0, warning: 0, normal: 0 };
    for (const school of this.filteredSchools) {
      const alert = this.alertBySchoolId.get(school.id);
      if (!alert) continue;
      summary[alert.level]++;
    }
    return summary;
  }

  /** Conserve l'ancien handler du bouton « Actualiser » pour ne pas casser le HTML. */
  loadSchools() {
    this.loadAll();
  }

  private loadSchoolsWithAlerts() {
    this.loading = true;
    this.error = '';

    // Pré-charge les taux de présence en parallèle (best-effort) puis les écoles.
    // Les schools restants sans donnée de présence seront évalués sur le seul ratio.
    this.analyticsApi.topSchools('attendance', PRESENCE_LOOKUP_LIMIT).subscribe({
      next: (response) => {
        this.presenceBySchoolId.clear();
        for (const row of response.rows) {
          if (row.presenceRateLast7Days !== null && row.presenceRateLast7Days !== undefined) {
            this.presenceBySchoolId.set(row.id, row.presenceRateLast7Days);
          }
        }
      },
      error: () => {
        // Non-bloquant : on calcule les alertes sans le critère de présence.
        this.presenceBySchoolId.clear();
      },
      complete: () => this.fetchSchools(),
    });
  }

  private fetchSchools() {
    this.schoolApi.listSchools().subscribe({
      next: (schools) => {
        this.schools = schools;
        this.recomputeAlerts();
        this.applyFilters();
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger la carte scolaire.';
        this.loading = false;
      },
    });
  }

  private recomputeAlerts() {
    this.alertBySchoolId.clear();
    for (const school of this.schools) {
      const students = school.counts?.students ?? 0;
      const teachers = school.counts?.teachers ?? 0;
      const presence = this.presenceBySchoolId.get(school.id) ?? null;
      this.alertBySchoolId.set(
        school.id,
        this.guineaMap.computeAlert(students, teachers, presence),
      );
    }
  }

  loadCensusTotals() {
    this.censusApi.dashboard().subscribe({
      next: (dashboard) => {
        this.censusTotals = {
          students: dashboard.totals.students ?? 0,
          teachers: dashboard.totals.teachers ?? 0,
          classes: dashboard.totals.classes ?? 0,
        };
      },
      error: () => {
        // Stats restent à 0 — le bandeau d'erreur principal informe déjà l'utilisateur.
        this.censusTotals = { students: 0, teachers: 0, classes: 0 };
      },
    });
  }

  applyFilters() {
    const search = this.searchTerm.trim().toLowerCase();
    this.filteredSchools = this.schools.filter((school) => {
      const matchesRegion = !this.selectedRegionId || school.regionId === this.selectedRegionId;
      const matchesType = !this.selectedType || school.type === this.selectedType;
      const searchable = [
        school.name,
        school.code,
        school.region?.name,
        school.prefecture,
        school.commune,
        school.address,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();

      return matchesRegion && matchesType && (!search || searchable.includes(search));
    });
    this.regionCoverage = this.buildRegionCoverage();
    this.refreshMarkers();
  }

  resetFilters() {
    this.selectedRegionId = '';
    this.selectedType = '';
    this.searchTerm = '';
    this.applyFilters();
  }

  focusSchool(school: School) {
    this.selectedSchool = school;
    const marker = this.markerBySchoolId.get(school.id);
    if (!marker || !this.map || school.latitude === null || school.longitude === null) {
      return;
    }

    this.map.setView([school.latitude ?? 0, school.longitude ?? 0], Math.max(this.map.getZoom(), 12), {
      animate: true,
    });
    marker.openPopup();
  }

  formatNumber(value?: number | null) {
    return (value ?? 0).toLocaleString('fr-FR');
  }

  hasCoordinates(school: School) {
    return (
      school.latitude !== null &&
      school.latitude !== undefined &&
      school.longitude !== null &&
      school.longitude !== undefined
    );
  }

  private renderMap() {
    if (!this.viewInitialized || this.map) {
      return;
    }

    const cfg = this.guineaMap.config;

    this.map = L.map('school-census-map', {
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
        if (!this.map) {
          return;
        }
        this.boundaryLayer = L.geoJSON(geojson, {
          style: () => cfg.borderStyle,
          interactive: false,
        }).addTo(this.map);
      },
      // Si le GeoJSON ne charge pas, la contrainte maxBounds tient déjà le pan.
      error: () => undefined,
    });

    this.markerLayer.addTo(this.map);
    this.addLegend();
    this.refreshMarkers();
    setTimeout(() => this.map?.invalidateSize(), 0);
  }

  private addLegend() {
    if (!this.map || this.legendControl) {
      return;
    }
    const ctrl = new L.Control({ position: 'bottomleft' });
    ctrl.onAdd = () => {
      const div = L.DomUtil.create('div', 'map-legend');
      div.innerHTML = `
        <div class="legend-title">Niveau d'alerte</div>
        <div class="legend-row"><span class="legend-dot dot-red"></span>Critique</div>
        <div class="legend-row"><span class="legend-dot dot-orange"></span>Attention</div>
        <div class="legend-row"><span class="legend-dot dot-green"></span>Normal</div>
      `;
      // Empêche le drag/zoom de la carte quand l'utilisateur survole la légende
      L.DomEvent.disableClickPropagation(div);
      L.DomEvent.disableScrollPropagation(div);
      return div;
    };
    ctrl.addTo(this.map);
    this.legendControl = ctrl;
  }

  private refreshMarkers() {
    if (!this.map) {
      return;
    }

    this.markerLayer.clearLayers();
    this.markerBySchoolId.clear();

    this.geolocatedSchools.forEach((school) => {
      const alert = this.alertBySchoolId.get(school.id)
        ?? this.guineaMap.computeAlert(
          school.counts?.students ?? 0,
          school.counts?.teachers ?? 0,
          this.presenceBySchoolId.get(school.id) ?? null,
        );

      const marker = L.marker([school.latitude ?? 0, school.longitude ?? 0], {
        // Marqueur "néon" : couleur = type d'école, taille = effectif élèves,
        // intensité d'animation = niveau d'alerte.
        icon: this.guineaMap.buildNeonMarkerIcon(
          alert.level,
          school.type ?? '',
          school.counts?.students ?? 0,
        ),
        // Place les marqueurs critiques au-dessus des autres dans la pile Z
        zIndexOffset: alert.level === 'critical' ? 1000 : alert.level === 'warning' ? 500 : 0,
        riseOnHover: true,
      });

      marker.bindTooltip(this.tooltipContent(school, alert), {
        direction: 'top',
        opacity: 1,
        className: 'school-alert-tooltip',
      });
      marker.bindPopup(this.tooltipContent(school, alert));
      marker.on('click', () => {
        this.selectedSchool = school;
      });

      marker.addTo(this.markerLayer);
      this.markerBySchoolId.set(school.id, marker);
    });

    const cfg = this.guineaMap.config;
    const bounds = L.latLngBounds(this.geolocatedSchools.map((school) => [school.latitude ?? 0, school.longitude ?? 0]));
    if (bounds.isValid()) {
      this.map.fitBounds(bounds.pad(0.18), { maxZoom: 12 });
    } else {
      this.map.setView(cfg.center, cfg.zoom);
    }

    setTimeout(() => this.map?.invalidateSize(), 0);
  }

  private buildRegionCoverage(): RegionCoverage[] {
    const rows = new Map<string, RegionCoverage>();

    this.filteredSchools.forEach((school) => {
      const region = school.region ?? { id: school.regionId, name: 'Région non renseignée', code: '' };
      const existing =
        rows.get(region.id) ??
        ({
          id: region.id,
          name: region.name,
          schools: 0,
          geolocated: 0,
          students: 0,
          teachers: 0,
          gpsRate: 0,
        } satisfies RegionCoverage);

      existing.schools += 1;
      existing.geolocated += this.hasCoordinates(school) ? 1 : 0;
      existing.students += school.counts?.students ?? 0;
      existing.teachers += school.counts?.teachers ?? 0;
      existing.gpsRate = existing.schools ? Math.round((existing.geolocated / existing.schools) * 100) : 0;
      rows.set(region.id, existing);
    });

    return Array.from(rows.values()).sort((left, right) => right.students - left.students);
  }

  private tooltipContent(school: School, alert: SchoolAlert) {
    const students = school.counts?.students ?? 0;
    const teachers = school.counts?.teachers ?? 0;
    const ratioStr = alert.ratio !== null ? alert.ratio.toFixed(1) : '—';
    const presenceStr = alert.presenceRate !== null
      ? `${alert.presenceRate.toFixed(1)}%`
      : 'Non communiqué';

    let alertBlock = '';
    if (alert.reason) {
      const cls = alert.level === 'critical' ? 'tip-alert-critical' : 'tip-alert-warning';
      const prefix = alert.level === 'critical' ? '⚠ Alerte critique : ' : 'Attention : ';
      alertBlock = `<div class="${cls}">${prefix}${this.escapeHtml(alert.reason)}</div>`;
    }

    return `
      <div class="tip-title">${this.escapeHtml(school.name)}</div>
      <div class="tip-meta">${this.escapeHtml(school.code)} · ${this.escapeHtml(school.region?.name ?? 'Région N/A')}</div>
      <dl class="tip-stats">
        <dt>Élèves</dt><dd>${this.formatNumber(students)}</dd>
        <dt>Enseignants</dt><dd>${this.formatNumber(teachers)}</dd>
        <dt>Ratio é/e</dt><dd>${ratioStr}</dd>
        <dt>Présence</dt><dd>${presenceStr}</dd>
      </dl>
      ${alertBlock}
    `;
  }

  private escapeHtml(value: string) {
    return value
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }
}
