import { CommonModule } from '@angular/common';
import { AfterViewInit, ChangeDetectorRef, Component, OnDestroy, inject } from '@angular/core';
import { RouterModule } from '@angular/router';
import * as L from 'leaflet';
import { ApexOptions } from 'ng-apexcharts';
import { SpkApexcharts } from '../../../@spk/charts/spk-apexcharts/spk-apexcharts';
import { ValidationStatus } from '../../school-census/shared/school-census.models';

interface TerritoryLocation {
  id: string;
  code: string;
  name: string;
  type: 'prefecture' | 'sub-prefecture';
  region: string;
  prefecture?: string;
  latitude: number;
  longitude: number;
  schools: number;
  students: number;
  teachers: number;
  classrooms: number;
  gpsRate: number;
  validationStatus: ValidationStatus;
  priority: 'low' | 'medium' | 'high';
}

interface TerritoryKpi {
  label: string;
  value: string;
  detail: string;
  icon: string;
  color: string;
}

@Component({
  selector: 'app-dashboard-3',
  imports: [CommonModule, RouterModule, SpkApexcharts],
  templateUrl: './dashboard-3.html',
  styleUrl: './dashboard-3.scss',
})
export class Dashboard3 implements AfterViewInit, OnDestroy {
  private cdr = inject(ChangeDetectorRef);
  private map?: L.Map;
  private markerLayer = L.layerGroup();
  private markerByTerritoryId = new Map<string, L.CircleMarker>();

  readonly territories: TerritoryLocation[] = [
    {
      id: 'pref-conakry',
      code: 'GN-CNK',
      name: 'Conakry',
      type: 'prefecture',
      region: 'Conakry',
      latitude: 9.6412,
      longitude: -13.5784,
      schools: 412,
      students: 184920,
      teachers: 6120,
      classrooms: 4380,
      gpsRate: 94,
      validationStatus: 'APPROVED',
      priority: 'low',
    },
    {
      id: 'pref-kindia',
      code: 'GN-KD',
      name: 'Kindia',
      type: 'prefecture',
      region: 'Kindia',
      latitude: 10.0569,
      longitude: -12.8658,
      schools: 286,
      students: 104680,
      teachers: 3290,
      classrooms: 2514,
      gpsRate: 83,
      validationStatus: 'SUBMITTED',
      priority: 'medium',
    },
    {
      id: 'pref-kankan',
      code: 'GN-KK',
      name: 'Kankan',
      type: 'prefecture',
      region: 'Kankan',
      latitude: 10.3854,
      longitude: -9.3057,
      schools: 331,
      students: 128450,
      teachers: 3684,
      classrooms: 2980,
      gpsRate: 76,
      validationStatus: 'SUBMITTED',
      priority: 'medium',
    },
    {
      id: 'pref-nzerekore',
      code: 'GN-NZ',
      name: 'Nzérékoré',
      type: 'prefecture',
      region: 'Nzérékoré',
      latitude: 7.7562,
      longitude: -8.8179,
      schools: 304,
      students: 97260,
      teachers: 2844,
      classrooms: 2312,
      gpsRate: 68,
      validationStatus: 'DRAFT',
      priority: 'high',
    },
    {
      id: 'sub-matoto',
      code: 'CNK-MTO',
      name: 'Matoto',
      type: 'sub-prefecture',
      region: 'Conakry',
      prefecture: 'Conakry',
      latitude: 9.5737,
      longitude: -13.6304,
      schools: 126,
      students: 59880,
      teachers: 2024,
      classrooms: 1420,
      gpsRate: 96,
      validationStatus: 'APPROVED',
      priority: 'low',
    },
    {
      id: 'sub-dixinn',
      code: 'CNK-DIX',
      name: 'Dixinn',
      type: 'sub-prefecture',
      region: 'Conakry',
      prefecture: 'Conakry',
      latitude: 9.5555,
      longitude: -13.6722,
      schools: 92,
      students: 42160,
      teachers: 1480,
      classrooms: 980,
      gpsRate: 91,
      validationStatus: 'APPROVED',
      priority: 'low',
    },
    {
      id: 'sub-macenta',
      code: 'NZ-MAC',
      name: 'Macenta',
      type: 'sub-prefecture',
      region: 'Nzérékoré',
      prefecture: 'Macenta',
      latitude: 8.5435,
      longitude: -9.4712,
      schools: 74,
      students: 24440,
      teachers: 714,
      classrooms: 552,
      gpsRate: 61,
      validationStatus: 'DRAFT',
      priority: 'high',
    },
    {
      id: 'sub-siguiri',
      code: 'KK-SIG',
      name: 'Siguiri',
      type: 'sub-prefecture',
      region: 'Kankan',
      prefecture: 'Siguiri',
      latitude: 11.4149,
      longitude: -9.1674,
      schools: 88,
      students: 35220,
      teachers: 912,
      classrooms: 714,
      gpsRate: 72,
      validationStatus: 'SUBMITTED',
      priority: 'medium',
    },
  ];

