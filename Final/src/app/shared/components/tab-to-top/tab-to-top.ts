import { ViewportScroller } from '@angular/common';
import { Component, HostListener, inject } from '@angular/core';


@Component({
    selector: 'app-tab-to-top',
    templateUrl: './tab-to-top.html',
    styleUrl: './tab-to-top.scss',
    standalone:false,
})
export class TabToTop {
  private viewScroller = inject(ViewportScroller);

  public show: boolean = false;

  ngOnInit(): void {
  }

  @HostListener("window:scroll", [])

  onWindowScroll(){
    let number = window.pageYOffset || document.documentElement.scrollTop || document.body.scrollTop || 0;
    if (number > 100) {
      this.show = true;
    } else {
      this.show = false;
    }
  }
   
  taptotop(){
    let body:any = document.querySelector('body')
    body.style. scrollBehavior = 'smooth';
    this.viewScroller.scrollToPosition([0,0]);
  }
}


