import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { AuthService, SCHOOL_MANAGEMENT_ROLES } from '../../../shared/services/auth.service';
import { CensusApiService } from '../shared/census-api.service';
import { SchoolAdminService, SchoolPayload } from '../shared/school-admin.service';
import { Region, School } from '../shared/school-census.models';
import { ExportColumn, downloadCsv, downloadExcel, printTable } from '../shared/export-utils';

@Component({
  selector: 'app-schools',
  imports: [CommonModule, FormsModule, ReactiveFormsModule],
  templateUrl: './schools.html',
  styleUrl: './schools.scss',
})
export class Schools {
  private auth = inject(AuthService);
  private censusApi = inject(CensusApiService);
  private schoolApi = inject(SchoolAdminService);
  private formBuilder = inject(FormBuilder);

  schools: School[] = [];
  regions: Region[] = [];
  selectedSchool: School | null = null;
  editingId = '';
  loading = false;
  saving = false;
  error = '';
  searchTerm = '';
  selectedRegionId = '';
  selectedType = '';
  selectedGpsStatus = '';

  private schoolExportColumns: ExportColumn<School>[] = [
    { header: 'Code', value: (school) => school.code },
    { header: 'École', value: (school) => school.name },
    { header: 'Région', value: (school) => school.region?.name },
    { header: 'Préfecture', value: (school) => school.prefecture },
    { header: 'Commune', value: (school) => school.commune },
    { header: 'Type', value: (school) => school.type },
    { header: 'Téléphone', value: (school) => school.phone },
    { header: 'Adresse', value: (school) => school.address },
    { header: 'Latitude', value: (school) => school.latitude },
    { header: 'Longitude', value: (school) => school.longitude },
    { header: 'Classes', value: (school) => school.counts?.classes ?? 0 },
    { header: 'Élèves', value: (school) => school.counts?.students ?? 0 },
    { header: 'Enseignants', value: (school) => school.counts?.teachers ?? 0 },
  ];

  form = this.formBuilder.group({
    name: ['', [Validators.required, Validators.minLength(2)]],
    code: ['', [Validators.required, Validators.minLength(2)]],
    regionId: ['', Validators.required],
    prefecture: [''],
    commune: [''],
    type: [''],
    address: [''],
    phone: [''],
    latitude: [''],
    longitude: [''],
  });

  get canManageSchools() {
    return this.auth.hasAnyRole(SCHOOL_MANAGEMENT_ROLES);
  }

  get schoolTypes() {
    return Array.from(new Set(this.schools.map((school) => school.type).filter(Boolean) as string[])).sort();
  }

