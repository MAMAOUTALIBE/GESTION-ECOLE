import { Component, ElementRef, OnInit, inject, Renderer2, TemplateRef } from '@angular/core';
import { SwitcherService } from '../../../shared/services/switcher.service';
import { NgbModal, NgbOffcanvas } from '@ng-bootstrap/ng-bootstrap';
import { Menu, NavService } from '../../services/nav.service';
import { Switcher } from '../switcher/switcher';
import { AppStateService } from '../../services/app-state.service';
import { RightSidebar } from '../right-sidebar/right-sidebar';
import { AuthService } from '../../services/auth.service';
import { AppNotification, WorkflowApiService } from '../../../components/school-census/shared/workflow-api.service';

interface Item {
  id: number;
  name: string;
  type: string;
  title: string;
  // Add other properties as needed
}
@Component({
  selector: 'app-header',
  templateUrl: './header.html',
  styleUrls: ['./header.scss'],
  standalone: false,
})
export class Header implements OnInit {
  elementRef = inject(ElementRef);
  SwitcherService = inject(SwitcherService);
  renderer = inject(Renderer2);
  NavServices = inject(NavService);
  authService = inject(AuthService);
  private workflowApi = inject(WorkflowApiService);
  private appStateService = inject(AppStateService);

  private modalService = inject(NgbModal);

  cartItemCount: number = 5;

  constructor() { }

  logout() {
    this.authService.logout();
  }

  private offcanvasService = inject(NgbOffcanvas);
  toggleSwitcher() {
    this.offcanvasService.open(Switcher, {
      position: 'end',
      scroll: true,
    });
  }

  openNotifications() {
    this.offcanvasService.open(RightSidebar, {
      position: 'end',
      scroll: true,
      panelClass:'sidebar-right'
    });
  }

  updateTheme(theme: string) {
    this.appStateService.updateState({ theme, menuColor: theme, headerColor: theme });
    if (theme == 'light') {
      this.appStateService.updateState({ theme, themeBackground: '', headerColor: 'light', menuColor: 'light' });
      let html = document.querySelector('html');
      html?.style.removeProperty('--body-bg-rgb');
      html?.style.removeProperty('--body-bg-rgb2');
      html?.style.removeProperty('--light-rgb');
      html?.style.removeProperty('--form-control-bg');
      html?.style.removeProperty('--input-border');
    }
    if (theme == 'dark') {
      this.appStateService.updateState({ theme, themeBackground: '', headerColor: 'dark', menuColor: 'dark' });
      let html = document.querySelector('html');
      html?.style.removeProperty('--body-bg-rgb');
      html?.style.removeProperty('--body-bg-rgb2');
      html?.style.removeProperty('--light-rgb');
      html?.style.removeProperty('--form-control-bg');
      html?.style.removeProperty('--input-border');
    }
  }


  toggleSidebar() {
    let html = document.querySelector("html")!;

    // Check the window width
    if (window.innerWidth <= 992) {
      let dataToggled = html.getAttribute("data-toggled");

      if (dataToggled == "open") {
        html.setAttribute("data-toggled", "close");
      } else {
        html.setAttribute("data-toggled", "open");
      }
    }
    else {
      let menuNavLayoutType = html.getAttribute("data-nav-style");
      let verticalStyleType = html.getAttribute("data-vertical-style");

      if (menuNavLayoutType) {
        let dataToggled = html.getAttribute("data-toggled");
        if (dataToggled) {
          html.removeAttribute("data-toggled");
        } else {
          html.setAttribute("data-toggled", menuNavLayoutType + "-closed",);
        }
      } else if (verticalStyleType) {
        let dataToggled = html.getAttribute("data-toggled");

        if (verticalStyleType == "doublemenu") {
          if (
            html.getAttribute("data-toggled") === "double-menu-open" && document.querySelector(".double-menu-active")
          ) {
            html.setAttribute("data-toggled", "double-menu-close");
          } else {
            if (document.querySelector(".double-menu-active")) {
              html.setAttribute("data-toggled", "double-menu-open",);
            }
          }
        } else if (dataToggled) {
          html.removeAttribute("data-toggled");
        } else {
          switch (verticalStyleType) {
            case "closed":
              html.setAttribute("data-toggled", "close-menu-close",);
              break;
            case "icontext":
              html.setAttribute("data-toggled", "icon-text-close",);
              break;
            case "overlay":
              html.setAttribute("data-toggled", "icon-overlay-close",);
              break;
            case "detached":
              html.setAttribute("data-toggled", "detached-close");
              break;
            default:

          }
        }
      }
    }
  }

