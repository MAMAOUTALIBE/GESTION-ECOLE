import { Routes } from '@angular/router';
import { dashboardRoutingModule } from '../../components/dashboards/dashboard.routes';
import { schoolCensusRoutingModule } from '../../components/school-census/school-census.routes';

export const content: Routes = [

  {
    path: '', children: [
      ...dashboardRoutingModule,
      ...schoolCensusRoutingModule
    ]
  }
];

