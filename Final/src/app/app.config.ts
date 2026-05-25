import { ApplicationConfig, importProvidersFrom, provideZonelessChangeDetection, isDevMode } from '@angular/core';
import { provideRouter, RouterOutlet } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { provideServiceWorker } from '@angular/service-worker';
import { routes } from './app.routes';
import { NgCircleProgressModule } from 'ng-circle-progress';
import { AngularFireModule } from '@angular/fire/compat';
import { AngularFireAuthModule } from '@angular/fire/compat/auth';
import { AngularFireDatabaseModule } from '@angular/fire/compat/database';
import { AngularFirestoreModule } from '@angular/fire/compat/firestore';
import { FlatpickrDefaults } from 'angularx-flatpickr';
import { ColorPickerDirective, ColorPickerService } from 'ngx-color-picker';
import { environment } from '../environments/environment';
import { ToastNoAnimationModule } from 'ngx-toastr';
import { provideCharts, withDefaultRegisterables } from 'ng2-charts';
import { MaterialModuleModule } from './material-module/material-module.module';
import { MatTableDataSource, MatTableModule } from '@angular/material/table';
import { NgbModule } from '@ng-bootstrap/ng-bootstrap';
import { OverlayscrollbarsModule } from 'overlayscrollbars-ngx';
import { provideAnimations } from '@angular/platform-browser/animations';
import { provideSweetAlert2 } from '@sweetalert2/ngx-sweetalert2';
import { QuillModule } from 'ngx-quill'
import { authInterceptor } from './shared/interceptors/auth.interceptor';
import { refreshInterceptor } from './shared/interceptors/refresh.interceptor';
import { provideI18n } from './shared/i18n/i18n.providers';
export const appConfig: ApplicationConfig = {
  providers: [
    provideZonelessChangeDetection(),
    provideRouter(routes),
    // L'ordre est critique : `authInterceptor` ajoute d'abord le Bearer,
    // puis `refreshInterceptor` intercepte les 401 pour silent-refresh JWT.
    provideHttpClient(withInterceptors([authInterceptor, refreshInterceptor])),
    // Module 20 — i18n (ngx-translate, fr par défaut, 4 langues).
    provideI18n(),
    provideCharts(withDefaultRegisterables()),
    RouterOutlet,
    ColorPickerDirective,
    MaterialModuleModule,
    AngularFireAuthModule,
    AngularFirestoreModule,
    AngularFireDatabaseModule,
    AngularFireModule,
    ColorPickerService,
    MatTableModule, MatTableDataSource,
    provideAnimations(),
    provideSweetAlert2({
    // Optional configuration
    fireOnInit: false,
    dismissOnDestroy: true,
    }),
    // Module 16 PWA: register the Angular Service Worker only in production
    // builds. In dev mode (`ng serve`) it stays disabled so hot-reload is not
    // shadowed by cached responses.
    provideServiceWorker('ngsw-worker.js', {
      enabled: !isDevMode(),
      registrationStrategy: 'registerWhenStable:30000',
    }),
    importProvidersFrom(

      FlatpickrDefaults,
      OverlayscrollbarsModule,
      NgbModule,
       QuillModule.forRoot(),
      NgCircleProgressModule.forRoot({}),
      ToastNoAnimationModule.forRoot({
        timeOut: 15000, // 15 seconds
        closeButton: true,
        progressBar: true,
      }),
      AngularFireModule.initializeApp(environment.firebase),

    ),

  ],
};