  selectedTerritory: TerritoryLocation = this.territories[0];

  readonly kpis: TerritoryKpi[] = [
    {
      label: 'Préfectures suivies',
      value: this.formatNumber(this.prefectures.length),
      detail: 'Territoires consolidés',
      icon: 'ri-map-2-line',
      color: 'primary',
    },
    {
      label: 'Sous-préfectures',
      value: this.formatNumber(this.subPrefectures.length),
      detail: 'Zones opérationnelles',
      icon: 'ri-road-map-line',
      color: 'info',
    },
    {
      label: 'Établissements',
      value: this.formatNumber(this.totalSchools),
      detail: 'Écoles rattachées',
      icon: 'ri-school-line',
      color: 'success',
    },
    {
      label: 'Couverture GPS',
      value: `${this.averageGpsRate}%`,
      detail: 'Localisation exploitable',
      icon: 'ri-map-pin-line',
      color: 'warning',
    },
  ];

  readonly prefectureLoadChart: ApexOptions = {
    series: [
      {
        name: 'Élèves',
        data: this.prefectures.map((item) => item.students),
      },
      {
        name: 'Enseignants',
        data: this.prefectures.map((item) => item.teachers),
      },
    ],
    chart: { type: 'bar', height: 330, toolbar: { show: false } },
    colors: ['var(--primary-color)', '#23b7e5'],
    dataLabels: { enabled: false },
    grid: { borderColor: 'var(--default-border)' },
    legend: { show: true, position: 'top' },
    plotOptions: {
      bar: { borderRadius: 4, columnWidth: '42%' },
    },
    xaxis: { categories: this.prefectures.map((item) => item.name) },
    yaxis: {
      labels: {
        formatter: (value) => `${Math.round(value / 1000)}k`,
      },
    },
  };

  readonly gpsCoverageChart: ApexOptions = {
    series: this.prefectures.map((item) => item.gpsRate),
    chart: { type: 'radialBar', height: 330 },
    colors: ['#26bf94', '#23b7e5', '#f5b849', '#e6533c'],
    labels: this.prefectures.map((item) => item.name),
    legend: { show: true, position: 'bottom' },
    plotOptions: {
      radialBar: {
        dataLabels: {
          total: {
            show: true,
            label: 'GPS moyen',
            formatter: () => `${this.averageGpsRate}%`,
          },
        },
      },
    },
  };

  readonly schoolDistributionChart: ApexOptions = {
    series: this.prefectures.map((item) => item.schools),
    chart: { type: 'donut', height: 290 },
    colors: ['var(--primary-color)', '#26bf94', '#23b7e5', '#f5b849'],
    dataLabels: { enabled: false },
    labels: this.prefectures.map((item) => item.name),
    legend: { show: true, position: 'bottom' },
    plotOptions: {
      pie: {
        donut: { size: '70%' },
      },
    },
  };

  ngAfterViewInit() {
    this.renderMap();
  }

  ngOnDestroy() {
    this.map?.remove();
  }

  get prefectures() {
    return this.territories.filter((item) => item.type === 'prefecture');
  }

  get subPrefectures() {
    return this.territories.filter((item) => item.type === 'sub-prefecture');
  }

  get totalSchools() {
    return this.prefectures.reduce((sum, item) => sum + item.schools, 0);
  }

  get totalStudents() {
    return this.prefectures.reduce((sum, item) => sum + item.students, 0);
  }

