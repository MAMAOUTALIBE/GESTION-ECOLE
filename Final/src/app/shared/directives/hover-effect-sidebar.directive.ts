import { Directive, DOCUMENT,  HostListener, inject } from '@angular/core';

@Directive({ selector: '[appHoverEffectSidebar]' })
export class HoverEffectSidebarDirective {
  private document = inject<Document>(DOCUMENT);
  html = this.document.documentElement;

  @HostListener('mouseover') onHover() {
    if (window.innerWidth > 768) {
      this.html?.setAttribute('data-icon-overlay', 'open');
    }
  }
  @HostListener('mouseleave') onLeave() {
    if (window.innerWidth > 768) {
      this.html?.removeAttribute('data-icon-overlay');
    }
  }
}
