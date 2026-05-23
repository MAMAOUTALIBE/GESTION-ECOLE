import { Component, inject } from '@angular/core';
import { NgbActiveOffcanvas } from '@ng-bootstrap/ng-bootstrap';

@Component({
  selector: 'app-right-sidebar',
  templateUrl: './right-sidebar.html',
  styleUrls: ['./right-sidebar.scss'],
  standalone: false,
})
export class RightSidebar {
  activeOffcanvas = inject(NgbActiveOffcanvas);

  shortcuts = [
    {
      title: 'Carte scolaire',
      description: 'GPS et charge des établissements',
      icon: 'ri-map-pin-line',
      route: ['/school-census/map'],
    },
    {
      title: 'Établissements',
      description: 'Registre des écoles',
      icon: 'ri-school-line',
      route: ['/school-census/schools'],
    },
    {
      title: 'Élèves',
      description: 'Dossiers et cartes scolaires',
      icon: 'ri-user-line',
      route: ['/school-census/students'],
    },
    {
      title: 'Notifications',
      description: 'Validations et alertes administratives',
      icon: 'ri-notification-3-line',
      route: ['/school-census/notifications'],
    },
  ];

  statusItems = [
    { label: 'Périmètre', value: 'National', badgeClass: 'bg-primary-transparent text-primary' },
    { label: 'Module actif', value: 'Recensement', badgeClass: 'bg-success-transparent text-success' },
    { label: 'Interface', value: 'Sombre', badgeClass: 'bg-secondary-transparent text-secondary' },
  ];
}
