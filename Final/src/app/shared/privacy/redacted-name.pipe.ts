import { Pipe, PipeTransform, inject } from '@angular/core';
import { PrivacyService, PrivacyPerson, PrivacyTarget } from './privacy.service';

/**
 * RedactedNamePipe — Module 5A
 *
 * Pipe standalone qui délègue à PrivacyService la décision d'affichage
 * « nom complet » vs « initiales ». À utiliser dans tout template qui
 * affiche un nom de personne (élève, parent, enseignant) :
 *
 *   {{ student | redactedName: { schoolId: student.school.id } }}
 *
 * Marqué impur volontairement car la session utilisateur peut changer
 * (login/logout) sans que la référence à `person` ne change.
 */
@Pipe({ name: 'redactedName', standalone: true, pure: false })
export class RedactedNamePipe implements PipeTransform {
  private privacy = inject(PrivacyService);

  transform(person: PrivacyPerson | null | undefined, target?: PrivacyTarget | null): string {
    return this.privacy.displayName(person, target);
  }
}
