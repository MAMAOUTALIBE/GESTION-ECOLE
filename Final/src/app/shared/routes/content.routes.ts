import { Routes } from '@angular/router';
import { dashboardRoutingModule } from '../../components/dashboards/dashboard.routes';
import { schoolCensusRoutingModule } from '../../components/school-census/school-census.routes';

export const content: Routes = [

  {
    path: '', children: [
      ...dashboardRoutingModule,
      ...schoolCensusRoutingModule,
      // Module 20 — vitrine du design system GE.
      {
        path: 'design-system',
        loadComponent: () =>
          import('../../design-system/design-system-demo.component').then(
            (m) => m.DesignSystemDemoComponent,
          ),
        title: 'GE-Design',
      },
    ]
  }
];

