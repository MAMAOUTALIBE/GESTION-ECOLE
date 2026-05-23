// communication.service.ts
import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

@Injectable({
  providedIn: 'root',
})
export class SidebarService {
    private doubleMenuToggleSubject = new BehaviorSubject<boolean>(false);
    doubleMenuToggle$ = this.doubleMenuToggleSubject.asObservable();
  
    setNavActive(): void {
      this.doubleMenuToggleSubject.next(true);
    }
}