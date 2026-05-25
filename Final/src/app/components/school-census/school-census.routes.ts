import { Routes } from '@angular/router';
import {
  CENSUS_READ_ROLES,
  NATIONAL_SCOPE_ROLES,
  REGIONAL_SCOPE_ROLES,
  UserRole,
} from '../../shared/services/auth.service';
import { roleGuard } from '../../shared/guards/role.guard';

// Module 1D — Dashboard Équité : seul un agent disposant d'une vue
// nationale ou régionale est habilité à consulter les indicateurs GPI
// agrégés (le détail "école" reste lisible par les rôles plus locaux
// via la table critique, qui pointe vers le profil école).
const EQUITE_DASHBOARD_ROLES: UserRole[] = [
  ...NATIONAL_SCOPE_ROLES,
  ...REGIONAL_SCOPE_ROLES,
];

// Module 3A — Réorganisation du réseau : NATIONAL / MINISTRY /
// REGIONAL / INSPECTOR. Les rôles préfecture et école n'ouvrent pas
// cette section — la décision de réorganisation se prend au minimum
// au niveau régional (cabinet, inspecteur académique).
const REORGANISATION_ROLES: UserRole[] = [
  ...NATIONAL_SCOPE_ROLES,
  ...REGIONAL_SCOPE_ROLES,
];

// Module 2D UI — Dashboard transferts enseignants. Volontairement plus
// restrictif que la réorganisation : seuls NATIONAL, MINISTRY et
// REGIONAL_ADMIN ouvrent cette page (l'INSPECTOR consulte les
// statistiques mais n'a pas vocation à arbitrer les transferts ; le
// roleGuard backend filtre déjà côté API).
const TRANSFERTS_ROLES: UserRole[] = [
  ...NATIONAL_SCOPE_ROLES,
  'REGIONAL_ADMIN',
];

// Module 3B UI — Simulateur what-if du réseau scolaire. Mêmes rôles que
// le backend (`SIMULATOR_WRITE_HTTP_ROLES`) : NATIONAL/MINISTRY/REGIONAL_ADMIN.
// Les autres rôles n'ont pas vocation à arbitrer la carte scolaire et
// l'écran serait inutile en lecture seule (les boutons d'écriture sont déjà
// désactivés côté frontend par `canEdit`).
const SIMULATEUR_ROLES: UserRole[] = [
  ...NATIONAL_SCOPE_ROLES,
  'REGIONAL_ADMIN',
];

// Module 3C UI — Priorités d'investissement (top 100 écoles). Ouvert à
// NATIONAL/MINISTRY/REGIONAL_ADMIN/INSPECTOR : l'inspecteur d'académie
// consulte la liste pour préparer ses tournées de terrain (lecture seule
// côté UI ; le bouton "Recalculer" reste réservé à NATIONAL/MINISTRY via
// le computed signal `canCompute`).
const INVESTISSEMENTS_ROLES: UserRole[] = [
  ...NATIONAL_SCOPE_ROLES,
  ...REGIONAL_SCOPE_ROLES,
];

