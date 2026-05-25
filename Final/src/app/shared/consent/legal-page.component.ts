import { ChangeDetectionStrategy, Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { TranslateModule } from '@ngx-translate/core';

/**
 * LegalPageComponent — Module 5B
 *
 * Page publique (sans auth requise) accessible via `/legal/privacy-policy`
 * et liee depuis le modal de consentement, le footer du layout et le menu.
 * Presente le contenu complet de la politique de confidentialite :
 * mentions legales, donnees collectees, finalites, bases legales, durees
 * de conservation, droits de la personne, contact DPO.
 *
 * Le contenu est traduit via ngx-translate (cles `legal.*`).
 */
@Component({
  selector: 'app-legal-page',
  standalone: true,
  imports: [CommonModule, TranslateModule, RouterLink],
  templateUrl: './legal-page.component.html',
  styleUrls: ['./legal-page.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LegalPageComponent {}
