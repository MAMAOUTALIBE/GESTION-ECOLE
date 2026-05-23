import { Component, input } from '@angular/core';
import { ChildrenOutletContexts, NavigationEnd, Router } from '@angular/router';
import { filter } from 'rxjs';
@Component({
    selector: 'app-page-header',
    templateUrl: './page-header.html',
    styleUrls: ['./page-header.scss'],
    standalone:false,
    
})
export class PageHeader {
   parentTitle?: string;
  subParentTitle?: string;
  childTitle?: string;

  constructor(
    private router: Router,
    private childrenOutletContexts: ChildrenOutletContexts
  ) {
    this.router.events
      .pipe(filter(event => event instanceof NavigationEnd))
      .subscribe(() => {
        const context = this.childrenOutletContexts.getContext('primary');
        const routeData = context?.route?.snapshot?.data;
        if (routeData) {
          this.childTitle = routeData['childTitle'] ?? '';
          this.parentTitle = routeData['parentTitle'] ?? '';
          this.subParentTitle = routeData['subParentTitle'] ?? '';

        }
      });
  }
}


