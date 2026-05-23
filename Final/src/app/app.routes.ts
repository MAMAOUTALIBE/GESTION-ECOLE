import { Routes } from '@angular/router';

import { content } from './shared/routes/content.routes';

import { authen } from './shared/routes/auth.routes';
import { ContentLayout } from './shared/layouts/content-layout/content-layout';
import { AuthenticationLayout } from './shared/layouts/authentication-layout/authentication-layout';
import { authGuard } from './shared/guards/auth.guard';

export const routes: Routes = [
    { path: '', redirectTo: 'school-census/map', pathMatch: 'full' },
    { path: '', component: ContentLayout, canActivate: [authGuard], children: content },
    { path: '', component: AuthenticationLayout, children: authen },


];
