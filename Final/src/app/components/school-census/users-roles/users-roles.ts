import { CommonModule } from '@angular/common';
import { Component, DestroyRef, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { catchError, of } from 'rxjs';
import {
  ACADEMIC_VALIDATION_ROLES,
  ACADEMIC_WRITE_ROLES,
  AuthService,
  CENSUS_READ_ROLES,
  CENSUS_WRITE_ROLES,
  CLASS_MANAGEMENT_ROLES,
  SCHOOL_MANAGEMENT_ROLES,
  UserDirectoryEntry,
  UserRole,
} from '../../../shared/services/auth.service';

type ScopeType = 'national' | 'regional' | 'prefecture' | 'sub-prefecture' | 'school';
type AuditStatus = 'success' | 'warning' | 'danger' | 'info';

interface RoleDefinition {
  role: UserRole;
  label: string;
  scope: ScopeType;
  description: string;
  color: string;
}

interface PermissionLine {
  key: string;
  label: string;
  description: string;
  roles: readonly UserRole[];
}

interface UserDirectoryRow {
  id: string;
  fullName: string;
  email: string;
  role: UserRole;
  scopeLabel: string;
  active: boolean;
  lastLogin: string;
}

interface AuditEvent {
  id: string;
  actor: string;
  action: string;
  target: string;
  date: string;
  status: AuditStatus;
}

@Component({
  selector: 'app-users-roles',
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './users-roles.html',
  styleUrl: './users-roles.scss',
})
export class UsersRoles {
  private auth = inject(AuthService);
  private destroyRef = inject(DestroyRef);

  searchTerm = '';
  selectedScope = '';
  selectedRole = '';
  selectedStatus = '';
  loading = false;
  loadError = '';

  ngOnInit() {
    this.loadUsers();
  }

  loadUsers() {
    this.loading = true;
    this.loadError = '';
    this.auth.listUsers()
      .pipe(catchError(() => of(null)), takeUntilDestroyed(this.destroyRef))
      .subscribe((rows) => {
        if (rows === null) {
          // Endpoint réservé aux admins ; pour les rôles non admins on garde les fixtures
          this.loadError = 'Annuaire restreint aux administrateurs nationaux/ministère.';
          this.loading = false;
          return;
        }
        this.users = rows.map((u) => this.toDirectoryRow(u));
        this.loading = false;
      });
  }

  private toDirectoryRow(u: UserDirectoryEntry): UserDirectoryRow {
    const scopeLabel =
      u.school?.name ??
      u.subPrefecture?.name ??
      u.prefecture?.name ??
      u.region?.name ??
      'National';
    return {
      id: u.id,
      fullName: u.fullName,
      email: u.email,
      role: u.role,
      scopeLabel,
      active: u.isActive,
      lastLogin: u.updatedAt
        ? new Date(u.updatedAt).toLocaleString('fr-FR', {
            day: '2-digit', month: '2-digit', year: 'numeric',
            hour: '2-digit', minute: '2-digit',
          })
        : '—',
    };
  }

  roles: RoleDefinition[] = [
    {
      role: 'NATIONAL_ADMIN',
      label: 'Administrateur national',
      scope: 'national',
      description: 'Accès complet aux référentiels, validations et paramètres nationaux.',
      color: 'primary',
    },
    {
      role: 'MINISTRY_ADMIN',
      label: 'Administrateur ministère',
      scope: 'national',
      description: 'Pilotage national, validation finale et consultation consolidée.',
      color: 'primary',
    },
    {
      role: 'REGIONAL_ADMIN',
      label: 'Administrateur régional',
      scope: 'regional',
      description: 'Gestion et validation des données de sa région.',
      color: 'info',
    },
    {
      role: 'INSPECTOR',
      label: 'Inspecteur',
      scope: 'regional',
      description: 'Lecture, contrôle et suivi des indicateurs régionaux.',
      color: 'info',
    },
    {
      role: 'PREFECTURE_ADMIN',
      label: 'Administrateur préfectoral',
      scope: 'prefecture',
      description: 'Validation et supervision des sous-préfectures et écoles.',
      color: 'warning',
    },
    {
      role: 'SUB_PREFECTURE_ADMIN',
      label: 'Administrateur sous-préfectoral',
      scope: 'sub-prefecture',
      description: 'Gestion des établissements et classes de son périmètre.',
      color: 'secondary',
    },
    {
      role: 'SCHOOL_DIRECTOR',
      label: 'Directeur d’école',
      scope: 'school',
      description: 'Gestion de son établissement, classes, élèves et présences.',
      color: 'success',
    },
    {
      role: 'TEACHER',
      label: 'Enseignant',
      scope: 'school',
      description: 'Saisie pédagogique, notes, présences et suivi de classe.',
      color: 'success',
    },
    {
      role: 'CENSUS_AGENT',
      label: 'Agent de recensement',
      scope: 'school',
      description: 'Collecte, mise à jour terrain et pointage QR.',
      color: 'success',
    },
  ];

  permissions: PermissionLine[] = [
    {
      key: 'read',
      label: 'Lecture recensement',
      description: 'Consultation des élèves, enseignants, écoles, cartes et rapports.',
      roles: CENSUS_READ_ROLES,
    },
    {
      key: 'write',
      label: 'Saisie recensement',
      description: 'Création et mise à jour des élèves, enseignants et affectations.',
      roles: CENSUS_WRITE_ROLES,
    },
    {
      key: 'schools',
      label: 'Gestion établissements',
      description: 'Création, modification et validation des écoles.',
      roles: SCHOOL_MANAGEMENT_ROLES,
    },
    {
      key: 'classes',
      label: 'Gestion classes',
      description: 'Création et modification des classes.',
      roles: CLASS_MANAGEMENT_ROLES,
    },
    {
      key: 'academics',
      label: 'Saisie pédagogique',
      description: 'Notes, bulletins, présences et activités académiques.',
      roles: ACADEMIC_WRITE_ROLES,
    },
    {
      key: 'validation',
      label: 'Validation académique',
      description: 'Validation des matières, bulletins et résultats.',
      roles: ACADEMIC_VALIDATION_ROLES,
    },
  ];

  /** Initialement fallback sample, remplacé au montage par un appel à /api/auth/users. */
  users: UserDirectoryRow[] = [
    {
      id: 'usr-001',
      fullName: 'Aminata Barry',
      email: 'aminata.barry@education.gov.gn',
      role: 'NATIONAL_ADMIN',
      scopeLabel: 'National',
      active: true,
      lastLogin: '02/05/2026 13:42',
    },
    {
      id: 'usr-002',
      fullName: 'Mamadou Diallo',
      email: 'mamadou.diallo@education.gov.gn',
      role: 'REGIONAL_ADMIN',
      scopeLabel: 'Région de Kindia',
      active: true,
      lastLogin: '02/05/2026 09:18',
    },
    {
      id: 'usr-003',
      fullName: 'Fatoumata Camara',
      email: 'fatoumata.camara@education.gov.gn',
      role: 'PREFECTURE_ADMIN',
      scopeLabel: 'Préfecture de Kaloum',
      active: true,
      lastLogin: '01/05/2026 17:05',
    },
    {
      id: 'usr-004',
      fullName: 'Ibrahima Keita',
      email: 'ibrahima.keita@education.gov.gn',
      role: 'SCHOOL_DIRECTOR',
      scopeLabel: 'École Primaire Almamya',
      active: true,
      lastLogin: '02/05/2026 08:31',
    },
    {
      id: 'usr-005',
      fullName: 'Mariama Sow',
      email: 'mariama.sow@education.gov.gn',
      role: 'TEACHER',
      scopeLabel: 'Collège 2 Octobre',
      active: true,
      lastLogin: '30/04/2026 16:22',
    },
    {
      id: 'usr-006',
      fullName: 'Ousmane Conte',
      email: 'ousmane.conte@education.gov.gn',
      role: 'CENSUS_AGENT',
      scopeLabel: 'Sous-préfecture de Manéah',
      active: false,
      lastLogin: '24/04/2026 11:06',
    },
  ];

  auditEvents: AuditEvent[] = [
    {
      id: 'audit-001',
      actor: 'Aminata Barry',
      action: 'Rôle modifié',
      target: 'Mamadou Diallo',
      date: '02/05/2026 13:12',
      status: 'success',
    },
    {
      id: 'audit-002',
      actor: 'Fatoumata Camara',
      action: 'Demande rejetée',
      target: 'École Sans GPS',
      date: '02/05/2026 10:48',
      status: 'warning',
    },
    {
      id: 'audit-003',
      actor: 'Système',
      action: 'Connexion refusée',
      target: 'Compte inactif',
      date: '01/05/2026 20:14',
      status: 'danger',
    },
    {
      id: 'audit-004',
      actor: 'Ibrahima Keita',
      action: 'Export lancé',
      target: 'Rapport territorial',
      date: '01/05/2026 15:37',
      status: 'info',
    },
  ];

  get currentUser() {
    return this.auth.currentUser;
  }

  get directoryRows() {
    const current = this.currentUser;
    const rows = current
      ? [
          {
            id: current.id,
            fullName: current.fullName,
            email: current.email,
            role: current.role,
            scopeLabel: this.currentUserScopeLabel(),
            active: true,
            lastLogin: 'Session active',
          },
          ...this.users.filter((user) => user.id !== current.id),
        ]
      : this.users;

    const search = this.normalizeSearch(this.searchTerm);

    return rows.filter((user) => {
      const roleDefinition = this.roleDefinition(user.role);
      const matchesScope = !this.selectedScope || roleDefinition.scope === this.selectedScope;
      const matchesRole = !this.selectedRole || user.role === this.selectedRole;
      const matchesStatus =
        !this.selectedStatus ||
        (this.selectedStatus === 'active' && user.active) ||
        (this.selectedStatus === 'inactive' && !user.active);
      const searchable = this.normalizeSearch([user.fullName, user.email, user.role, user.scopeLabel].join(' '));

      return matchesScope && matchesRole && matchesStatus && (!search || searchable.includes(search));
    });
  }

  get totals() {
    const rows = this.directoryRows;

    return {
      users: rows.length,
      active: rows.filter((user) => user.active).length,
      roles: this.roles.length,
      scopes: new Set(this.roles.map((role) => role.scope)).size,
    };
  }

  get scopes() {
    return [
      { value: 'national', label: 'National' },
      { value: 'regional', label: 'Régional' },
      { value: 'prefecture', label: 'Préfecture' },
      { value: 'sub-prefecture', label: 'Sous-préfecture' },
      { value: 'school', label: 'Établissement' },
    ];
  }

  resetFilters() {
    this.searchTerm = '';
    this.selectedScope = '';
    this.selectedRole = '';
    this.selectedStatus = '';
  }

  roleDefinition(role: UserRole) {
    return this.roles.find((item) => item.role === role) ?? this.roles[0];
  }

  roleLabel(role: UserRole) {
    return this.roleDefinition(role).label;
  }

  roleBadgeClass(role: UserRole) {
    const color = this.roleDefinition(role).color;
    return `bg-${color}-transparent text-${color}`;
  }

  hasPermission(role: UserRole, permission: PermissionLine) {
    return permission.roles.includes(role);
  }

  statusClass(status: AuditStatus) {
    return `bg-${status}-transparent text-${status}`;
  }

  formatNumber(value: number) {
    return value.toLocaleString('fr-FR');
  }

  private currentUserScopeLabel() {
    const user = this.currentUser;

    return (
      user?.school?.name ??
      user?.subPrefecture?.name ??
      user?.prefecture?.name ??
      user?.region?.name ??
      'National'
    );
  }

  private normalizeSearch(value?: string | null) {
    return (value ?? '')
      .toLocaleLowerCase('fr-FR')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '');
  }
}
