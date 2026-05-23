import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { AuthService, UserRole } from '../services/auth.service';

export const roleGuard: CanActivateFn = (route) => {
  const authService = inject(AuthService);
  const router = inject(Router);
  const roles = (route.data?.['roles'] ?? []) as UserRole[];

  if (authService.hasAnyRole(roles)) {
    return true;
  }

  return router.createUrlTree(['/dashboards/dashboard-1']);
};
