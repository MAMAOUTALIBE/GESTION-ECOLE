import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, shareReplay } from 'rxjs';
import * as L from 'leaflet';

export type AlertLevel = 'critical' | 'warning' | 'normal';

export interface SchoolAlert {
  level: AlertLevel;
  /** Motif de l'alerte (null si normal). */
  reason: string | null;
  ratio: number | null;
  presenceRate: number | null;
}

/**
 * Configuration centralisée de la carte scolaire.
 *
 * Toutes les constantes territoriales (centre, zoom min, bornes, style frontière,
 * URL des tuiles) vivent ici pour qu'aucun composant n'ait à les ré-encoder.
 */
export interface GuineaMapConfig {
  center: L.LatLngExpression;
  zoom: number;
  minZoom: number;
  maxZoom: number;
  /** Bornes approximatives englobant le territoire de la Guinée (avec marge). */
  maxBounds: L.LatLngBoundsExpression;
  maxBoundsViscosity: number;
  tileUrl: string;
  tileAttribution: string;
  borderStyle: L.PathOptions;
}

@Injectable({ providedIn: 'root' })
export class GuineaMapService {
  private http = inject(HttpClient);

  /**
   * Bornes de la Guinée (lat min/max, lng min/max) avec ~0.5° de marge — laisse
   * un peu de respiration en bord de carte sans laisser apparaître le territoire
   * d'un voisin.
   */
  readonly config: GuineaMapConfig = {
    center: [10.8, -11.0],
    zoom: 7,
    minZoom: 7,
    maxZoom: 18,
    maxBounds: [
      [6.7, -15.6], // sud-ouest
      [13.2, -7.1], // nord-est
    ],
    maxBoundsViscosity: 1.0,
    // Carto Dark Matter : fond sombre adapté à l'esthétique "centre de pilotage" néon.
    tileUrl: 'https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png',
    tileAttribution:
      '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> · © <a href="https://carto.com/attributions">CARTO</a>',
    borderStyle: {
      // Frontière néon vert phosphorescent. Le glow CSS est appliqué via
      // la classe `.guinea-border-glow` (filter: drop-shadow).
      color: '#16e07a',
      weight: 3,
      opacity: 1,
      fillOpacity: 0,
      fillColor: 'transparent',
      className: 'guinea-border-glow',
    },
  };

  private readonly geoJsonUrl = '/assets/guinea.geojson';
  private geoJson$?: Observable<GeoJSON.FeatureCollection>;

  /**
   * Charge le GeoJSON des frontières de la Guinée. Mis en cache via shareReplay
   * pour ne pas re-télécharger à chaque navigation vers la carte.
   */
  loadGuineaBoundary(): Observable<GeoJSON.FeatureCollection> {
    if (!this.geoJson$) {
      this.geoJson$ = this.http
        .get<GeoJSON.FeatureCollection>(this.geoJsonUrl)
        .pipe(shareReplay(1));
    }
    return this.geoJson$;
  }

  /**
   * Calcule le niveau d'alerte d'une école selon les règles métier :
   *  - 🔴 critical : 0 enseignant, 0 élève, ou ratio > 45
   *  - 🟠 warning  : ratio ∈ [35, 45] ou taux présence < 70%
   *  - 🟢 normal   : sinon
   */
  computeAlert(
    students: number,
    teachers: number,
    presenceRate: number | null = null,
  ): SchoolAlert {
    if (teachers <= 0) {
      return {
        level: 'critical',
        reason: 'Aucun enseignant assigné',
        ratio: null,
        presenceRate,
      };
    }
    if (students <= 0) {
      return {
        level: 'critical',
        reason: 'Aucun élève inscrit',
        ratio: null,
        presenceRate,
      };
    }
    const ratio = students / teachers;
    if (ratio > 45) {
      return {
        level: 'critical',
        reason: `Ratio élèves/enseignant ${ratio.toFixed(1)} (> 45)`,
        ratio,
        presenceRate,
      };
    }
    if (ratio >= 35) {
      return {
        level: 'warning',
        reason: `Ratio élèves/enseignant ${ratio.toFixed(1)} (35–45)`,
        ratio,
        presenceRate,
      };
    }
    if (presenceRate !== null && presenceRate < 70) {
      return {
        level: 'warning',
        reason: `Taux de présence ${presenceRate.toFixed(1)}% (< 70%)`,
        ratio,
        presenceRate,
      };
    }
    return { level: 'normal', reason: null, ratio, presenceRate };
  }

