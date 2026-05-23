import { CommonModule } from '@angular/common';
import { Component, DestroyRef, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { catchError, forkJoin, of } from 'rxjs';
import { CensusApiService } from '../shared/census-api.service';
import { downloadCsv, downloadExcel, ExportColumn, printTable } from '../shared/export-utils';
import { BusRouteRow, SchoolLifeApiService } from '../shared/schoollife-api.service';
import { CensusPerson, Region, School } from '../shared/school-census.models';

type TransportMode = 'bus' | 'walk' | 'bike' | 'boat';
type TransportStatus = 'active' | 'watch' | 'suspended';
type RiskLevel = 'low' | 'medium' | 'high';

interface TransportModeConfig {
  id: TransportMode;
  title: string;
  description: string;
  icon: string;
  color: string;
}

interface TransportRoute {
  id: string;
  routeName: string;
  origin: string;
  destination: string;
  schoolId: string;
  schoolName: string;
  regionId: string;
  region: string;
  mode: TransportMode;
  distanceKm: number;
  students: number;
  capacity: number;
  occupancyRate: number;
  driver: string;
  vehicle: string;
  incidents: number;
  risk: RiskLevel;
  status: TransportStatus;
  lastCheck: string;
}

@Component({
  selector: 'app-school-transport',
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './school-transport.html',
  styleUrl: './school-transport.scss',
})
export class SchoolTransport {
  private schoolLifeApi = inject(SchoolLifeApiService);
  private destroyRef = inject(DestroyRef);
  private censusApi = inject(CensusApiService);

  regions: Region[] = [];
  schools: School[] = [];
  students: CensusPerson[] = [];
  routes: TransportRoute[] = [];
  loading = false;
  error = '';
  searchTerm = '';
  selectedRegionId = '';
  selectedSchoolId = '';
  selectedMode = '';
  selectedRisk = '';
  selectedStatus = '';

  modes: TransportModeConfig[] = [
    {
      id: 'bus',
      title: 'Bus scolaire',
      description: 'Transport collectif organisé sur itinéraire régulier.',
      icon: 'ri-bus-2-line',
      color: 'primary',
    },
    {
      id: 'walk',
      title: 'Marche encadrée',
      description: 'Trajets à pied suivis par relais communautaire.',
      icon: 'ri-walk-line',
      color: 'success',
    },
    {
      id: 'bike',
      title: 'Vélo / moto',
      description: 'Mobilité légère pour zones périurbaines ou rurales.',
      icon: 'ri-riding-line',
      color: 'warning',
    },
    {
      id: 'boat',
      title: 'Pirogue',
      description: 'Traversées fluviales ou accès insulaire sécurisé.',
      icon: 'ri-ship-line',
      color: 'info',
    },
  ];

  private drivers = [
    'Mamadou Diallo',
    'Ibrahima Camara',
    'Aissatou Barry',
    'Sékou Condé',
    'Kadiatou Bah',
    'Aboubacar Sylla',
  ];

  private exportColumns: ExportColumn<TransportRoute>[] = [
    { header: 'Itinéraire', value: (route) => route.routeName },
    { header: 'Départ', value: (route) => route.origin },
    { header: 'Arrivée', value: (route) => route.destination },
    { header: 'Établissement', value: (route) => route.schoolName },
    { header: 'Région', value: (route) => route.region },
    { header: 'Mode', value: (route) => this.modeLabel(route.mode) },
    { header: 'Distance km', value: (route) => route.distanceKm },
    { header: 'Élèves', value: (route) => route.students },
    { header: 'Capacité', value: (route) => route.capacity },
    { header: 'Occupation', value: (route) => `${route.occupancyRate}%` },
    { header: 'Responsable', value: (route) => route.driver },
    { header: 'Véhicule', value: (route) => route.vehicle },
    { header: 'Incidents', value: (route) => route.incidents },
    { header: 'Risque', value: (route) => this.riskLabel(route.risk) },
    { header: 'Statut', value: (route) => this.statusLabel(route.status) },
    { header: 'Dernier contrôle', value: (route) => route.lastCheck },
  ];

  ngOnInit() {
    this.load();
  }

  get filteredSchools() {
    return this.schools
      .filter((school) => !this.selectedRegionId || school.regionId === this.selectedRegionId)
      .sort((left, right) => left.name.localeCompare(right.name, 'fr-FR'));
  }

  get filteredRoutes() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.routes.filter((route) => {
      const matchesRegion = !this.selectedRegionId || route.regionId === this.selectedRegionId;
      const matchesSchool = !this.selectedSchoolId || route.schoolId === this.selectedSchoolId;
      const matchesMode = !this.selectedMode || route.mode === this.selectedMode;
      const matchesRisk = !this.selectedRisk || route.risk === this.selectedRisk;
      const matchesStatus = !this.selectedStatus || route.status === this.selectedStatus;
      const searchable = this.normalizeSearch(
        [
          route.routeName,
          route.origin,
          route.destination,
          route.schoolName,
          route.region,
          route.driver,
          route.vehicle,
          this.modeLabel(route.mode),
        ].join(' '),
      );

      return (
        matchesRegion &&
        matchesSchool &&
        matchesMode &&
        matchesRisk &&
        matchesStatus &&
        (!search || searchable.includes(search))
      );
    });
  }

  get totals() {
    const routes = this.filteredRoutes;
    const students = routes.reduce((sum, route) => sum + route.students, 0);
    const capacity = routes.reduce((sum, route) => sum + route.capacity, 0);

    return {
      routes: routes.length,
      students,
      capacity,
      occupancyRate: capacity ? Math.round((students / capacity) * 100) : 0,
      incidents: routes.reduce((sum, route) => sum + route.incidents, 0),
      highRisk: routes.filter((route) => route.risk === 'high').length,
      suspended: routes.filter((route) => route.status === 'suspended').length,
      distanceKm: Math.round(routes.reduce((sum, route) => sum + route.distanceKm, 0) * 10) / 10,
    };
  }

  get modeSummaries() {
    return this.modes.map((mode) => {
      const routes = this.filteredRoutes.filter((route) => route.mode === mode.id);

      return {
        ...mode,
        routes: routes.length,
        students: routes.reduce((sum, route) => sum + route.students, 0),
        incidents: routes.reduce((sum, route) => sum + route.incidents, 0),
      };
    });
  }

  get riskSummaries() {
    return [
      {
        risk: 'high' as RiskLevel,
        label: 'Risque élevé',
        count: this.filteredRoutes.filter((route) => route.risk === 'high').length,
        className: 'bg-danger-transparent text-danger',
      },
      {
        risk: 'medium' as RiskLevel,
        label: 'Risque moyen',
        count: this.filteredRoutes.filter((route) => route.risk === 'medium').length,
        className: 'bg-warning-transparent text-warning',
      },
      {
        risk: 'low' as RiskLevel,
        label: 'Risque faible',
        count: this.filteredRoutes.filter((route) => route.risk === 'low').length,
        className: 'bg-success-transparent text-success',
      },
    ];
  }

  load() {
    this.loading = true;
    this.error = '';

    forkJoin({
      metadata: this.censusApi.metadata(),
      students: this.censusApi.students(),
      busRoutes: this.schoolLifeApi.listBusRoutes({ limit: 500 }),
    })
      .pipe(catchError(() => of(null)), takeUntilDestroyed(this.destroyRef))
      .subscribe((result) => {
        if (!result) {
          this.regions = this.fallbackRegions();
          this.schools = this.fallbackSchools();
          this.students = this.fallbackStudents();
          this.routes = this.buildRoutes();
          this.error = 'Données backend indisponibles, affichage du transport scolaire de démonstration.';
          this.loading = false;
          return;
        }
        this.regions = result.metadata.regions.length ? result.metadata.regions : this.fallbackRegions();
        this.schools = result.metadata.schools.length ? result.metadata.schools : this.fallbackSchools();
        this.students = result.students.length ? result.students : this.fallbackStudents();
        // Vraies lignes de bus (Phase 13) + circuits synthétisés pour walk/bike/boat
        this.routes = [
          ...this.busRoutesToTransportRows(result.busRoutes),
          ...this.buildRoutes().filter((r) => r.mode !== 'bus'),
        ];
        this.loading = false;
      });
  }

  private busRoutesToTransportRows(busRoutes: BusRouteRow[]): TransportRoute[] {
    const schoolsById = new Map(this.schools.map((s) => [s.id, s]));
    return busRoutes.map((br) => {
      const school = schoolsById.get(br.schoolId);
      const students = br.studentsAssigned;
      const occupancyRate = br.capacity ? Math.round((students / br.capacity) * 100) : 0;
      const incidents = br.status === 'MAINTENANCE' ? 1 : 0;
      const risk: RiskLevel =
        br.status === 'MAINTENANCE' ? 'high'
          : occupancyRate > 95 ? 'medium' : 'low';
      const status: TransportStatus =
        br.status === 'INACTIVE' ? 'suspended'
          : br.status === 'MAINTENANCE' ? 'watch' : 'active';
      const lastCheck = new Date(br.updatedAt).toLocaleDateString('fr-FR');
      return {
        id: br.id,
        routeName: br.name,
        origin: school?.commune ?? 'Centre',
        destination: school?.name ?? '—',
        schoolId: br.schoolId,
        schoolName: br.school?.name ?? school?.name ?? '—',
        regionId: school?.regionId ?? '',
        region: school?.region?.name ?? 'Région N/A',
        mode: 'bus',
        distanceKm: 0,
        students,
        capacity: br.capacity,
        occupancyRate,
        driver: br.driverName ?? '—',
        vehicle: br.plate ? `Bus ${br.plate}` : 'Bus scolaire',
        incidents,
        risk,
        status,
        lastCheck,
      };
    });
  }

  onRegionChange() {
    this.selectedSchoolId = '';
  }

  resetFilters() {
    this.searchTerm = '';
    this.selectedRegionId = '';
    this.selectedSchoolId = '';
    this.selectedMode = '';
    this.selectedRisk = '';
    this.selectedStatus = '';
  }

  exportRows(format: 'csv' | 'excel' | 'print') {
    if (format === 'csv') {
      downloadCsv('transport-scolaire.csv', this.filteredRoutes, this.exportColumns);
      return;
    }

    if (format === 'excel') {
      downloadExcel('transport-scolaire.xls', this.filteredRoutes, this.exportColumns);
      return;
    }

    printTable('Transport scolaire', this.filteredRoutes, this.exportColumns);
  }

  modeLabel(mode: TransportMode) {
    return this.modes.find((item) => item.id === mode)?.title ?? mode;
  }

  statusLabel(status: TransportStatus) {
    const labels: Record<TransportStatus, string> = {
      active: 'Actif',
      watch: 'À surveiller',
      suspended: 'Suspendu',
    };

    return labels[status];
  }

  statusClass(status: TransportStatus) {
    const classes: Record<TransportStatus, string> = {
      active: 'bg-success-transparent text-success',
      watch: 'bg-warning-transparent text-warning',
      suspended: 'bg-danger-transparent text-danger',
    };

    return classes[status];
  }

  riskLabel(risk: RiskLevel) {
    const labels: Record<RiskLevel, string> = {
      low: 'Faible',
      medium: 'Moyen',
      high: 'Élevé',
    };

    return labels[risk];
  }

  riskClass(risk: RiskLevel) {
    const classes: Record<RiskLevel, string> = {
      low: 'bg-success-transparent text-success',
      medium: 'bg-warning-transparent text-warning',
      high: 'bg-danger-transparent text-danger',
    };

    return classes[risk];
  }

  toneClass(color: string) {
    return `bg-${color}-transparent text-${color}`;
  }

  formatNumber(value: number) {
    return value.toLocaleString('fr-FR');
  }

  private buildRoutes(): TransportRoute[] {
    const origins = ['Centre-ville', 'Quartier périphérique', 'Village voisin', 'Marché central', 'Rive communautaire'];

    return this.schools.slice(0, 24).flatMap((school, schoolIndex) => {
      const routesPerSchool = schoolIndex % 3 === 0 ? 3 : 2;

      return Array.from({ length: routesPerSchool }, (_, routeIndex) => {
        const index = schoolIndex * 3 + routeIndex;
        const mode = this.modes[index % this.modes.length].id;
        const students = this.countStudentsForSchool(school.id) || 18 + ((index * 7) % 42);
        const capacity = mode === 'bus' ? 48 : mode === 'boat' ? 26 : mode === 'bike' ? 18 : 35;
        const occupancyRate = Math.round((students / capacity) * 100);
        const distanceKm = Math.round((1.4 + (index % 9) * 1.7 + routeIndex * 0.8) * 10) / 10;
        const incidents = index % 8 === 0 ? 3 : index % 5 === 0 ? 1 : 0;
        const risk: RiskLevel = incidents >= 3 || distanceKm > 12 ? 'high' : incidents || occupancyRate > 95 ? 'medium' : 'low';
        const status: TransportStatus = risk === 'high' ? 'watch' : index % 13 === 0 ? 'suspended' : 'active';

        return {
          id: `${school.id}-transport-${routeIndex + 1}`,
          routeName: `Circuit ${school.code}-${routeIndex + 1}`,
          origin: origins[index % origins.length],
          destination: school.name,
          schoolId: school.id,
          schoolName: school.name,
          regionId: school.regionId,
          region: school.region?.name ?? this.regions.find((region) => region.id === school.regionId)?.name ?? 'Région',
          mode,
          distanceKm,
          students,
          capacity,
          occupancyRate,
          driver: this.drivers[index % this.drivers.length],
          vehicle: mode === 'bus' ? `Bus GN-${240 + index}` : mode === 'boat' ? `Pirogue ${index + 1}` : 'Relais communautaire',
          incidents,
          risk,
          status,
          lastCheck: `${String(2 + (index % 24)).padStart(2, '0')}/05/2026`,
        };
      });
    });
  }

  private countStudentsForSchool(schoolId: string) {
    return this.students.filter((student) => student.school?.id === schoolId).length;
  }

  private fallbackRegions(): Region[] {
    return [
      { id: 'rg-conakry', code: 'RG-CON', name: 'Conakry' },
      { id: 'rg-kindia', code: 'RG-KIN', name: 'Kindia' },
      { id: 'rg-labe', code: 'RG-LAB', name: 'Labé' },
    ];
  }

  private fallbackSchools(): School[] {
    const regions = this.regions.length ? this.regions : this.fallbackRegions();
    const names = ['École Primaire Almamya', 'Collège 2 Octobre', 'Lycée Donka', 'École Application Kindia'];

    return names.map((name, index) => {
      const region = regions[index % regions.length];

      return {
        id: `school-transport-${index + 1}`,
        name,
        code: `ECO-${String(index + 1).padStart(3, '0')}`,
        regionId: region.id,
        region,
      };
    });
  }

  private fallbackStudents(): CensusPerson[] {
    return this.fallbackSchools().flatMap((school, schoolIndex) =>
      Array.from({ length: 14 + schoolIndex * 6 }, (_, index) => ({
        id: `${school.id}-student-${index + 1}`,
        type: 'STUDENT',
        uniqueCode: `ELV-${schoolIndex + 1}${String(index + 1).padStart(3, '0')}`,
        firstName: 'Élève',
        lastName: `${index + 1}`,
        fullName: `Élève ${index + 1}`,
        gender: index % 2 ? 'MALE' : 'FEMALE',
        school,
        createdAt: '2026-05-02T00:00:00.000Z',
      })),
    );
  }

  private normalizeSearch(value?: string | null) {
    return (value ?? '')
      .toLocaleLowerCase('fr-FR')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '');
  }
}
