import { Component, signal, OnInit, OnDestroy, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { Subscription } from 'rxjs';
import { SharedModule } from './shared/shared.module';
import { AppStateService } from './shared/services/app-state.service';
import { OfflineBannerComponent } from './shared/components/offline-banner/offline-banner.component';
import { AuthService } from './shared/services/auth.service';
import { ConsentService } from './shared/consent/consent.service';
import { ConsentModalComponent } from './shared/consent/consent-modal.component';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, SharedModule, OfflineBannerComponent, ConsentModalComponent],
  templateUrl: './app.html',
  styleUrl: './app.scss'
})
export class App implements OnInit, OnDestroy {
  protected readonly title = signal('Nowa');
  private auth = inject(AuthService);
  private consent = inject(ConsentService);
  private sessionSub?: Subscription;

  constructor(private appState: AppStateService) {
    this.appState.updateState();
  }

  ngOnInit(): void {
    // Module 5B — au login (et au reload si deja authentifie), on recupere
    // le statut consentement pour decider d'afficher le modal.
    this.sessionSub = this.auth.session$.subscribe((session) => {
      if (session) {
        this.consent.getStatus().subscribe({
          // L'erreur reseau n'est pas bloquante — le modal restera ferme
          // mais l'utilisateur sera invite a la prochaine connexion.
          error: () => undefined,
        });
      } else {
        this.consent.clear();
      }
    });
  }

  ngOnDestroy(): void {
    this.sessionSub?.unsubscribe();
  }
}
