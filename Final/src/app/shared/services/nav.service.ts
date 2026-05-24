import { Injectable, OnDestroy, inject } from '@angular/core';
import { Subject, BehaviorSubject, fromEvent } from 'rxjs';
import { takeUntil, debounceTime } from 'rxjs/operators';
import { Router } from '@angular/router';
// Menu
export interface Menu {
  headTitle?: string;
  headTitle2?: string;
  path?: string;
  title?: string;
  icon?: string;
  type?: string;
  badgeValue?: string;
  badgeClass?: string;
  badgeText?: string;
  active?: boolean;
  selected?: boolean;
  bookmark?: boolean;
  children?: Menu[];
  children2?: Menu[];
  Menusub?: boolean;
  target?: boolean;
  menutype?: string,
  dirchange?: boolean,
  nochild?: any

}

@Injectable({
  providedIn: 'root',
})
export class NavService implements OnDestroy {
  private router = inject(Router);

  private unsubscriber: Subject<any> = new Subject();
  public screenWidth: BehaviorSubject<number> = new BehaviorSubject(
    window.innerWidth
  );

  // Search Box
  public search = false;

  // Language
  public language = false;

  // Mega Menu
  public megaMenu = false;
  public levelMenu = false;
  public megaMenuColapse: boolean = window.innerWidth < 1199 ? true : false;

  // Collapse Sidebar
  public collapseSidebar: boolean = window.innerWidth < 991 ? true : false;

  // For Horizontal Layout Mobile
  public horizontal: boolean = window.innerWidth < 991 ? false : true;

  // Full screen
  public fullScreen = false;
  active: any;

  constructor() {
    this.setScreenWidth(window.innerWidth);
    fromEvent(window, 'resize')
      .pipe(debounceTime(1000), takeUntil(this.unsubscriber))
      .subscribe((evt: any) => {
        this.setScreenWidth(evt.target.innerWidth);
        if (evt.target.innerWidth < 991) {
          this.collapseSidebar = true;
          this.megaMenu = false;
          this.levelMenu = false;
        }
        if (evt.target.innerWidth < 1199) {
          this.megaMenuColapse = true;
        }
      });
    if (window.innerWidth < 991) {
      // Detect Route change sidebar close
      this.router.events.subscribe((event) => {
        this.collapseSidebar = true;
        this.megaMenu = false;
        this.levelMenu = false;
      });
    }
  }

  ngOnDestroy() {
    this.unsubscriber.next;
    this.unsubscriber.complete();
  }

  private setScreenWidth(width: number): void {
    this.screenWidth.next(width);
  }