  get filteredSchools() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.schools.filter((school) => {
      const matchesRegion = !this.selectedRegionId || school.regionId === this.selectedRegionId;
      const matchesType = !this.selectedType || school.type === this.selectedType;
      const matchesGps =
        !this.selectedGpsStatus ||
        (this.selectedGpsStatus === 'with-gps' && this.hasCoordinates(school)) ||
        (this.selectedGpsStatus === 'missing-gps' && !this.hasCoordinates(school));
      const searchable = this.normalizeSearch(
        [
          school.name,
          school.code,
          school.region?.name,
          school.prefecture,
          school.commune,
          school.type,
          school.address,
          school.phone,
        ].join(' '),
      );

      return matchesRegion && matchesType && matchesGps && (!search || searchable.includes(search));
    });
  }

  get schoolTotals() {
    const rows = this.filteredSchools;

    return {
      schools: rows.length,
      classes: rows.reduce((sum, school) => sum + (school.counts?.classes ?? 0), 0),
      students: rows.reduce((sum, school) => sum + (school.counts?.students ?? 0), 0),
      teachers: rows.reduce((sum, school) => sum + (school.counts?.teachers ?? 0), 0),
      geolocated: rows.filter((school) => this.hasCoordinates(school)).length,
    };
  }

  ngOnInit() {
    this.loadMetadata();
    this.loadSchools();
  }

  loadMetadata() {
    this.censusApi.metadata().subscribe({
      next: (metadata) => {
        this.regions = metadata.regions;
        const firstRegionId = this.regions[0]?.id;
        if (firstRegionId && !this.form.controls.regionId.value) {
          this.form.patchValue({ regionId: firstRegionId });
        }
      },
      error: () => {
        this.error = 'Impossible de charger les régions.';
      },
    });
  }

  loadSchools() {
    this.loading = true;
    this.error = '';

    this.schoolApi.listSchools().subscribe({
      next: (schools) => {
        this.schools = schools;
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger les écoles.';
        this.loading = false;
      },
    });
  }

  saveSchool() {
    if (!this.canManageSchools || this.form.invalid || this.saving) {
      this.form.markAllAsTouched();
      return;
    }

    this.saving = true;
    this.error = '';
    const payload = this.normalizePayload(this.form.getRawValue());
    const request = this.editingId
      ? this.schoolApi.updateSchool(this.editingId, payload)
      : this.schoolApi.createSchool(payload);

    request.subscribe({
      next: (school) => {
        this.schools = this.editingId
          ? this.schools.map((item) => (item.id === school.id ? school : item))
          : [school, ...this.schools];
        this.resetForm();
        this.saving = false;
      },
      error: () => {
        this.error = 'Enregistrement impossible. Vérifiez le code école et les droits utilisateur.';
        this.saving = false;
      },
    });
  }

  editSchool(school: School) {
    if (!this.canManageSchools) {
      return;
    }

    this.editingId = school.id;
    this.selectedSchool = school;
    this.form.patchValue({
      name: school.name,
      code: school.code,
      regionId: school.regionId,
      prefecture: school.prefecture ?? '',
      commune: school.commune ?? '',
      type: school.type ?? '',
      address: school.address ?? '',
      phone: school.phone ?? '',
      latitude: school.latitude?.toString() ?? '',
      longitude: school.longitude?.toString() ?? '',
    });
  }

  deleteSchool(school: School) {
    if (!this.canManageSchools || !window.confirm(`Supprimer l'école ${school.name} ?`)) {
      return;
    }

    this.schoolApi.deleteSchool(school.id).subscribe({
      next: () => {
        this.schools = this.schools.filter((item) => item.id !== school.id);
        if (this.editingId === school.id) {
          this.resetForm();
        }
      },
      error: () => {
        this.error = 'Suppression impossible : cette école est déjà utilisée.';
      },
    });
  }

  resetForm() {
    const regionId = this.regions[0]?.id ?? '';
    this.editingId = '';
    this.selectedSchool = null;
    this.form.reset({
      name: '',
      code: '',
      regionId,
      prefecture: '',
      commune: '',
      type: '',
      address: '',
      phone: '',
      latitude: '',
      longitude: '',
    });
  }

  resetFilters() {
    this.searchTerm = '';
    this.selectedRegionId = '';
    this.selectedType = '';
    this.selectedGpsStatus = '';
  }

  exportCsv() {
    downloadCsv('ecoles.csv', this.filteredSchools, this.schoolExportColumns);
  }

  exportExcel() {
    downloadExcel('ecoles.xls', this.filteredSchools, this.schoolExportColumns);
  }

  printReport() {
    printTable('Liste des écoles', this.filteredSchools, this.schoolExportColumns);
  }

  hasCoordinates(school: School) {
    return (
      school.latitude !== null &&
      school.latitude !== undefined &&
      school.longitude !== null &&
      school.longitude !== undefined
    );
  }

  private normalizePayload(value: ReturnType<typeof this.form.getRawValue>): SchoolPayload {
    return {
      name: value.name ?? '',
      code: value.code ?? '',
      regionId: value.regionId ?? '',
      prefecture: this.optionalText(value.prefecture),
      commune: this.optionalText(value.commune),
      type: this.optionalText(value.type),
      address: this.optionalText(value.address),
      phone: this.optionalText(value.phone),
      latitude: this.toNumber(value.latitude),
      longitude: this.toNumber(value.longitude),
    };
  }

  private optionalText(value?: string | null) {
    const trimmed = value?.trim();
    return trimmed || null;
  }

  private toNumber(value?: string | null) {
    if (value === null || value === undefined || value === '') {
      return null;
    }
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  private normalizeSearch(value?: string | null) {
    return (value ?? '')
      .toLocaleLowerCase('fr-FR')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '');
  }
}
