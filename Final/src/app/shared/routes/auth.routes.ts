import { Routes } from '@angular/router';

export const authen: Routes = [
    {
        path: 'auth/login',
        loadComponent: () => import('../../authentication/login/login').then((m) => m.Login),
    },
]
