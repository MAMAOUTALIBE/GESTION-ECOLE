import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, Observable, tap } from 'rxjs';
import { environment } from '../../../environments/environment';

/**
 * Statut du consentement renvoye par le backend (Module 5B).
 *
 * - `version` : derniere version acceptee par l'utilisateur (`null` si
 *   jamais accepte).
 * - `acceptedAt` : ISO8601 ou `null`.
 * - `needsAcceptance` : vrai si l'utilisateur doit (re)consentir avant
 *   d'utiliser la plateforme.
 * - `currentRequiredVersion` : version requise actuellement (utile pour
 *   l'envoi du POST /accept).
 */
export interface ConsentStatus {
  version: string | null;
  acceptedAt: string | null;
  needsAcceptance: boolean;
  currentRequiredVersion: string;
}

/**
 * ConsentService — Module 5B
 *
 * Recueille et propage l'etat du consentement utilisateur :
 * - `getStatus()` interroge GET /api/consent/status apres login pour
 *   savoir si l'utilisateur doit voir le modal d'acceptation.
 * - `accept(version)` envoie POST /api/consent/accept et met a jour le
 *   cache local.
 * - `status$` est un BehaviorSubject que les composants peuvent observer
 *   (le modal global ecoute pour s'ouvrir / se fermer).
 *
 * Base legale : Loi 037/AN/2016 (Guinee) + RGPD (art. 6, 7, 12-14).
 */
@Injectable({ providedIn: 'root' })
export class ConsentService {
  private http = inject(HttpClient);

  private readonly endpoint = `${environment.apiUrl}/consent`;

  private statusSubject = new BehaviorSubject<ConsentStatus | null>(null);

  /** Observable du statut consentement courant (ou null si pas encore charge). */
  status$ = this.statusSubject.asObservable();

  /** Cache synchrone de la derniere version requise renvoyee par le backend. */
  get requiredVersion(): string | null {
    return this.statusSubject.value?.currentRequiredVersion ?? null;
  }

  /** Cache synchrone : l'utilisateur doit-il (re)consentir ? */
  get needsAcceptance(): boolean {
    return Boolean(this.statusSubject.value?.needsAcceptance);
  }

  /**
   * Interroge GET /api/consent/status. A appeler apres login reussi pour
   * decider d'afficher le modal. Met a jour `status$`.
   */
  getStatus(): Observable<ConsentStatus> {
    return this.http
      .get<ConsentStatus>(`${this.endpoint}/status`)
      .pipe(tap((status) => this.statusSubject.next(status)));
  }

  /**
   * Envoie POST /api/consent/accept avec la version requise. Met a jour
   * `status$` avec le nouveau statut (needsAcceptance=false).
   */
  accept(version: string): Observable<ConsentStatus> {
    return this.http
      .post<ConsentStatus>(`${this.endpoint}/accept`, {
        consentVersion: version,
      })
      .pipe(tap((status) => this.statusSubject.next(status)));
  }

  /** Reinitialise le cache (a appeler au logout). */
  clear(): void {
    this.statusSubject.next(null);
  }
}