export const schoolCensusRoutingModule: Routes = [
  {
    path: 'school-census/equite',
    canActivate: [roleGuard],
    data: {
      roles: EQUITE_DASHBOARD_ROLES,
      parentTitle: 'Pilotage national',
      childTitle: 'Équité (GPI)',
    },
    loadComponent: () => import('./equite/equite-page').then((m) => m.EquitePage),
  },
  {
    path: 'school-census/territory',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Administration', childTitle: 'Territoires' },
    loadComponent: () => import('./territory-admin/territory-admin').then((m) => m.TerritoryAdmin),
  },
  {
    path: 'school-census/map',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Recensement', childTitle: 'Carte scolaire' },
    loadComponent: () => import('./school-map/school-map').then((m) => m.SchoolMap),
  },
  {
    path: 'school-census/reorganisation',
    canActivate: [roleGuard],
    data: {
      roles: REORGANISATION_ROLES,
      parentTitle: 'Pilotage national',
      childTitle: 'Réorganisation réseau',
    },
    loadComponent: () =>
      import('./reorganisation/reorganisation-page').then(
        (m) => m.ReorganisationPage,
      ),
  },
  {
    path: 'school-census/transferts',
    canActivate: [roleGuard],
    data: {
      roles: TRANSFERTS_ROLES,
      parentTitle: 'Pilotage national',
      childTitle: 'Transferts enseignants',
    },
    loadComponent: () =>
      import('./transferts/transferts-page').then((m) => m.TransfertsPage),
  },
  {
    path: 'school-census/simulateur',
    canActivate: [roleGuard],
    data: {
      roles: SIMULATEUR_ROLES,
      parentTitle: 'Pilotage national',
      childTitle: 'Simulateur what-if',
    },
    loadComponent: () =>
      import('./simulateur/simulateur-page').then((m) => m.SimulateurPage),
  },
  {
    path: 'school-census/investissements',
    canActivate: [roleGuard],
    data: {
      roles: INVESTISSEMENTS_ROLES,
      parentTitle: 'Pilotage national',
      childTitle: "Priorités d'investissement",
    },
    loadComponent: () =>
      import('./investissements/investissements-page').then(
        (m) => m.InvestissementsPage,
      ),
  },
  {
    path: 'school-census/schools',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Recensement', childTitle: 'Écoles' },
    loadComponent: () => import('./schools/schools').then((m) => m.Schools),
  },
  {
    path: 'school-census/classes',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Recensement', childTitle: 'Classes' },
    loadComponent: () => import('./classes/classes').then((m) => m.Classes),
  },
  {
    path: 'school-census/infrastructure',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Recensement', childTitle: 'Infrastructures' },
    loadComponent: () => import('./infrastructure/infrastructure').then((m) => m.Infrastructure),
  },
  {
    path: 'school-census/students/:id',
    canActivate: [roleGuard],
    data: {
      roles: CENSUS_READ_ROLES,
      parentTitle: 'Recensement',
      childTitle: 'Détail élève',
      personType: 'STUDENT',
    },
    loadComponent: () => import('./person-profile/person-profile').then((m) => m.PersonProfile),
  },
  {
    path: 'school-census/students',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Recensement', childTitle: 'Élèves' },
    loadComponent: () => import('./students/students').then((m) => m.Students),
  },
  {
    path: 'school-census/transfers',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Recensement', childTitle: 'Transferts & inscriptions' },
    loadComponent: () => import('./transfers/transfers').then((m) => m.Transfers),
  },
  {
    path: 'school-census/parents',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Recensement', childTitle: 'Parents / Tuteurs' },
    loadComponent: () => import('./parents/parents').then((m) => m.Parents),
  },
  {
    path: 'school-census/teachers/:id',
    canActivate: [roleGuard],
    data: {
      roles: CENSUS_READ_ROLES,
      parentTitle: 'Recensement',
      childTitle: 'Détail enseignant',
      personType: 'TEACHER',
    },
    loadComponent: () => import('./person-profile/person-profile').then((m) => m.PersonProfile),
  },
  {
    path: 'school-census/teachers',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Personnel', childTitle: 'Enseignants' },
    loadComponent: () => import('./teachers/teachers').then((m) => m.Teachers),
  },
  {
    path: 'school-census/teacher-assignments',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Personnel', childTitle: 'Affectations enseignants' },
    loadComponent: () => import('./teacher-assignments/teacher-assignments').then((m) => m.TeacherAssignments),
  },
  {
    path: 'school-census/grades',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Pédagogie', childTitle: 'Notes' },
    loadComponent: () => import('./grades/grades').then((m) => m.Grades),
  },
  {
    path: 'school-census/exams',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Pédagogie', childTitle: 'Examens' },
    loadComponent: () => import('./exam-management/exam-management').then((m) => m.ExamManagement),
  },
  {
    path: 'school-census/resources',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Pédagogie', childTitle: 'Ressources pédagogiques' },
    loadComponent: () => import('./learning-resources/learning-resources').then((m) => m.LearningResources),
  },
  {
    path: 'school-census/library',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Pédagogie', childTitle: 'Bibliothèque & manuels' },
    loadComponent: () => import('./library-management/library-management').then((m) => m.LibraryManagement),
  },
  {
    path: 'school-census/school-years',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Administration scolaire', childTitle: 'Années scolaires' },
    loadComponent: () => import('./school-years/school-years').then((m) => m.SchoolYears),
  },
  {
    path: 'school-census/calendar',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Administration scolaire', childTitle: 'Calendrier scolaire' },
    loadComponent: () => import('./school-calendar/school-calendar').then((m) => m.SchoolCalendar),
  },
  {
    path: 'school-census/timetable',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Administration scolaire', childTitle: 'Emplois du temps' },
    loadComponent: () => import('./timetable/timetable').then((m) => m.Timetable),
  },
  {
    path: 'school-census/subjects',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Administration scolaire', childTitle: 'Matières' },
    loadComponent: () => import('./subjects/subjects').then((m) => m.Subjects),
  },
  {
    path: 'school-census/report-cards',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Pédagogie', childTitle: 'Bulletins' },
    loadComponent: () => import('./report-cards/report-cards').then((m) => m.ReportCards),
  },
  {
    path: 'school-census/attendance',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Vie scolaire', childTitle: 'Scan présences' },
    loadComponent: () => import('./attendance/attendance').then((m) => m.Attendance),
  },
  {
    path: 'school-census/attendance-monitoring',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Vie scolaire', childTitle: 'Suivi présences' },
    loadComponent: () => import('./attendance-monitoring/attendance-monitoring').then((m) => m.AttendanceMonitoring),
  },
  {
    path: 'school-census/health',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Vie scolaire', childTitle: 'Santé scolaire' },
    loadComponent: () => import('./school-health/school-health').then((m) => m.SchoolHealth),
  },
  {
    path: 'school-census/social-support',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Vie scolaire', childTitle: 'Cantines & aides' },
    loadComponent: () => import('./social-support/social-support').then((m) => m.SocialSupport),
  },
  {
    path: 'school-census/transport',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Vie scolaire', childTitle: 'Transport scolaire' },
    loadComponent: () => import('./school-transport/school-transport').then((m) => m.SchoolTransport),
  },
  {
    path: 'school-census/discipline',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Vie scolaire', childTitle: 'Discipline & incidents' },
    loadComponent: () => import('./discipline/discipline').then((m) => m.Discipline),
  },
  {
    path: 'school-census/reports',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Rapports', childTitle: 'Rapports officiels' },
    loadComponent: () => import('./reports/reports').then((m) => m.Reports),
  },
  {
    path: 'school-census/imports',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Rapports & outils', childTitle: 'Import & synchronisation' },
    loadComponent: () => import('./data-imports/data-imports').then((m) => m.DataImports),
  },
  {
    path: 'school-census/budget',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Rapports & outils', childTitle: 'Budget & financements' },
    loadComponent: () => import('./budget-monitoring/budget-monitoring').then((m) => m.BudgetMonitoring),
  },
  {
    path: 'school-census/users-roles',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Gouvernance', childTitle: 'Utilisateurs & rôles' },
    loadComponent: () => import('./users-roles/users-roles').then((m) => m.UsersRoles),
  },
  {
    path: 'school-census/settings',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Gouvernance', childTitle: 'Paramètres plateforme' },
    loadComponent: () => import('./platform-settings/platform-settings').then((m) => m.PlatformSettings),
  },
  {
    path: 'school-census/inspections',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Gouvernance', childTitle: 'Inspections & supervision' },
    loadComponent: () => import('./inspection-monitoring/inspection-monitoring').then((m) => m.InspectionMonitoring),
  },
  {
    path: 'school-census/policy-decision',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Pilotage ministériel', childTitle: 'Pouvoir décisionnel' },
    loadComponent: () => import('./policy-decision/policy-decision').then((m) => m.PolicyDecision),
  },
  {
    path: 'school-census/ministerial-dashboard',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Pilotage ministériel', childTitle: 'Tableau de bord du Ministre' },
    loadComponent: () => import('./ministerial-dashboard/ministerial-dashboard').then((m) => m.MinisterialDashboard),
  },
  {
    path: 'school-census/validations',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Administration', childTitle: 'Demandes à valider' },
    loadComponent: () => import('./validation-requests/validation-requests').then((m) => m.ValidationRequests),
  },
  {
    path: 'school-census/notifications',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Communication', childTitle: 'Notifications' },
    loadComponent: () => import('./notifications/notifications').then((m) => m.Notifications),
  },
  {
    path: 'identify/:token',
    canActivate: [roleGuard],
    data: { roles: CENSUS_READ_ROLES, parentTitle: 'Recensement', childTitle: 'Identification QR' },
    loadComponent: () => import('./identity/identity').then((m) => m.Identity),
  },
];
