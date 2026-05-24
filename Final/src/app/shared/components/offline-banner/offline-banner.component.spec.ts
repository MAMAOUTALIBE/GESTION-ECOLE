import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { NetworkService } from '../../network.service';
import { OfflineBannerComponent } from './offline-banner.component';

describe('OfflineBannerComponent', () => {
  let fixture: ComponentFixture<OfflineBannerComponent>;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [OfflineBannerComponent],
    });
    fixture = TestBed.createComponent(OfflineBannerComponent);
  });

  it('creates the component', () => {
    expect(fixture.componentInstance).toBeTruthy();
  });

  it('hides the banner while the network is online', async () => {
    window.dispatchEvent(new Event('online'));
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    const banner = fixture.nativeElement.querySelector('.offline-banner');
    expect(banner).toBeNull();
  });

  it('shows the banner when the network goes offline', async () => {
    fixture.detectChanges();
    await fixture.whenStable();
    window.dispatchEvent(new Event('offline'));
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const service = TestBed.inject(NetworkService);
    expect(service.isOnline()).toBe(false);

    const banner = fixture.nativeElement.querySelector('.offline-banner');
    expect(banner).not.toBeNull();
    expect(banner.textContent).toContain('Mode hors ligne');
  });
});