  cartItems = [
    {
      id: 'row1',
      iconClass: 'ri-map-pin-line',
      name: 'Carte scolaire',
      detail: 'Localisation des établissements',
      route: ['/school-census/map'],
    },
    {
      id: 'row2',
      iconClass: 'ri-school-line',
      name: 'Établissements',
      detail: 'Registre des écoles',
      route: ['/school-census/schools'],
    },
    {
      id: 'row3',
      iconClass: 'ri-user-line',
      name: 'Élèves',
      detail: 'Dossiers recensés',
      route: ['/school-census/students'],
    },
    {
      id: 'row4',
      iconClass: 'ri-briefcase-4-line',
      name: 'Enseignants',
      detail: 'Personnel affecté',
      route: ['/school-census/teachers'],
    },
    {
      id: 'row5',
      iconClass: 'ri-qr-scan-2-line',
      name: 'Présences',
      detail: 'Pointage QR',
      route: ['/school-census/attendance'],
    },
  ];

  notifications: AppNotification[] = [];
  notificationCount = 0;

  loadWorkflowNotifications() {
    if (!this.authService.isAuthenticated) {
      this.notifications = [];
      this.notificationCount = 0;
      this.isNotifyEmpty = true;
      return;
    }

    this.workflowApi.notifications(true).subscribe({
      next: (notifications) => {
        this.notifications = notifications.slice(0, 6);
        this.isNotifyEmpty = this.notifications.length === 0;
      },
      error: () => {
        this.notifications = [];
        this.isNotifyEmpty = true;
      },
    });

    this.workflowApi.unreadCount().subscribe({
      next: ({ count }) => {
        this.notificationCount = count;
        this.isNotifyEmpty = count === 0;
      },
      error: () => {
        this.notificationCount = 0;
      },
    });
  }

  markWorkflowNotificationRead(notification: AppNotification) {
    if (notification.isRead) {
      return;
    }

    this.workflowApi.markNotificationRead(notification.id).subscribe({
      next: () => {
        notification.isRead = true;
        this.notifications = this.notifications.filter((item) => item.id !== notification.id);
        this.notificationCount = Math.max(this.notificationCount - 1, 0);
        this.isNotifyEmpty = this.notifications.length === 0;
      },
    });
  }

  notificationIcon(notification: AppNotification) {
    const icons: Record<string, string> = {
      VALIDATION_REQUEST: 'ri-checkbox-circle-line text-fixed-white fs-18',
      VALIDATION_APPROVED: 'ri-check-line text-fixed-white fs-18',
      VALIDATION_REJECTED: 'ri-close-line text-fixed-white fs-18',
      CORRECTION_REQUIRED: 'ri-error-warning-line text-fixed-white fs-18',
      SYSTEM_ALERT: 'ri-alert-line text-fixed-white fs-18',
      MESSAGE: 'ri-mail-line text-fixed-white fs-18',
    };
    return icons[notification.type] ?? 'ri-notification-3-line text-fixed-white fs-18';
  }

  notificationBgClass(notification: AppNotification) {
    const classes: Record<string, string> = {
      VALIDATION_REQUEST: 'bg-primary',
      VALIDATION_APPROVED: 'bg-success',
      VALIDATION_REJECTED: 'bg-danger',
      CORRECTION_REQUIRED: 'bg-warning',
      SYSTEM_ALERT: 'bg-info',
      MESSAGE: 'bg-secondary',
    };
    return classes[notification.type] ?? 'bg-secondary';
  }

  notificationDate(notification: AppNotification) {
    return new Date(notification.createdAt).toLocaleString('fr-FR', {
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    });
  }
  languages = [
    { code: 'en', name: 'English', flagSrc: './assets/images/flags/us_flag.jpg' },
    { code: 'es', name: 'Spanish', flagSrc: './assets/images/flags/spain_flag.jpg' },
    { code: 'fr', name: 'French', flagSrc: './assets/images/flags/french_flag.jpg' },
    { code: 'de', name: 'German', flagSrc: './assets/images/flags/germany_flag.jpg' },
    { code: 'it', name: 'Italian', flagSrc: './assets/images/flags/italy_flag.jpg' },
    { code: 'ru', name: 'Russian', flagSrc: './assets/images/flags/russia_flag.jpg' },
  ];


