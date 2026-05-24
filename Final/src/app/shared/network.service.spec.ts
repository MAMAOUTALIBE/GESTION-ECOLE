import { TestBed } from '@angular/core/testing';
import { firstValueFrom } from 'rxjs';
import { skip, take } from 'rxjs/operators';
import { describe, expect, it } from 'vitest';

import { NetworkService } from './network.service';

describe('NetworkService', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({});
  });

  it('exposes the current online status synchronously', () => {
    const service = TestBed.inject(NetworkService);
    expect(typeof service.isOnline()).toBe('boolean');
  });

  it('emits the initial online status on subscribe', async () => {
    const service = TestBed.inject(NetworkService);
    const first = await firstValueFrom(service.online$.pipe(take(1)));
    expect(typeof first).toBe('boolean');
  });

  it('reacts to window offline / online events', async () => {
    const service = TestBed.inject(NetworkService);

    const offlinePromise = firstValueFrom(service.online$.pipe(skip(1), take(1)));
    window.dispatchEvent(new Event('offline'));
    const offlineValue = await offlinePromise;
    expect(offlineValue).toBe(false);
    expect(service.isOnline()).toBe(false);

    const onlinePromise = firstValueFrom(service.online$.pipe(skip(1), take(1)));
    window.dispatchEvent(new Event('online'));
    const onlineValue = await onlinePromise;
    expect(onlineValue).toBe(true);
    expect(service.isOnline()).toBe(true);
  });
});
