import { Injectable, NgZone, OnDestroy } from '@angular/core';
import { BehaviorSubject, Observable } from 'rxjs';
import { distinctUntilChanged } from 'rxjs/operators';

/**
 * NetworkService — Module 16 PWA
 *
 * Tracks the browser's online/offline status and exposes it both as an
 * RxJS Observable (`online$`) and as a synchronous getter (`isOnline()`).
 *
 * Listens to `window.online` / `window.offline` events. SSR safe: if the
 * `window` global is not available, defaults to "online".
 */
@Injectable({ providedIn: 'root' })
export class NetworkService implements OnDestroy {
  private readonly subject: BehaviorSubject<boolean>;
  public readonly online$: Observable<boolean>;

  private readonly onOnline = (): void => this.zone.run(() => this.subject.next(true));
  private readonly onOffline = (): void => this.zone.run(() => this.subject.next(false));

  constructor(private readonly zone: NgZone) {
    const initial =
      typeof navigator !== 'undefined' && typeof navigator.onLine === 'boolean'
        ? navigator.onLine
        : true;
    this.subject = new BehaviorSubject<boolean>(initial);
    this.online$ = this.subject.asObservable().pipe(distinctUntilChanged());

    if (typeof window !== 'undefined') {
      window.addEventListener('online', this.onOnline);
      window.addEventListener('offline', this.onOffline);
    }
  }

  /** Synchronous accessor for the current online status. */
  isOnline(): boolean {
    return this.subject.value;
  }

  ngOnDestroy(): void {
    if (typeof window !== 'undefined') {
      window.removeEventListener('online', this.onOnline);
      window.removeEventListener('offline', this.onOffline);
    }
    this.subject.complete();
  }
}
