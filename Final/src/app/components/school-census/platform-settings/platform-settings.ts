import { CommonModule } from '@angular/common';
import { Component, DestroyRef, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { catchError, of } from 'rxjs';
import { AuthService } from '../../../shared/services/auth.service';
import { AdminApiService, PlatformSetting as ApiPlatformSetting } from '../shared/admin-api.service';

type SettingsCategoryId = 'quality' | 'capacity' | 'attendance' | 'security';

interface SettingsCategory {
  id: SettingsCategoryId;
  title: string;
  description: string;
  icon: string;
  color: string;
}

interface ThresholdSetting {
  id: string;
  category: SettingsCategoryId;
  label: string;
  description: string;
  value: number;
  defaultValue: number;
  min: number;
  max: number;
  unit: string;
}

interface WorkflowStep {
  id: string;
  entity: string;
  requester: string;
  reviewer: string;
  slaHours: number;
  autoApprove: boolean;
}

interface NotificationChannel {
  id: string;
  label: string;
  description: string;
  icon: string;
  color: string;
  enabled: boolean;
}

@Component({
  selector: 'app-platform-settings',
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './platform-settings.html',
  styleUrl: './platform-settings.scss',
})
export class PlatformSettings {
  private auth = inject(AuthService);
  private adminApi = inject(AdminApiService);
  private destroyRef = inject(DestroyRef);

  selectedCategory: SettingsCategoryId = 'quality';
  saving = false;
  savedAt = new Date();
  /** Paramètres backend chargés via /api/admin/settings (Phase 13bis). */
  apiSettings: ApiPlatformSetting[] = [];
  apiSavingKey = '';
  apiSaveError = '';

  ngOnInit() {
    this.loadApiSettings();
  }

  loadApiSettings() {
    this.adminApi.listSettings()
      .pipe(catchError(() => of([] as ApiPlatformSetting[])), takeUntilDestroyed(this.destroyRef))
      .subscribe((rows) => { this.apiSettings = rows; });
  }

  saveApiSetting(setting: ApiPlatformSetting) {
    this.apiSavingKey = setting.key;
    this.apiSaveError = '';
    this.adminApi.updateSetting(setting.key, setting.value)
      .pipe(catchError((err) => {
        this.apiSaveError = err?.error?.detail ?? 'Échec sauvegarde paramètre.';
        return of(null);
      }), takeUntilDestroyed(this.destroyRef))
      .subscribe((updated) => {
        this.apiSavingKey = '';
        if (updated) {
          // Remplace dans la liste
          this.apiSettings = this.apiSettings.map((s) =>
            s.key === setting.key ? updated : s,
          );
          this.savedAt = new Date();
        }
      });
  }

  apiSettingsByCategory(category: string) {
    return this.apiSettings.filter((s) => s.category === category);
  }

  apiSettingCategories(): string[] {
    return Array.from(new Set(this.apiSettings.map((s) => s.category))).sort();
  }

  categories: SettingsCategory[] = [
    {
      id: 'quality',
      title: 'Qualité des données',
      description: 'Complétude des profils, photos, classes et coordonnées.',
      icon: 'ri-shield-check-line',
      color: 'primary',
    },
    {
      id: 'capacity',
      title: 'Capacité scolaire',
      description: 'Seuils de surcharge des classes et ratio élèves/enseignant.',
      icon: 'ri-building-4-line',
      color: 'warning',
    },
    {
      id: 'attendance',
      title: 'Présences',
      description: 'Tolérance de retard et suivi des absences.',
      icon: 'ri-calendar-check-line',
      color: 'success',
    },
    {
      id: 'security',
      title: 'Sécurité',
      description: 'Session, accès, audit et alertes critiques.',
      icon: 'ri-lock-password-line',
      color: 'danger',
    },
  ];

  thresholds: ThresholdSetting[] = [
    {
      id: 'quality-score',
      category: 'quality',
      label: 'Score qualité minimum',
      description: 'Seuil requis avant validation officielle.',
      value: 85,
      defaultValue: 85,
      min: 0,
      max: 100,
      unit: '%',
    },
    {
      id: 'gps-coverage',
      category: 'quality',
      label: 'Couverture GPS cible',
      description: 'Part minimale des écoles géolocalisées.',
      value: 90,
      defaultValue: 90,
      min: 0,
      max: 100,
      unit: '%',
    },
    {
      id: 'missing-photo',
      category: 'quality',
      label: 'Tolérance photos manquantes',
      description: 'Pourcentage maximum de profils sans photo.',
      value: 5,
      defaultValue: 5,
      min: 0,
      max: 100,
      unit: '%',
    },
    {
      id: 'class-fill',
      category: 'capacity',
      label: 'Seuil surcharge classe',
      description: 'Taux de remplissage déclenchant une alerte.',
      value: 105,
      defaultValue: 105,
      min: 50,
      max: 150,
      unit: '%',
    },
    {
      id: 'student-teacher-ratio',
      category: 'capacity',
      label: 'Ratio élèves / enseignant',
      description: 'Ratio maximal recommandé par établissement.',
      value: 45,
      defaultValue: 45,
      min: 10,
      max: 100,
      unit: '',
    },
    {
      id: 'late-minutes',
      category: 'attendance',
      label: 'Retard toléré',
      description: 'Délai après le début des cours.',
      value: 15,
      defaultValue: 15,
      min: 0,
      max: 120,
      unit: 'min',
    },
    {
      id: 'absence-alert',
      category: 'attendance',
      label: 'Alerte absences répétées',
      description: 'Nombre de jours déclenchant une alerte.',
      value: 3,
      defaultValue: 3,
      min: 1,
      max: 30,
      unit: 'j',
    },
    {
      id: 'session-duration',
      category: 'security',
      label: 'Durée session',
      description: 'Durée maximale avant reconnexion.',
      value: 8,
      defaultValue: 8,
      min: 1,
      max: 24,
      unit: 'h',
    },
    {
      id: 'audit-retention',
      category: 'security',
      label: 'Conservation audit',
      description: 'Durée de conservation du journal.',
      value: 24,
      defaultValue: 24,
      min: 1,
      max: 60,
      unit: 'mois',
    },
  ];

  workflowSteps: WorkflowStep[] = [
    {
      id: 'prefecture',
      entity: 'Préfecture',
      requester: 'Administrateur régional',
      reviewer: 'Administrateur ministère',
      slaHours: 48,
      autoApprove: false,
    },
    {
      id: 'sub-prefecture',
      entity: 'Sous-préfecture',
      requester: 'Administrateur préfectoral',
      reviewer: 'Administrateur régional',
      slaHours: 36,
      autoApprove: false,
    },
    {
      id: 'school',
      entity: 'Établissement',
      requester: 'Administrateur sous-préfectoral',
      reviewer: 'Administrateur préfectoral',
      slaHours: 24,
      autoApprove: false,
    },
    {
      id: 'teacher',
      entity: 'Enseignant',
      requester: 'Directeur / agent',
      reviewer: 'Administrateur sous-préfectoral',
      slaHours: 24,
      autoApprove: true,
    },
  ];

  channels: NotificationChannel[] = [
    {
      id: 'in-app',
      label: 'In-app',
      description: 'Centre de notifications de la plateforme.',
      icon: 'ri-notification-3-line',
      color: 'primary',
      enabled: true,
    },
    {
      id: 'email',
      label: 'Email',
      description: 'Alertes administratives et rapports programmés.',
      icon: 'ri-mail-line',
      color: 'info',
      enabled: true,
    },
    {
      id: 'sms',
      label: 'SMS',
      description: 'Alertes critiques vers les responsables terrain.',
      icon: 'ri-message-2-line',
      color: 'success',
      enabled: false,
    },
    {
      id: 'whatsapp',
      label: 'WhatsApp',
      description: 'Relances opérationnelles vers les écoles.',
      icon: 'ri-whatsapp-line',
      color: 'success',
      enabled: false,
    },
  ];

  get currentUserName() {
    return this.auth.currentUserName || 'Administrateur';
  }

  get selectedCategoryDefinition() {
    return this.categories.find((category) => category.id === this.selectedCategory) ?? this.categories[0];
  }

  get visibleThresholds() {
    return this.thresholds.filter((setting) => setting.category === this.selectedCategory);
  }

  get totals() {
    return {
      thresholds: this.thresholds.length,
      workflows: this.workflowSteps.length,
      channels: this.channels.filter((channel) => channel.enabled).length,
      modified: this.thresholds.filter((setting) => setting.value !== setting.defaultValue).length,
    };
  }

  saveSettings() {
    this.saving = true;
    setTimeout(() => {
      this.savedAt = new Date();
      this.saving = false;
    }, 600);
  }

  resetDefaults() {
    this.thresholds = this.thresholds.map((setting) => ({
      ...setting,
      value: setting.defaultValue,
    }));
  }

  toggleChannel(channel: NotificationChannel) {
    channel.enabled = !channel.enabled;
  }

  settingProgress(setting: ThresholdSetting) {
    const range = setting.max - setting.min;
    return range ? Math.round(((setting.value - setting.min) / range) * 100) : 0;
  }

  channelClass(channel: NotificationChannel) {
    return channel.enabled
      ? `bg-${channel.color}-transparent text-${channel.color}`
      : 'bg-light text-muted';
  }

  formatSavedAt() {
    return this.savedAt.toLocaleString('fr-FR', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }
}
