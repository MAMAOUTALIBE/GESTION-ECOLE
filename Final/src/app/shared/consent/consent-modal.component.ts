import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterLink } from '@angular/router';
import { TranslateModule } from '@ngx-translate/core';
import { ConsentService } from './consent.service';
import { AuthService } from '../services/auth.service';

/**
 * ConsentModalComponent — Module 5B
 *
 * Modal Bootstrap qui s'affiche apres login si `ConsentService.needsAcceptance`
 * est vrai. Il presente un resume des donnees collectees, des finalites et des
 * droits de l'utilisateur (acces aux logs Module 5C + droit a l'oubli 5D),
 * puis demande un acte explicite : "J'accepte" ou "Se deconnecter".
 *
 * Le modal n'est PAS fermable sans choix (backdrop static, pas de bouton X) —
 * conforme au principe RGPD "consentement libre, eclaire, specifique".
 */
@Component({
  selector: 'app-consent-modal',
  standalone: true,
  imports: [CommonModule, TranslateModule, RouterLink],
  templateUrl: './consent-modal.component.html',
  styleUrls: ['./consent-modal.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ConsentModalComponent {
  private consentSvc = inject(ConsentService);
  private authSvc = inject(AuthService);
  private router = inject(Router);

  /** Etat reactif local pour les retours visuels (loading / erreur). */
  protected readonly submitting = signal(false);
  protected readonly errorMessage = signal<string | null>(null);

  /** Vrai si le modal doit etre visible (delegue au service). */
  get visible(): boolean {
    return this.consentSvc.needsAcceptance && this.authSvc.isAuthenticated;
  }

  /** Version requise affichee dans le footer (transparence). */
  get version(): string {
    return this.consentSvc.requiredVersion ?? '';
  }

  /** Acceptation : appelle POST /accept et ferme le modal en cas de succes. */
  accept(): void {
    const version = this.consentSvc.requiredVersion;
    if (!version || this.submitting()) {
      return;
    }
    this.submitting.set(true);
    this.errorMessage.set(null);
    this.consentSvc.accept(version).subscribe({
      next: () => {
        this.submitting.set(false);
      },
      error: (err) => {
        this.submitting.set(false);
        const msg = (err && (err.error?.detail || err.message)) || 'Echec';
        this.errorMessage.set(String(msg));
      },
    });
  }

  /** Refus = deconnexion (pas d'acces a la plateforme sans consentement). */
  decline(): void {
    this.consentSvc.clear();
    this.authSvc.logout();
    void this.router.navigate(['/auth/login']);
  }
}
