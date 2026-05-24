import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { GeLang, LanguageService } from '../../i18n/language.service';

/**
 * Select compact qui affiche les 4 langues GESTION-EE.
 * Standalone, à brancher dans le header existant.
 */
@Component({
  selector: 'app-language-switcher',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './language-switcher.component.html',
  styleUrls: ['./language-switcher.component.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LanguageSwitcherComponent {
  private readonly language = inject(LanguageService);

  readonly currentLang = this.language.currentLang;
  readonly options = this.language.available;

  onChange(lang: GeLang): void {
    this.language.setLang(lang);
  }
}
