import { Component, ElementRef, Renderer2, TemplateRef, inject } from '@angular/core';
import { NgbOffcanvas } from '@ng-bootstrap/ng-bootstrap';
import { Menu, NavService } from '../../services/nav.service';
import { SwitcherService } from '../../../shared/services/switcher.service';

@Component({
  selector: 'app-pageheader-layout',
  templateUrl: './pageheader-layout.html',
  styleUrl: './pageheader-layout.scss',
  standalone:false,
})
export class PageheaderLayout {
  private elementRef = inject(ElementRef);
  navServices = inject(NavService);
  SwitcherService = inject(SwitcherService);
  renderer = inject(Renderer2);

  private offcanvasService = inject(NgbOffcanvas);
  lastSegment: any;
  public menuItems!: Menu[];
  constructor() {
    this.navServices.items.subscribe((menuItems: any) => {
      this.menuItems = menuItems;
    });
  }
  openEnd(content: TemplateRef<any>) {
    this.offcanvasService.open(content, { position: 'end' });
  }
  clickOnBody() {
    document.querySelector('#cartitemclose')?.classList.remove('show');document.querySelector('#header-cart-items-scroll')?.removeAttribute('class');
    document.querySelector('#notificationitemclose')?.classList.remove('show')||document.querySelector('#notificationitemclose')?.classList.remove('show');
    const htmlElement =
      this.elementRef.nativeElement.ownerDocument.documentElement;
      this.SwitcherService.emitChange(false);

    if (localStorage.getItem('layoutStyles') == 'icontext') {
      this.renderer.removeAttribute(htmlElement, 'data-icon-text');
    }

    const switcher = this.elementRef.nativeElement.querySelector('.switcher');
    if (switcher) {
      this.renderer.removeClass(switcher, 'show');
      document.querySelector('#responsive-overlay')?.classList.add('active');
    } else {
      this.renderer.addClass('data-toggle', 'close');
      document.querySelector('#responsive-overlay')?.classList.remove('active');
    }
    const sidebar = this.elementRef.nativeElement.querySelector('.sidebar');
    if (sidebar) {
      this.renderer.removeClass(sidebar, 'show');
    }

    document.querySelector('#responsive-overlay')?.classList.remove('active');
    let html = this.elementRef.nativeElement.ownerDocument.documentElement;
    if (window.innerWidth <= 992) {
      html?.setAttribute(
        'data-toggled',
        html?.getAttribute('data-toggled') == 'close' ? 'close' : 'close'
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
    let html = this.elementRef.nativeElement.ownerDocument.documentElement;
    html?.setAttribute('data-toggled', 'close');
    document.querySelector('#responsive-overlay')?.classList.remove('active');
  }

}


