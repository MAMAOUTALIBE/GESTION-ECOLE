import { Routes } from '@angular/router';

export const dashboardRoutingModule: Routes = [
  {
    path: 'dashboards', children: [
      {
        path: '',
        redirectTo: 'dashboard-1',
        pathMatch: 'full',
      },
      {
        path: 'dashboard-1',
        loadComponent: () => import('./dashboard-1/dashboard-1').then((m) => m.Dashboard1),
        title: 'Recensement scolaire - Tableau de bord',
        data: { parentTitle: 'Dashboards', subParentTitle: '', childTitle: 'Pilotage national' }
      },
      {
        path: 'dashboard-2',
        loadComponent: () => import('./dashboard-2/dashboard-2').then((m) => m.Dashboard2),
        title: 'Suivi académique - Tableau de bord',
        data: { parentTitle: 'Dashboards', subParentTitle: '', childTitle: 'Suivi académique' }
      },
      {
        path: 'dashboard-3',
        loadComponent: () => import('./dashboard-3/dashboard-3').then((m) => m.Dashboard3),
        title: 'Territoires - Tableau de bord',
        data: { parentTitle: 'Dashboards', subParentTitle: '', childTitle: 'Territoires' }
      },
    ]
  }
];
