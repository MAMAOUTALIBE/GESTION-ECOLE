import {
  Component,
  DOCUMENT,
  ElementRef,
  Renderer2,
  TemplateRef,
  ViewChild,
  inject,
} from '@angular/core';
import { Menu, NavService } from '../../services/nav.service';
import { SwitcherService } from '../../../shared/services/switcher.service';
import { NgbOffcanvas } from '@ng-bootstrap/ng-bootstrap';
import { NavigationEnd, Router } from '@angular/router';
import { filter } from 'rxjs';
import { AppStateService } from '../../services/app-state.service';

@Component({
  selector: 'app-content-layout',
  templateUrl: './content-layout.html',
  styleUrl: './content-layout.scss',
  standalone: false,
})
export class ContentLayout {
  private router = inject(Router);
  private elementRef = inject(ElementRef);
  navServices = inject(NavService);
  appStateService = inject(AppStateService);
  SwitcherService = inject(SwitcherService);
  renderer = inject(Renderer2);
  private document = inject<Document>(DOCUMENT);
  @ViewChild('responsiveoverlay') responsiveoverlay!: ElementRef<HTMLDivElement>;
  Selector = (selector: string): HTMLElement | null => document.querySelector<HTMLElement>(selector);

  private offcanvasService = inject(NgbOffcanvas);
  lastSegment: any;
  public menuItems!: Menu[];
  html = this.document.documentElement;
  constructor() {
    this.navServices.items.subscribe((menuItems: any) => {
      this.menuItems = menuItems;
    });
    this.router.events.pipe(
      filter(event => event instanceof NavigationEnd))
      .subscribe(() => {
        window.scrollTo(0, 0);
      });
    this.appStateService.state$.subscribe(state => {
      if (state) {
        if (window.innerWidth <= 992) {
          this.html?.setAttribute('data-toggled', this.html?.getAttribute('data-toggled') == 'close' ? 'close' : 'close');
        }
      }
      if (state.menuStyles == 'icon-click' || state.menuStyles == 'icon-hover') {
        this.closeMenu()
      }
    });

    if (window.innerWidth <= 992) {
      this.html?.setAttribute('data-toggled', this.html?.getAttribute('data-toggled') == 'close' ? 'close' : 'close');
    }
  }

  openEnd(content: TemplateRef<any>) {
    this.offcanvasService.open(content, { position: 'end' });
  }

  clickOnBody() {
    this.Selector('#responsive-overlay')?.classList.remove('active');
    this.Selector('#headersearch')?.classList.remove('searchdrop')
    this.Selector('#cartitemclose')?.classList.remove('show');
    this.Selector('#header-cart-items-scroll')?.removeAttribute('class');
    this.Selector('#notificationitemclose')?.classList.remove('show') || this.Selector('#notificationitemclose')?.classList.remove('show');
    const htmlElement = this.elementRef.nativeElement.ownerDocument.documentElement;
    // this.renderer.removeAttribute(htmlElement, 'data-icon-overlay');

    const navStyle = document.documentElement.getAttribute('data-nav-style');

    if (document.documentElement.getAttribute('data-toggled') == 'icon-text-close') {
      this.renderer.removeAttribute(htmlElement, 'data-icon-text');
    }
    if (document.documentElement.getAttribute('data-nav-layout') == 'horizontal'
      && window.innerWidth > 992) {
      this.closeMenu();
    } else
      if (navStyle === 'menu-click' || navStyle === 'menu-hover' || navStyle === 'icon-click' || navStyle === 'icon-hover') {
        this.Selector('.double-menu-active')?.setAttribute('style', 'display: none;');
      }

    const switcher = this.elementRef.nativeElement.querySelector('.switcher');
    if (switcher) {
      this.renderer.removeClass(switcher, 'show');
      this.responsiveoverlay.nativeElement.classList.add('active');
    } else {
      this.responsiveoverlay.nativeElement.classList.remove('active');
    }
    const sidebar = this.elementRef.nativeElement.querySelector('.sidebar');
    if (sidebar) {
      this.renderer.removeClass(sidebar, 'show');

    }

    if (window.innerWidth <= 992) {
      htmlElement?.setAttribute(
        'data-toggled',
        htmlElement?.getAttribute('data-toggled') == 'close' ? 'close' : 'close'
      );
    }
  }

  closeMenu() {
    this.menuItems?.forEach((a: any) => {
      if (this.menuItems) {
        a.active = false;
      }
      a?.children?.forEach((b: any) => {
        if (a.children) {
          b.active = false;
        }
      });
    });
  }

  clearToggle() {
    let html = this.document.documentElement;
    html?.setAttribute('data-toggled', 'close');
    this.Selector('#responsive-overlay')?.classList.remove('active');
  }

}


