import { CommonModule } from '@angular/common';
import { Component, DestroyRef, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { catchError, of } from 'rxjs';
import {
  AdminApiService,
  ImportTemplate as ApiImportTemplate,
} from '../shared/admin-api.service';
import { downloadCsv, ExportColumn } from '../shared/export-utils';

type ImportStatus = 'success' | 'warning' | 'danger';
type SyncStatus = 'completed' | 'running' | 'queued' | 'failed';

interface ImportTemplate {
  id: string;
  title: string;
  description: string;
  icon: string;
  color: string;
  headers: string[];
  requiredHeaders: string[];
  sampleRows: Array<Record<string, string>>;
}

interface ImportPreviewRow {
  rowNumber: number;
  values: Record<string, string>;
  issues: string[];
  status: ImportStatus;
}

interface SyncItem {
  id: string;
  title: string;
  source: string;
  records: number;
  status: SyncStatus;
  lastRun: string;
}

@Component({
  selector: 'app-data-imports',
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './data-imports.html',
  styleUrl: './data-imports.scss',
})
export class DataImports {
  private adminApi = inject(AdminApiService);
  private destroyRef = inject(DestroyRef);

  apiTemplates: ApiImportTemplate[] = [];
  apiLoaded = false;

  ngOnInit() {
    // Charge les vrais templates exposés par /api/imports/templates ;
    // les fixtures `templates` ci-dessous restent en fallback.
    this.adminApi.listImportTemplates()
      .pipe(catchError(() => of([] as ApiImportTemplate[])), takeUntilDestroyed(this.destroyRef))
      .subscribe((tpls) => {
        this.apiTemplates = tpls;
        this.apiLoaded = true;
        // Si l'API renvoie quelque chose, on aligne les templates sur ses colonnes
        if (tpls.length) {
          this.templates = this.templates.map((t) => {
            const real = tpls.find((r) => r.kind === t.id);
            if (!real) return t;
            return {
              ...t,
              title: real.label,
              headers: real.columns,
              requiredHeaders: real.columns.slice(0, 4),
            };
          });
        }
      });
  }

  templates: ImportTemplate[] = [
    {
      id: 'students',
      title: 'Élèves',
      description: 'Matricules, identité, école, classe et responsable.',
      icon: 'ri-graduation-cap-line',
      color: 'primary',
      headers: ['Matricule', 'Nom', 'Prénom', 'Genre', 'Date naissance', 'École', 'Classe', 'Parent', 'Téléphone parent'],
      requiredHeaders: ['Matricule', 'Nom', 'Prénom', 'Genre', 'École'],
      sampleRows: [
        {
          Matricule: 'ELV-0001',
          Nom: 'Camara',
          Prénom: 'Aminata',
          Genre: 'FEMALE',
          'Date naissance': '2012-04-18',
          École: 'École Primaire Almamya',
          Classe: 'CM2 A',
          Parent: 'Mamadou Camara',
          'Téléphone parent': '+224620000001',
        },
      ],
    },
    {
      id: 'teachers',
      title: 'Enseignants',
      description: 'Affectations, matières, diplômes et contacts.',
      icon: 'ri-briefcase-4-line',
      color: 'info',
      headers: ['Matricule', 'Nom', 'Prénom', 'Genre', 'Téléphone', 'Diplôme', 'Matière', 'École', 'Classe'],
      requiredHeaders: ['Matricule', 'Nom', 'Prénom', 'Genre', 'École'],
      sampleRows: [
        {
          Matricule: 'ENS-0001',
          Nom: 'Diallo',
          Prénom: 'Ibrahima',
          Genre: 'MALE',
          Téléphone: '+224620000002',
          Diplôme: 'Licence',
          Matière: 'Mathématiques',
          École: 'Collège 2 Octobre',
          Classe: '9ème A',
        },
      ],
    },
    {
      id: 'schools',
      title: 'Établissements',
      description: 'Codes, territoires, coordonnées GPS et contacts.',
      icon: 'ri-school-line',
      color: 'secondary',
      headers: ['Code', 'Nom', 'Région', 'Préfecture', 'Commune', 'Type', 'Téléphone', 'Latitude', 'Longitude'],
      requiredHeaders: ['Code', 'Nom', 'Région'],
      sampleRows: [
        {
          Code: 'ECO-001',
          Nom: 'École Primaire Almamya',
          Région: 'Conakry',
          Préfecture: 'Kaloum',
          Commune: 'Almamya',
          Type: 'PUBLIC',
          Téléphone: '+224620000003',
          Latitude: '9.5092',
          Longitude: '-13.7122',
        },
      ],
    },
    {
      id: 'territories',
      title: 'Territoires',
      description: 'Régions, préfectures et sous-préfectures.',
      icon: 'ri-map-2-line',
      color: 'success',
      headers: ['Code région', 'Région', 'Code préfecture', 'Préfecture', 'Code sous-préfecture', 'Sous-préfecture'],
      requiredHeaders: ['Code région', 'Région', 'Code préfecture', 'Préfecture'],
      sampleRows: [
        {
          'Code région': 'RG-CON',
          Région: 'Conakry',
          'Code préfecture': 'P-KAL',
          Préfecture: 'Kaloum',
          'Code sous-préfecture': 'SP-ALM',
          'Sous-préfecture': 'Almamya',
        },
      ],
    },
  ];

  syncItems: SyncItem[] = [
    {
      id: 'territory',
      title: 'Référentiel territorial',
      source: 'Territory',
      records: 412,
      status: 'completed',
      lastRun: '02/05/2026 14:10',
    },
    {
      id: 'schools',
      title: 'Établissements et classes',
      source: 'Census',
      records: 1284,
      status: 'queued',
      lastRun: '01/05/2026 18:42',
    },
    {
      id: 'academics',
      title: 'Années, matières et bulletins',
      source: 'Academics',
      records: 96,
      status: 'completed',
      lastRun: '02/05/2026 09:25',
    },
  ];

  selectedTemplateId = 'students';
  selectedFileName = '';
  previewHeaders: string[] = [];
  previewRows: ImportPreviewRow[] = [];
  missingHeaders: string[] = [];
  unknownHeaders: string[] = [];
  loading = false;
  error = '';

  get selectedTemplate() {
    return this.templates.find((template) => template.id === this.selectedTemplateId) ?? this.templates[0];
  }

  get stats() {
    return {
      rows: this.previewRows.length,
      valid: this.previewRows.filter((row) => row.status === 'success').length,
      warnings: this.previewRows.filter((row) => row.status === 'warning').length + this.unknownHeaders.length,
      errors: this.previewRows.filter((row) => row.status === 'danger').length + this.missingHeaders.length,
    };
  }

  get canPrepareImport() {
    return this.previewRows.length > 0 && this.stats.errors === 0;
  }

  onTemplateChange() {
    this.clearPreview();
  }

  onFileSelected(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];

    if (!file) {
      return;
    }

    this.selectedFileName = file.name;
    this.loading = true;
    this.error = '';

    const reader = new FileReader();
    reader.onload = () => {
      this.parseFile(String(reader.result ?? ''));
      this.loading = false;
    };
    reader.onerror = () => {
      this.error = 'Lecture du fichier impossible.';
      this.loading = false;
    };
    reader.readAsText(file, 'utf-8');
  }

  downloadTemplate(template: ImportTemplate) {
    const columns: Array<ExportColumn<Record<string, string>>> = template.headers.map((header) => ({
      header,
      value: (row) => row[header] ?? '',
    }));

    downloadCsv(`modele-${template.id}.csv`, template.sampleRows, columns);
  }

  prepareImport() {
    if (!this.canPrepareImport) {
      return;
    }

    this.loading = true;
    setTimeout(() => {
      this.loading = false;
    }, 500);
  }

  runSync(item: SyncItem) {
    if (item.status === 'running') {
      return;
    }

    item.status = 'running';
    setTimeout(() => {
      item.status = 'completed';
      item.lastRun = new Date().toLocaleString('fr-FR', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    }, 700);
  }

  statusLabel(status: ImportStatus | SyncStatus) {
    const labels: Record<ImportStatus | SyncStatus, string> = {
      success: 'Valide',
      warning: 'À vérifier',
      danger: 'Erreur',
      completed: 'Synchronisé',
      running: 'En cours',
      queued: 'En attente',
      failed: 'Échec',
    };

    return labels[status];
  }

  statusClass(status: ImportStatus | SyncStatus) {
    const classes: Record<ImportStatus | SyncStatus, string> = {
      success: 'bg-success-transparent text-success',
      warning: 'bg-warning-transparent text-warning',
      danger: 'bg-danger-transparent text-danger',
      completed: 'bg-success-transparent text-success',
      running: 'bg-info-transparent text-info',
      queued: 'bg-warning-transparent text-warning',
      failed: 'bg-danger-transparent text-danger',
    };

    return classes[status];
  }

  formatNumber(value: number) {
    return value.toLocaleString('fr-FR');
  }

  private clearPreview() {
    this.selectedFileName = '';
    this.previewHeaders = [];
    this.previewRows = [];
    this.missingHeaders = [];
    this.unknownHeaders = [];
    this.error = '';
  }

  private parseFile(content: string) {
    const lines = content
      .replace(/^\uFEFF/, '')
      .replace(/\r/g, '')
      .split('\n')
      .filter((line) => line.trim().length);

    if (lines.length < 2) {
      this.error = 'Le fichier doit contenir une ligne d’en-tête et au moins une ligne de données.';
      this.previewHeaders = [];
      this.previewRows = [];
      return;
    }

    const delimiter = this.detectDelimiter(lines[0]);
    this.previewHeaders = this.splitDelimitedLine(lines[0], delimiter).map((cell) => cell.trim());
    this.missingHeaders = this.selectedTemplate.requiredHeaders.filter((header) => !this.previewHeaders.includes(header));
    this.unknownHeaders = this.previewHeaders.filter((header) => !this.selectedTemplate.headers.includes(header));
    this.previewRows = lines.slice(1, 51).map((line, index) => this.parseRow(line, delimiter, index + 2));
  }

  private parseRow(line: string, delimiter: string, rowNumber: number): ImportPreviewRow {
    const values = this.splitDelimitedLine(line, delimiter);
    const rowValues = this.previewHeaders.reduce<Record<string, string>>((accumulator, header, index) => {
      accumulator[header] = values[index]?.trim() ?? '';
      return accumulator;
    }, {});

    const issues = [
      ...this.selectedTemplate.requiredHeaders
        .filter((header) => !rowValues[header])
        .map((header) => `${header} manquant`),
      ...this.unknownHeaders.map((header) => `${header} non attendu`),
    ];

    return {
      rowNumber,
      values: rowValues,
      issues,
      status: this.missingHeaders.length || issues.some((issue) => issue.endsWith('manquant')) ? 'danger' : issues.length ? 'warning' : 'success',
    };
  }

  private detectDelimiter(headerLine: string) {
    const candidates = [';', ',', '\t'];
    return candidates.reduce((selected, candidate) =>
      this.splitDelimitedLine(headerLine, candidate).length > this.splitDelimitedLine(headerLine, selected).length
        ? candidate
        : selected,
    );
  }

  private splitDelimitedLine(line: string, delimiter: string) {
    const cells: string[] = [];
    let current = '';
    let quoted = false;

    for (const char of line) {
      if (char === '"') {
        quoted = !quoted;
        continue;
      }
      if (char === delimiter && !quoted) {
        cells.push(current);
        current = '';
        continue;
      }
      current += char;
    }

    cells.push(current);
    return cells;
  }
}