  schoolCensusMenu: Menu[] = [
    { headTitle: 'PILOTAGE NATIONAL' },
    {
      title: 'Dashboards',
      icon: `
        <svg xmlns="http://www.w3.org/2000/svg" class="side-menu__icon" width="24" height="24" viewBox="0 0 24 24"><path d="M4 13h6a1 1 0 0 0 1-1V4a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1zm1-8h4v6H5V5zm9 16h6a1 1 0 0 0 1-1v-8a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1zm1-8h4v6h-4v-6zM4 21h6a1 1 0 0 0 1-1v-4a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1zm1-4h4v2H5v-2zM14 9h6a1 1 0 0 0 1-1V4a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1zm1-4h4v2h-4V5z"/></svg>
      `,
      type: 'sub',
      dirchange: false,
      children: [
        {
          title: 'Dashboard 1',
          path: '/dashboards/dashboard-1',
          type: 'link',
        },
        {
          title: 'Dashboard 2',
          path: '/dashboards/dashboard-2',
          type: 'link',
        },
        {
          title: 'Dashboard 3',
          path: '/dashboards/dashboard-3',
          type: 'link',
        },
      ],
    },
    { headTitle: 'REGISTRES SCOLAIRES' },
    {
      title: 'Équité (GPI)',
      icon: '<i class="ri-scales-3-line side-menu__icon"></i>',
      path: '/school-census/equite',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Territoires',
      icon: `
        <svg xmlns="http://www.w3.org/2000/svg" class="side-menu__icon" width="24" height="24" viewBox="0 0 24 24"><path d="M12 2 3 6v6c0 5.55 3.84 9.74 9 10 5.16-.26 9-4.45 9-10V6l-9-4zm0 2.19 7 3.11V12c0 4.39-2.91 7.58-7 7.98C7.91 19.58 5 16.39 5 12V7.3l7-3.11z"/><path d="M8 9h8v2H8zm0 4h5v2H8z"/></svg>
      `,
      path: '/school-census/territory',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Établissements',
      icon: `
        <svg xmlns="http://www.w3.org/2000/svg" class="side-menu__icon" width="24" height="24" viewBox="0 0 24 24"><path d="M21 20h-2V5c0-1.103-.897-2-2-2H7c-1.103 0-2 .897-2 2v15H3v2h18v-2zM7 5h10v15h-3v-4h-4v4H7V5zm2 3h2v2H9V8zm4 0h2v2h-2V8zm-4 4h2v2H9v-2zm4 0h2v2h-2v-2z"/></svg>
      `,
      path: '/school-census/schools',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Carte scolaire',
      icon: `
        <svg xmlns="http://www.w3.org/2000/svg" class="side-menu__icon" width="24" height="24" viewBox="0 0 24 24"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5A2.5 2.5 0 1 1 12 6a2.5 2.5 0 0 1 0 5.5z"/></svg>
      `,
      path: '/school-census/map',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Classes',
      icon: `
        <svg xmlns="http://www.w3.org/2000/svg" class="side-menu__icon" width="24" height="24" viewBox="0 0 24 24"><path d="M4 4h16v2H4V4zm0 4h16v12H4V8zm2 2v8h12v-8H6zm2 2h8v2H8v-2zm0 3h5v2H8v-2z"/></svg>
      `,
      path: '/school-census/classes',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Infrastructures',
      icon: '<i class="ri-building-4-line side-menu__icon"></i>',
      path: '/school-census/infrastructure',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Élèves',
      icon: `
        <svg xmlns="http://www.w3.org/2000/svg" class="side-menu__icon" width="24" height="24" viewBox="0 0 24 24"><path d="M12 12c2.206 0 4-1.794 4-4s-1.794-4-4-4-4 1.794-4 4 1.794 4 4 4zm0-6c1.103 0 2 .897 2 2s-.897 2-2 2-2-.897-2-2 .897-2 2-2zm0 8c-3.859 0-7 2.691-7 6v1h14v-1c0-3.309-3.141-6-7-6zm-4.584 5c.516-1.738 2.392-3 4.584-3s4.068 1.262 4.584 3H7.416z"/></svg>
      `,
      path: '/school-census/students',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Transferts',
      icon: '<i class="ri-arrow-left-right-line side-menu__icon"></i>',
      path: '/school-census/transfers',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Parents',
      icon: `
        <svg xmlns="http://www.w3.org/2000/svg" class="side-menu__icon" width="24" height="24" viewBox="0 0 24 24"><path d="M16 11c1.657 0 3-1.343 3-3s-1.343-3-3-3-3 1.343-3 3 1.343 3 3 3zm-8 0c1.657 0 3-1.343 3-3S9.657 5 8 5 5 6.343 5 8s1.343 3 3 3zm0 2c-2.67 0-8 1.337-8 4v2h16v-2c0-2.663-5.33-4-8-4zm8 0c-.332 0-.729.021-1.17.064 1.329.959 2.17 2.251 2.17 3.936v2h7v-2c0-2.663-5.33-4-8-4z"/></svg>
      `,
      path: '/school-census/parents',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Enseignants',
      icon: `
        <svg xmlns="http://www.w3.org/2000/svg" class="side-menu__icon" width="24" height="24" viewBox="0 0 24 24"><path d="M20 6h-4V4c0-1.103-.897-2-2-2h-4c-1.103 0-2 .897-2 2v2H4c-1.103 0-2 .897-2 2v10c0 1.103.897 2 2 2h16c1.103 0 2-.897 2-2V8c0-1.103-.897-2-2-2zM10 4h4v2h-4V4zm10 14H4V8h16v10z"/><path d="M11 10h2v2h-2zm-4 0h2v2H7zm8 0h2v2h-2z"/></svg>
      `,
      path: '/school-census/teachers',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Affectations',
      icon: '<i class="ri-node-tree side-menu__icon"></i>',
      path: '/school-census/teacher-assignments',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    { headTitle: 'SUIVI PÉDAGOGIQUE' },
    {
      title: 'Notes',
      icon: `
        <svg xmlns="http://www.w3.org/2000/svg" class="side-menu__icon" width="24" height="24" viewBox="0 0 24 24"><path d="M19 3H5c-1.103 0-2 .897-2 2v14c0 1.103.897 2 2 2h14c1.103 0 2-.897 2-2V5c0-1.103-.897-2-2-2zM5 5h14v14H5V5zm2 3h10v2H7V8zm0 4h10v2H7v-2zm0 4h6v2H7v-2z"/></svg>
      `,
      path: '/school-census/grades',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Examens',
      icon: '<i class="ri-award-line side-menu__icon"></i>',
      path: '/school-census/exams',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Ressources',
      icon: '<i class="ri-stack-line side-menu__icon"></i>',
      path: '/school-census/resources',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Bibliothèque',
      icon: '<i class="ri-book-2-line side-menu__icon"></i>',
      path: '/school-census/library',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Années scolaires',
      icon: '<i class="ri-calendar-2-line side-menu__icon"></i>',
      path: '/school-census/school-years',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Calendrier',
      icon: '<i class="ri-calendar-event-line side-menu__icon"></i>',
      path: '/school-census/calendar',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Emplois du temps',
      icon: '<i class="ri-time-line side-menu__icon"></i>',
      path: '/school-census/timetable',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Matières',
      icon: '<i class="ri-book-open-line side-menu__icon"></i>',
      path: '/school-census/subjects',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Bulletins',
      icon: `
        <svg xmlns="http://www.w3.org/2000/svg" class="side-menu__icon" width="24" height="24" viewBox="0 0 24 24"><path d="M14 2H6c-1.103 0-2 .897-2 2v16c0 1.103.897 2 2 2h12c1.103 0 2-.897 2-2V8l-6-6zm-1 7V3.5L18.5 9H13zm-5 3h8v2H8v-2zm0 4h8v2H8v-2zm0-8h3v2H8V8z"/></svg>
      `,
      path: '/school-census/report-cards',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Présences',
      icon: `
        <svg xmlns="http://www.w3.org/2000/svg" class="side-menu__icon" width="24" height="24" viewBox="0 0 24 24"><path d="M3 3h8v8H3V3zm2 2v4h4V5H5zm8-2h8v8h-8V3zm2 2v4h4V5h-4zM3 13h8v8H3v-8zm2 2v4h4v-4H5zm10 0h2v2h-2v-2zm-2-2h2v2h-2v-2zm4 4h2v2h-2v-2zm2-4h2v4h-2v-4zm-6 6h4v2h-4v-2zm6 0h2v2h-2v-2z"/></svg>
      `,
      path: '/school-census/attendance',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Suivi présences',
      icon: '<i class="ri-calendar-check-line side-menu__icon"></i>',
      path: '/school-census/attendance-monitoring',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Santé scolaire',
      icon: '<i class="ri-heart-pulse-line side-menu__icon"></i>',
      path: '/school-census/health',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Cantines & aides',
      icon: '<i class="ri-hand-heart-line side-menu__icon"></i>',
      path: '/school-census/social-support',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Transport',
      icon: '<i class="ri-bus-2-line side-menu__icon"></i>',
      path: '/school-census/transport',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Discipline',
      icon: '<i class="ri-flag-line side-menu__icon"></i>',
      path: '/school-census/discipline',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Rapports officiels',
      icon: '<i class="ri-file-chart-line side-menu__icon"></i>',
      path: '/school-census/reports',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Import & sync',
      icon: '<i class="ri-upload-cloud-2-line side-menu__icon"></i>',
      path: '/school-census/imports',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Budget',
      icon: '<i class="ri-money-dollar-circle-line side-menu__icon"></i>',
      path: '/school-census/budget',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Utilisateurs & rôles',
      icon: '<i class="ri-shield-user-line side-menu__icon"></i>',
      path: '/school-census/users-roles',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Paramètres',
      icon: '<i class="ri-settings-4-line side-menu__icon"></i>',
      path: '/school-census/settings',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Inspections',
      icon: '<i class="ri-route-line side-menu__icon"></i>',
      path: '/school-census/inspections',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Pouvoir décisionnel',
      icon: '<i class="ri-government-line side-menu__icon"></i>',
      path: '/school-census/policy-decision',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Tableau de bord Ministre',
      icon: '<i class="ri-vip-crown-line side-menu__icon"></i>',
      path: '/school-census/ministerial-dashboard',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    { headTitle: 'VALIDATION & COMMUNICATION' },
    {
      title: 'Demandes à valider',
      icon: `
        <svg xmlns="http://www.w3.org/2000/svg" class="side-menu__icon" width="24" height="24" viewBox="0 0 24 24"><path d="M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/><path d="M19 19H5V5h10V3H5c-1.103 0-2 .897-2 2v14c0 1.103.897 2 2 2h14c1.103 0 2-.897 2-2v-8h-2v8z"/></svg>
      `,
      path: '/school-census/validations',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
    {
      title: 'Notifications',
      icon: `
        <svg xmlns="http://www.w3.org/2000/svg" class="side-menu__icon" width="24" height="24" viewBox="0 0 24 24"><path d="M12 22a2.5 2.5 0 0 0 2.45-2h-4.9A2.5 2.5 0 0 0 12 22zm7-6v-5c0-3.07-1.63-5.64-4.5-6.32V4a2.5 2.5 0 0 0-5 0v.68C6.64 5.36 5 7.92 5 11v5l-2 2v1h18v-1l-2-2z"/></svg>
      `,
      path: '/school-census/notifications',
      type: 'link',
      dirchange: false,
      nochild: true,
    },
  ];

  private subModuleIconFallbacks: Record<string, string> = {
    'Dashboard 1': '<i class="ri-dashboard-line side-menu__icon"></i>',
    'Dashboard 2': '<i class="ri-dashboard-2-line side-menu__icon"></i>',
    'Dashboard 3': '<i class="ri-dashboard-3-line side-menu__icon"></i>',
  };

  items = new BehaviorSubject<Menu[]>(this.compactSchoolCensusMenu());

  private compactSchoolCensusMenu(): Menu[] {
    const byTitle = new Map(
      this.schoolCensusMenu.filter((item) => item.title).map((item) => [item.title as string, item]),
    );

    const compactLink = (title: string): Menu => {
      const item = byTitle.get(title);

      return {
        title: item?.title ?? title,
        icon: item?.icon ?? this.subModuleIconFallbacks[title],
        path: item?.path,
        type: item?.type ?? 'empty',
      };
    };

    const compactGroup = (title: string, iconSource: string, children: string[]): Menu => ({
      title,
      icon: byTitle.get(iconSource)?.icon,
      type: 'sub',
      dirchange: false,
      children: children.map((child) => compactLink(child)),
    });

    const dashboards = byTitle.get('Dashboards');

    return [
      { headTitle: 'PILOTAGE NATIONAL' },
      dashboards
        ? {
            ...dashboards,
            children: dashboards.children?.map((child) => ({
              ...child,
              icon: child.icon ?? this.subModuleIconFallbacks[child.title ?? ''],
            })),
          }
        : compactGroup('Dashboards', 'Dashboards', []),
      { headTitle: 'MODULES' },
      compactGroup('Recensement scolaire', 'Établissements', [
        'Équité (GPI)',
        'Territoires',
        'Établissements',
        'Carte scolaire',
        'Classes',
        'Infrastructures',
        'Élèves',
        'Transferts',
        'Parents',
      ]),
      compactGroup('Personnel', 'Enseignants', ['Enseignants', 'Affectations']),
      compactGroup('Administration scolaire', 'Années scolaires', [
        'Années scolaires',
        'Calendrier',
        'Emplois du temps',
        'Matières',
      ]),
      compactGroup('Pédagogie', 'Notes', ['Notes', 'Examens', 'Ressources', 'Bibliothèque', 'Bulletins']),
      compactGroup('Vie scolaire', 'Présences', [
        'Présences',
        'Suivi présences',
        'Santé scolaire',
        'Cantines & aides',
        'Transport',
        'Discipline',
      ]),
      compactGroup('Rapports & outils', 'Rapports officiels', ['Rapports officiels', 'Import & sync', 'Budget']),
      compactGroup('Gouvernance', 'Utilisateurs & rôles', [
        'Utilisateurs & rôles',
        'Paramètres',
        'Inspections',
        'Demandes à valider',
        'Notifications',
      ]),
    ];
  }
}
