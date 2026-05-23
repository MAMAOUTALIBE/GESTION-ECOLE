import { Component, Renderer2, inject } from '@angular/core';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { NgbModule } from '@ng-bootstrap/ng-bootstrap';
import { ToastrModule, ToastrService } from 'ngx-toastr';
import { AuthService } from '../../shared/services/auth.service';

@Component({
  selector: 'app-login',
  imports: [RouterModule, FormsModule, ReactiveFormsModule, NgbModule, ToastrModule],
  providers: [{ provide: ToastrService, useClass: ToastrService }],
  templateUrl: './login.html',
  styleUrls: ['./login.scss'],
})
export class Login {
  authservice = inject(AuthService);
  private router = inject(Router);
  private route = inject(ActivatedRoute);
  private formBuilder = inject(FormBuilder);
  private renderer = inject(Renderer2);
  private toastr = inject(ToastrService);

  loginForm = this.formBuilder.group({
    email: ['admin@scolarite.gov.gn', [Validators.required, Validators.email]],
    password: ['Admin@2026', [Validators.required, Validators.minLength(8)]],
  });

  error = '';
  showPassword = false;

  constructor() {
    const bodyElement = this.renderer.selectRootElement('body', true);
    this.renderer.setAttribute(bodyElement, 'class', 'error-page1 bg-primary');
  }

  submit() {
    if (this.loginForm.invalid || this.authservice.showLoader) {
      this.loginForm.markAllAsTouched();
      return;
    }

    const { email, password } = this.loginForm.getRawValue();
    this.authservice
      .loginWithEmail(email ?? '', password ?? '')
      .then(() => {
        const returnUrl = this.route.snapshot.queryParamMap.get('returnUrl');
        this.router.navigateByUrl(returnUrl || '/school-census/map');
        this.toastr.success('Connexion réussie', 'Recensement scolaire', {
          timeOut: 3000,
          positionClass: 'toast-top-right',
        });
      })
      .catch(() => {
        this.authservice.showLoader = false;
        this.error = 'Adresse email ou mot de passe invalide.';
        this.toastr.error('Identifiants invalides', 'Recensement scolaire', {
          timeOut: 3000,
          positionClass: 'toast-top-right',
        });
      });
  }

  toggleVisibility(): void {
    this.showPassword = !this.showPassword;
  }

  ngOnDestroy(): void {
    const bodyElement = this.renderer.selectRootElement('body', true);
    this.renderer.removeAttribute(bodyElement, 'class');
  }
}