  /**
   * Construit l'icône Leaflet pour un marqueur d'école.
   * Niveau 'normal' = pastille fixe ; 'warning'/'critical' = pastille + halo
   * pulsé (animation CSS désactivée si prefers-reduced-motion).
   */
  buildPulseIcon(level: AlertLevel): L.DivIcon {
    const className = `pulse-marker pulse-${this.cssSuffixForLevel(level)}`;
    return L.divIcon({
      className: 'pulse-marker-wrapper',
      html: `
        <span class="${className}" aria-hidden="true">
          <span class="pulse-ring"></span>
          <span class="pulse-dot"></span>
        </span>
      `,
      iconSize: [18, 18],
      iconAnchor: [9, 9],
      popupAnchor: [0, -10],
      tooltipAnchor: [0, -10],
    });
  }

  private cssSuffixForLevel(level: AlertLevel): 'red' | 'orange' | 'green' {
    switch (level) {
      case 'critical': return 'red';
      case 'warning': return 'orange';
      default: return 'green';
    }
  }

  /**
   * Construit un marqueur "néon" pour la carte de pilotage.
   *
   * Couleur déterminée par le type d'école :
   *   - PUBLIC      → vert  (#16e07a)
   *   - PRIVATE     → rouge (#ff2e57)
   *   - COMMUNITY   → jaune (#ffd60a)
   *   - défaut      → vert
   *
   * Taille selon l'effectif élèves :
   *   ≤ 50  → 12px ; ≤ 200 → 16px ; ≤ 500 → 22px ; > 500 → 28px
   *
   * L'animation de pulsation néon est désactivée pour les écoles en état
   * `normal` lorsqu'il y a beaucoup de marqueurs (perf) — c'est l'appelant
   * qui décide en ajoutant la classe `cs-marker-static`.
   */
  buildNeonMarkerIcon(
    level: AlertLevel,
    schoolType: 'PUBLIC' | 'PRIVATE' | 'COMMUNITY' | string,
    studentsCount: number,
  ): L.DivIcon {
    const color = this.neonColorForType(schoolType);
    const size = this.neonSizeForStudents(studentsCount);
    // Le `level` ajuste l'intensité de l'animation : critical/warning gardent
    // la pulsation, normal peut être atténuée.
    const levelClass = level === 'normal' ? 'cs-marker-calm' : `cs-marker-alert-${level}`;
    const html =
      `<span class="cs-marker cs-marker-${color} ${levelClass}" ` +
      `style="--size:${size}px" aria-hidden="true"></span>`;
    return L.divIcon({
      className: 'cs-marker-wrapper',
      html,
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
      popupAnchor: [0, -size / 2 - 2],
      tooltipAnchor: [0, -size / 2 - 2],
    });
  }

  private neonColorForType(schoolType: string): 'green' | 'red' | 'yellow' {
    const type = (schoolType ?? '').toUpperCase();
    if (type === 'PRIVATE') return 'red';
    if (type === 'COMMUNITY') return 'yellow';
    return 'green';
  }

  private neonSizeForStudents(students: number): number {
    if (students <= 50) return 12;
    if (students <= 200) return 16;
    if (students <= 500) return 22;
    return 28;
  }

  /**
   * Module 3A — Helper d'ajout d'une couche GeoJSON à une carte Leaflet.
   *
   * Le composant `reorganisation-map` empile jusqu'à 6 couches togglables ;
   * passer par ce helper standardise la création (style, tooltip, gestion
   * des Points avec marqueurs cerclés) et évite à chaque composant de
   * ré-implémenter le marshalling.
   *
   * @param map     instance Leaflet.Map déjà initialisée.
   * @param geoJson FeatureCollection à afficher (GeoJSON RFC 7946).
   * @param style   options de style (rayon, couleur, opacité).
   * @param name    libellé interne (utile pour debugging / removeLayer).
   *
   * @returns       la couche Leaflet.GeoJSON créée et déjà ajoutée à la
   *                carte. Le composant l'enregistre pour pouvoir la retirer
   *                au toggle off.
   */
  addGeoJsonLayer(
    map: L.Map,
    geoJson: GeoJSON.FeatureCollection,
    style: {
      radius?: number;
      color: string;
      fillColor?: string;
      fillOpacity?: number;
      weight?: number;
    },
    name: string,
  ): L.GeoJSON {
    const layer = L.geoJSON(geoJson, {
      pointToLayer: (_feature, latlng) =>
        L.circleMarker(latlng, {
          radius: style.radius ?? 8,
          color: style.color,
          fillColor: style.fillColor ?? style.color,
          fillOpacity: style.fillOpacity ?? 0.6,
          weight: style.weight ?? 1,
        }),
    });
    // Stocke le nom dans la closure pour le debug Leaflet (devtools).
    (layer as L.GeoJSON & { _layerName?: string })._layerName = name;
    layer.addTo(map);
    return layer;
  }
}
