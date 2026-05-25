import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { throwError } from 'rxjs';
import { catchError, switchMap } from 'rxjs/operators';

import { environment } from '../../../environments/environment';
import { TokenRefreshService } from '../services/token-refresh.service';

/**
 * Intercepteur silent-refresh JWT.
 *
 * Sur 401 d'un endpoint API protégé : déclenche un refresh (mutualisé via
 * TokenRefreshService) puis rejoue UNE FOIS la requête originale avec le
 * nouveau Bearer.
 *
 * Cas explicitement ignorés (pas d'interception, on laisse remonter l'erreur) :
 * - URL `/auth/login` : un échec est légitime (mauvais identifiants).
 * - URL `/auth/refresh` : éviter tout loop infini si le refresh lui-même
 *   retourne 401 (le redirect `/auth/login` est géré dans TokenRefreshService).
 * - URLs hors API (`environment.apiUrl`) : pas concernées par notre auth.
 *
 * Doit être enregistré APRÈS `authInterceptor` pour que la requête rejouée
 * passe à nouveau par lui et reçoive le nouveau Bearer.
 */
export const refreshInterceptor: HttpInterceptorFn = (request, next) => {
  // Injection au top-level (contexte d'injection garanti) ; on n'utilise la
  // référence qu'à l'intérieur du catchError plus bas.
  const tokenRefresh = inject(TokenRefreshService);

  return next(request).pipe(
    catchError((err: unknown) => {
      if (!(err instanceof HttpErrorResponse) || err.status !== 401) {
        return throwError(() => err);
      }

      const isApi = request.url.startsWith(environment.apiUrl);
      const isLogin = request.url.includes('/auth/login');
      const isRefresh = request.url.includes('/auth/refresh');

      if (!isApi || isLogin || isRefresh) {
        return throwError(() => err);
      }

      return tokenRefresh.refreshToken().pipe(
        switchMap((newToken) => {
          // Rejoue la requête originale avec le nouveau Bearer (sans dépendre
          // de l'authInterceptor pour ce retry — on force la valeur).
          const retried = request.clone({
            setHeaders: { Authorization: `Bearer ${newToken}` },
          });
          return next(retried);
        }),
        catchError(() => {
          // Le refresh a échoué (TokenRefreshService a déjà clear+redirect).
          // On propage l'erreur 401 originale pour que l'appelant la voie.
          return throwError(() => err);
        }),
      );
    }),
  );
};
