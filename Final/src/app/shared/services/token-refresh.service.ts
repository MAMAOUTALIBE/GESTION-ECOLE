import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Router } from '@angular/router';
import { Observable, ReplaySubject, throwError } from 'rxjs';
import { catchError, tap } from 'rxjs/operators';

import { environment } from '../../../environments/environment';
import { AuthService, AuthUser } from './auth.service';

/**
 * Réponse de `POST /api/auth/refresh` (Module 1.1).
 * Rotation refresh à chaque appel (H-10).
 */
export interface RefreshResponse {
  accessToken: string;
  refreshToken: string;
  user: AuthUser;
  /** Présent si le compte exige un challenge MFA (non géré ici). */
  mfaChallenge?: unknown;
}

/**
 * Orchestre le silent-refresh JWT :
 * - mutualise les appels concurrents (un seul HTTP refresh même si plusieurs
 *   requêtes échouent en 401 en parallèle) via un `ReplaySubject<string>`,
 * - met à jour `AuthService` avec les nouveaux jetons,
 * - en cas d'échec (refresh expiré) : clear session + redirect `/auth/login`.
 *
 * NB : on n'utilise PAS l'AuthInterceptor pour l'appel `/auth/refresh` lui-même.
 * On laisse l'AuthInterceptor ajouter le Bearer access (déjà périmé, donc inutile
 * mais inoffensif côté backend qui valide le refresh body) ; le
 * `refreshInterceptor` ignore explicitement cette URL pour éviter tout loop.
 */
@Injectable({ providedIn: 'root' })
export class TokenRefreshService {
  private http = inject(HttpClient);
  private auth = inject(AuthService);
  private router = inject(Router);

  /** True pendant qu'un refresh est en cours. */
  private refreshing = false;
  /**
   * ReplaySubject qui rejoue le dernier access token aux abonnés tardifs durant
   * la fenêtre de refresh. Recréé à chaque cycle pour éviter de rejouer un
   * vieux token sur le cycle suivant.
   */
  private refresh$ = new ReplaySubject<string>(1);

  /**
   * Déclenche (ou rejoint) un refresh en cours.
   *
   * @returns Observable qui émet le nouveau access token puis complete.
   *          Erreur si refresh expiré ou pas de refresh token stocké.
   */
  refreshToken(): Observable<string> {
    if (this.refreshing) {
      return this.refresh$.asObservable();
    }

    const stored = this.auth.refreshToken;
    if (!stored) {
      // Pas de refresh token : impossible de prolonger la session.
      return throwError(() => new Error('NO_REFRESH_TOKEN'));
    }

    this.refreshing = true;
    // Nouveau ReplaySubject pour ce cycle.
    this.refresh$ = new ReplaySubject<string>(1);

    this.http
      .post<RefreshResponse>(`${environment.apiUrl}/auth/refresh`, {
        refreshToken: stored,
      })
      .pipe(
        tap((res) => {
          this.auth.updateTokens(res.accessToken, res.refreshToken);
        }),
        catchError((err) => {
          // Refresh expiré ou révoqué : on coupe la session et on renvoie vers login.
          this.auth.clearSession();
          void this.router.navigate(['/auth/login']);
          return throwError(() => err);
        }),
      )
      .subscribe({
        next: (res) => {
          this.refresh$.next(res.accessToken);
          this.refresh$.complete();
          this.refreshing = false;
        },
        error: (err) => {
          this.refresh$.error(err);
          this.refreshing = false;
        },
      });

    return this.refresh$.asObservable();
  }
}
