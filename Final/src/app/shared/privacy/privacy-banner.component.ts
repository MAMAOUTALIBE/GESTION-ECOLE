import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule } from '@ngx-translate/core';
import { PrivacyService } from './privacy.service';

/**
 * PrivacyBannerComponent — Module 5A
 *
 * Petite bannière inline qui s'affiche au-dessus des listes contenant des
 * noms anonymisés. Rappelle à l'utilisateur que la redaction est faite en
 * application de la loi 037/AN/2016 (protection des données personnelles).
 *
 * Visible UNIQUEMENT si `PrivacyService.hasAnyRedaction()` retourne true
 * pour le contexte courant. Pour un NATIONAL_ADMIN/MINISTRY_ADMIN, la
 * bannière reste invisible.
 *
 * Design : additif (alert Bootstrap léger), ne modifie pas Spruko.
 */
@Component({
  selector: 'app-privacy-banner',
  standalone: true,
  imports: [CommonModule, TranslateModule],
  templateUrl: './privacy-banner.component.html',
  styleUrls: ['./privacy-banner.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PrivacyBannerComponent {
  private privacy = inject(PrivacyService);

  /** Vrai si au moins une partie des noms affichés est caviardée. */
  get visible(): boolean {
    return this.privacy.hasAnyRedaction();
  }
}
