import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { EnvironmentProviders, importProvidersFrom } from '@angular/core';
import { TranslateLoader, TranslateModule } from '@ngx-translate/core';
import { TranslateHttpLoader } from '@ngx-translate/http-loader';

/**
 * Factory utilisée par `@ngx-translate` pour charger les fichiers JSON
 * statiques depuis `/assets/i18n/{lang}.json` (servis par Angular CLI).
 */
export function createTranslateLoader(http: HttpClient): TranslateLoader {
  return new TranslateHttpLoader(http, './assets/i18n/', '.json');
}

/**
 * Providers à inclure dans `appConfig.providers` pour activer la traduction.
 * Ne provisionne PAS `HttpClient` (déjà fait dans `app.config.ts`), juste le
 * module Translate avec son loader.
 */
export function provideI18n(): EnvironmentProviders {
  return importProvidersFrom(
    TranslateModule.forRoot({
      defaultLanguage: 'fr',
      loader: {
        provide: TranslateLoader,
        useFactory: createTranslateLoader,
        deps: [HttpClient],
      },
    }),
  );
}

// Ré-exports pour les composants standalone qui veulent juste les directives.
export { TranslateModule, TranslateLoader };
// Évite l'avertissement linter "unused import" sur withInterceptors/provideHttpClient
// si ce fichier sert plus tard à provisionner HttpClient localement.
void provideHttpClient;
void withInterceptors;
