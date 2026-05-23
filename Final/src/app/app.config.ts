import { ApplicationConfig, importProvidersFrom, provideZonelessChangeDetection } from '@angular/core';
import { provideRouter, RouterOutlet } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
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
export const appConfig: ApplicationConfig = {
  providers: [
    provideZonelessChangeDetection(),
    provideRouter(routes),
    provideHttpClient(withInterceptors([authInterceptor])),
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

