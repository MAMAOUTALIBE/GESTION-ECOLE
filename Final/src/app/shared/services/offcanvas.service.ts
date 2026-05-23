import { Injectable } from '@angular/core';
import { Subject } from 'rxjs';

@Injectable({
  providedIn: 'root',
})
export class SwitcherService {
  private switcherSubject = new Subject<boolean>();

  switcherState$ = this.switcherSubject.asObservable();

  openSwitcher() {
    this.switcherSubject.next(true);
  }

  closeSwitcher() {
    this.switcherSubject.next(false);
  }
}