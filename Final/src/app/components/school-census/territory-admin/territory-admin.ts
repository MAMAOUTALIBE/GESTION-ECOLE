import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import {
  AuthService,
  NATIONAL_SCOPE_ROLES,
  PREFECTURE_SCOPE_ROLES,
  REGIONAL_SCOPE_ROLES,
} from '../../../shared/services/auth.service';
import { CensusApiService } from '../shared/census-api.service';
import { Prefecture, Region, SubPrefecture } from '../shared/school-census.models';
import { TerritoryApiService } from '../shared/territory-api.service';

@Component({
  selector: 'app-territory-admin',
  imports: [CommonModule, FormsModule, ReactiveFormsModule],
  templateUrl: './territory-admin.html',
  styleUrl: './territory-admin.scss',
})
export class TerritoryAdmin {
  private auth = inject(AuthService);
  private censusApi = inject(CensusApiService);
  private territoryApi = inject(TerritoryApiService);
  private formBuilder = inject(FormBuilder);

  regions: Region[] = [];
  prefectures: Prefecture[] = [];
  subPrefectures: SubPrefecture[] = [];
  loading = false;
  savingPrefecture = false;
  savingSubPrefecture = false;
  error = '';
  success = '';
  searchTerm = '';
  selectedRegionId = '';
  selectedPrefectureId = '';
  selectedStatus = '';

  prefectureForm = this.formBuilder.group({
    name: ['', [Validators.required, Validators.minLength(2)]],
    code: ['', [Validators.required, Validators.minLength(2)]],
    regionId: [''],
  });

  subPrefectureForm = this.formBuilder.group({
    name: ['', [Validators.required, Validators.minLength(2)]],
    code: ['', [Validators.required, Validators.minLength(2)]],
    prefectureId: ['', Validators.required],
  });

  get canCreatePrefecture() {
    return this.auth.hasAnyRole([...NATIONAL_SCOPE_ROLES, 'REGIONAL_ADMIN']);
  }

  get canCreateSubPrefecture() {
    return this.auth.hasAnyRole([...NATIONAL_SCOPE_ROLES, ...REGIONAL_SCOPE_ROLES, ...PREFECTURE_SCOPE_ROLES]);
  }