  apps = [
    { name: 'Carte', iconClass: 'ri-map-pin-line', route: ['/school-census/map'] },
    { name: 'Écoles', iconClass: 'ri-school-line', route: ['/school-census/schools'] },
    { name: 'Élèves', iconClass: 'ri-user-line', route: ['/school-census/students'] },
    { name: 'Classes', iconClass: 'ri-layout-grid-line', route: ['/school-census/classes'] },
    { name: 'Notes', iconClass: 'ri-file-list-3-line', route: ['/school-census/grades'] },
    { name: 'Présences', iconClass: 'ri-qr-scan-2-line', route: ['/school-census/attendance'] },
  ];
  SearchModal(SearchModal: TemplateRef<HTMLElement>) {
    this.modalService.open(SearchModal);
  }
  SearchHeader() {
    document.querySelector('.header-search')?.classList.toggle('searchdrop');
  }
  isCartEmpty: boolean = false;
  isNotifyEmpty: boolean = false;

  removeRow(rowId: string) {
    const rowElement = document.getElementById(rowId);
    if (rowElement) {
      rowElement.remove();
    }
    this.cartItemCount--;
    this.isCartEmpty = this.cartItemCount === 0;
  }



  handleCardClick(event: MouseEvent) {
    // Prevent the click event from propagating to the container
    event.stopPropagation();
  }

  isFullscreen: boolean = false;

  toggleFullscreen() {
    this.isFullscreen = !this.isFullscreen;
  }

  openModal(content: TemplateRef<HTMLElement>) {
    this.modalService.open(content, {
      windowClass: 'searchdisplay',
      backdropClass: 'searchdisplaybackdrop'
    });
  }

  ngOnInit(): void {
    this.loadWorkflowNotifications();
    this.NavServices.items.subscribe((menuItems) => {
      this.items = menuItems;
    });
    // To clear and close the search field by clicking on body
    document.querySelector('.main-content')?.addEventListener('click', () => {
      this.clearSearch();
    })
    this.text = '';
  }

  //search
  public menuItems!: Menu[];
  public items!: Menu[];
  public text!: string;
  public SearchResultEmpty: boolean = false;

  Search(searchText: any) {
    if (!searchText) return this.menuItems = [];
    // items array which stores the elements
    let items: any[] = [];
    // Converting the text to lower case by using toLowerCase() and trim() used to remove the spaces from starting and ending
    searchText = searchText.toLowerCase().trim();
    this.items.filter((menuItems: any) => {
      // checking whether menuItems having title property, if there was no title property it will return
      if (!menuItems?.title) return false;
      //  checking wheteher menuitems type is text or string and checking the titles of menuitems
      if (menuItems.type === 'link' && menuItems.title.toLowerCase().includes(searchText)) {
        // Converting the menuitems title to lowercase and checking whether title is starting with same text of searchText
        if (menuItems.title.toLowerCase().startsWith(searchText)) {// If you want to get all the data with matching to letter entered remove this line(condition and leave items.push(menuItems))
          // If both are matching then the code is pushed to items array
          items.push(menuItems);
        }
      }
      //  checking whether the menuItems having children property or not if there was no children the return
      if (!menuItems.children) return false;
      menuItems.children.filter((subItems: any) => {
        if (subItems.type === 'link' && subItems.title.toLowerCase().includes(searchText)) {
          if (subItems.title.toLowerCase().startsWith(searchText)) {         // If you want to get all the data with matching to letter entered remove this line(condition and leave items.push(subItems))
            items.push(subItems);
          }

        }
        if (!subItems.children) return false;
        subItems.children.filter((subSubItems: any) => {
          if (subSubItems.title.toLowerCase().includes(searchText)) {
            if (subSubItems.title.toLowerCase().startsWith(searchText)) {// If you want to get all the data with matching to letter entered remove this line(condition and leave items.push(subSubItems))
              items.push(subSubItems);
            }
          }
        })
        return;
      })
      return this.menuItems = items;
    });
    // Used to show the No search result found box if the length of the items is 0
    if (!items.length) {
      this.SearchResultEmpty = true;
    }
    else {
      this.SearchResultEmpty = false;
    }
    return;
  }

  //  Used to clear previous search result
  clearSearch() {
    this.text = '';
    this.menuItems = [];
    this.SearchResultEmpty = false;
    return this.text, this.menuItems
  }

}