  get averageGpsRate() {
    const total = this.prefectures.reduce((sum, item) => sum + item.gpsRate, 0);
    return Math.round(total / this.prefectures.length);
  }

  get priorityTerritories() {
    return this.territories
      .filter((item) => item.priority !== 'low')
      .sort((left, right) => right.students - left.students);
  }

  selectTerritory(territory: TerritoryLocation) {
    this.selectedTerritory = territory;
    const marker = this.markerByTerritoryId.get(territory.id);
    if (marker && this.map) {
      this.map.setView([territory.latitude, territory.longitude], Math.max(this.map.getZoom(), 8), {
        animate: true,
      });
      marker.openPopup();
    }
  }

  formatNumber(value: number) {
    return value.toLocaleString('fr-FR');
  }

  statusLabel(status: ValidationStatus) {
    const labels: Record<ValidationStatus, string> = {
      DRAFT: 'Brouillon',
      SUBMITTED: 'Soumis',
      APPROVED: 'Approuvé',
      REJECTED: 'Rejeté',
    };

    return labels[status];
  }

  statusClass(status: ValidationStatus) {
    const classes: Record<ValidationStatus, string> = {
      DRAFT: 'bg-warning-transparent text-warning',
      SUBMITTED: 'bg-info-transparent text-info',
      APPROVED: 'bg-success-transparent text-success',
      REJECTED: 'bg-danger-transparent text-danger',
    };

    return classes[status];
  }

  priorityClass(priority: TerritoryLocation['priority']) {
    const classes: Record<TerritoryLocation['priority'], string> = {
      low: 'bg-success-transparent text-success',
      medium: 'bg-warning-transparent text-warning',
      high: 'bg-danger-transparent text-danger',
    };

    return classes[priority];
  }

  priorityLabel(priority: TerritoryLocation['priority']) {
    const labels: Record<TerritoryLocation['priority'], string> = {
      low: 'Stable',
      medium: 'À suivre',
      high: 'Prioritaire',
    };

    return labels[priority];
  }

  private renderMap() {
    this.map = L.map('dashboard-territory-map', {
      scrollWheelZoom: false,
      zoomControl: true,
    }).setView([9.9456, -9.6966], 6);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18,
      attribution: '© OpenStreetMap',
    }).addTo(this.map);

    this.markerLayer.addTo(this.map);
    this.addTerritoryMarkers();
    setTimeout(() => this.map?.invalidateSize(), 0);
  }

  private addTerritoryMarkers() {
    if (!this.map) {
      return;
    }

    this.markerLayer.clearLayers();
    this.markerByTerritoryId.clear();

    this.territories.forEach((territory) => {
      const marker = L.circleMarker([territory.latitude, territory.longitude], {
        radius: territory.type === 'prefecture' ? 15 : 10,
        color: this.markerColor(territory.priority),
        fillColor: this.markerColor(territory.priority),
        fillOpacity: territory.type === 'prefecture' ? 0.75 : 0.55,
        opacity: 0.95,
        weight: 2,
      })
        .bindPopup(this.popupContent(territory))
        .on('click', () => {
          this.selectedTerritory = territory;
          this.cdr.markForCheck();
        });

      marker.addTo(this.markerLayer);
      this.markerByTerritoryId.set(territory.id, marker);
    });

    const bounds = L.latLngBounds(this.territories.map((item) => [item.latitude, item.longitude]));
    if (bounds.isValid()) {
      this.map.fitBounds(bounds.pad(0.2), { maxZoom: 8 });
    }
  }

  private markerColor(priority: TerritoryLocation['priority']) {
    const colors: Record<TerritoryLocation['priority'], string> = {
      low: '#26bf94',
      medium: '#f5b849',
      high: '#e6533c',
    };

    return colors[priority];
  }

  private popupContent(territory: TerritoryLocation) {
    return `
      <strong>${territory.name}</strong><br>
      <span>${territory.region} · ${territory.code}</span><br>
      <span>${this.formatNumber(territory.schools)} écoles · ${this.formatNumber(territory.students)} élèves</span><br>
      <span>GPS ${territory.gpsRate}%</span>
    `;
  }
}