  get filteredPrefectures() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.prefectures.filter((prefecture) => {
      const matchesRegion = !this.selectedRegionId || prefecture.regionId === this.selectedRegionId;
      const matchesStatus = !this.selectedStatus || prefecture.status === this.selectedStatus;
      const searchable = this.normalizeSearch([prefecture.name, prefecture.code, prefecture.region?.name].join(' '));

      return matchesRegion && matchesStatus && (!search || searchable.includes(search));
    });
  }

  get filteredSubPrefectures() {
    const search = this.normalizeSearch(this.searchTerm);

    return this.subPrefectures.filter((subPrefecture) => {
      const prefecture = subPrefecture.prefecture;
      const matchesRegion = !this.selectedRegionId || subPrefecture.regionId === this.selectedRegionId;
      const matchesPrefecture = !this.selectedPrefectureId || subPrefecture.prefectureId === this.selectedPrefectureId;
      const matchesStatus = !this.selectedStatus || subPrefecture.status === this.selectedStatus;
      const searchable = this.normalizeSearch(
        [subPrefecture.name, subPrefecture.code, prefecture?.name, prefecture?.region?.name].join(' '),
      );

      return matchesRegion && matchesPrefecture && matchesStatus && (!search || searchable.includes(search));
    });
  }

  get territoryTotals() {
    const approvedPrefectures = this.prefectures.filter((item) => item.status === 'APPROVED').length;
    const pendingPrefectures = this.prefectures.filter((item) => item.status === 'SUBMITTED').length;
    const approvedSubPrefectures = this.subPrefectures.filter((item) => item.status === 'APPROVED').length;
    const pendingSubPrefectures = this.subPrefectures.filter((item) => item.status === 'SUBMITTED').length;

    return {
      regions: this.regions.length,
      prefectures: this.prefectures.length,
      subPrefectures: this.subPrefectures.length,
      approved: approvedPrefectures + approvedSubPrefectures,
      pending: pendingPrefectures + pendingSubPrefectures,
    };
  }

  ngOnInit() {
    this.loadAll();
  }

  loadAll() {
    this.loading = true;
    this.error = '';

    this.censusApi.metadata().subscribe({
      next: (metadata) => {
        this.regions = metadata.regions;
        this.prefectures = metadata.prefectures;
        this.subPrefectures = metadata.subPrefectures;
        this.patchDefaultForms();
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger la hiérarchie territoriale.';
        this.loading = false;
      },
    });

    this.territoryApi.prefectures().subscribe({
      next: (prefectures) => {
        this.prefectures = prefectures;
        this.patchDefaultForms();
      },
    });

    this.territoryApi.subPrefectures().subscribe({
      next: (subPrefectures) => {
        this.subPrefectures = subPrefectures;
      },
    });
  }

  createPrefecture() {
    if (!this.canCreatePrefecture || this.prefectureForm.invalid || this.savingPrefecture) {
      this.prefectureForm.markAllAsTouched();
      return;
    }

    this.savingPrefecture = true;
    this.error = '';
    this.success = '';

    const value = this.prefectureForm.getRawValue();
    this.territoryApi
      .createPrefecture({
        name: value.name?.trim() ?? '',
        code: value.code?.trim().toUpperCase() ?? '',
        regionId: value.regionId || undefined,
      })
      .subscribe({
        next: () => {
          this.success = 'Préfecture enregistrée. Une validation peut être requise selon votre rôle.';
          this.savingPrefecture = false;
          this.prefectureForm.reset({ name: '', code: '', regionId: this.defaultRegionId() });
          this.loadAll();
        },
        error: () => {
          this.error = 'Création de la préfecture impossible. Vérifiez le code et vos droits.';
          this.savingPrefecture = false;
        },
      });
  }

  createSubPrefecture() {
    if (!this.canCreateSubPrefecture || this.subPrefectureForm.invalid || this.savingSubPrefecture) {
      this.subPrefectureForm.markAllAsTouched();
      return;
    }

    this.savingSubPrefecture = true;
    this.error = '';
    this.success = '';

    const value = this.subPrefectureForm.getRawValue();
    this.territoryApi
      .createSubPrefecture({
        name: value.name?.trim() ?? '',
        code: value.code?.trim().toUpperCase() ?? '',
        prefectureId: value.prefectureId ?? '',
      })
      .subscribe({
        next: () => {
          this.success = 'Sous-préfecture enregistrée. Une validation peut être requise selon votre rôle.';
          this.savingSubPrefecture = false;
          this.subPrefectureForm.reset({ name: '', code: '', prefectureId: this.defaultPrefectureId() });
          this.loadAll();
        },
        error: () => {
          this.error = 'Création de la sous-préfecture impossible. Vérifiez le code et vos droits.';
          this.savingSubPrefecture = false;
        },
      });
  }

  statusLabel(status?: string) {
    const labels: Record<string, string> = {
      APPROVED: 'Validé',
      SUBMITTED: 'À valider',
      REJECTED: 'Rejeté',
      DRAFT: 'Brouillon',
    };
    return labels[status ?? ''] ?? 'Non défini';
  }

  statusClass(status?: string) {
    const classes: Record<string, string> = {
      APPROVED: 'bg-success-transparent',
      SUBMITTED: 'bg-warning-transparent',
      REJECTED: 'bg-danger-transparent',
      DRAFT: 'bg-secondary-transparent',
    };
    return classes[status ?? ''] ?? 'bg-light text-muted';
  }

  resetFilters() {
    this.searchTerm = '';
    this.selectedRegionId = '';
    this.selectedPrefectureId = '';
    this.selectedStatus = '';
  }

  private patchDefaultForms() {
    if (!this.prefectureForm.controls.regionId.value) {
      this.prefectureForm.patchValue({ regionId: this.defaultRegionId() });
    }
    if (!this.subPrefectureForm.controls.prefectureId.value) {
      this.subPrefectureForm.patchValue({ prefectureId: this.defaultPrefectureId() });
    }
  }

  private defaultRegionId() {
    return this.auth.currentUser?.region?.id ?? this.regions[0]?.id ?? '';
  }

  private defaultPrefectureId() {
    return this.auth.currentUser?.prefecture?.id ?? this.prefectures[0]?.id ?? '';
  }

  private normalizeSearch(value: string) {
    return value
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '');
  }
}
