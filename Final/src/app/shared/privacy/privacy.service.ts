import { Injectable, inject } from '@angular/core';
import { AuthService, UserRole } from '../services/auth.service';

/**
 * PrivacyService — Module 5A
 *
 * Couche d'anonymisation contextuelle des noms de personnes (élèves mineurs,
 * parents, enseignants) côté frontend Angular.
 *
 * Base légale et métier :
 * - Loi guinéenne 037/AN/2016 (protection des données personnelles).
 * - Principe de minimisation RGPD : ne pas révéler l'identité d'une personne
 *   sauf besoin opérationnel légitime.
 *
 * Règles d'affichage v1 (logique simplifiée par rôle) :
 * - NATIONAL_ADMIN, MINISTRY_ADMIN : nom complet partout.
 * - REGIONAL_ADMIN, INSPECTOR     : nom complet si target appartient à leur
 *   région (target.regionId === user.region.id), sinon initiales.
 *   Si target ne porte pas de regionId → nom complet (vue globale autorisée
 *   pour le scope régional).
 * - PREFECTURE_ADMIN, SUB_PREFECTURE_ADMIN : nom complet si target.regionId
 *   matche le scope régional de l'utilisateur, sinon initiales.
 * - SCHOOL_DIRECTOR, TEACHER, CENSUS_AGENT : nom complet UNIQUEMENT si
 *   target.schoolId === user.school.id (besoin opérationnel direct).
 *   Sinon initiales.
 * - Aucun utilisateur connecté → initiales (défaut le plus restrictif).
 * - Aucun target fourni → comportement par rôle : central voit tout, local
 *   voit initiales (« hors de mon scope opérationnel »).
 *
 * Important : cette couche est défensive UI. La vraie protection PII reste
 * côté backend (audit PII étendu Module 5C, droit à l'oubli Module 5D).
 */

/** Cible métier minimale décrivant la personne dont on évalue la visibilité. */
export interface PrivacyTarget {
  /** Identifiant de l'école à laquelle la personne est rattachée. */
  schoolId?: string | null;
  /** Identifiant de la région à laquelle l'école/personne est rattachée. */
  regionId?: string | null;
}

/** Personne minimale pour calculer un affichage de nom. */
export interface PrivacyPerson {
  firstName?: string | null;
  lastName?: string | null;
}

const CENTRAL_ROLES: ReadonlyArray<UserRole> = ['NATIONAL_ADMIN', 'MINISTRY_ADMIN'];
const REGIONAL_SCOPE_ROLES: ReadonlyArray<UserRole> = [
  'REGIONAL_ADMIN',
  'INSPECTOR',
  'PREFECTURE_ADMIN',
  'SUB_PREFECTURE_ADMIN',
];
const SCHOOL_SCOPE_ROLES: ReadonlyArray<UserRole> = [
  'SCHOOL_DIRECTOR',
  'TEACHER',
  'CENSUS_AGENT',
];

@Injectable({ providedIn: 'root' })
export class PrivacyService {
  private auth = inject(AuthService);

  /**
   * Détermine si l'utilisateur courant a le droit de voir le nom complet de
   * la personne associée au `target` fourni.
   */
  canSeeFullName(target?: PrivacyTarget | null): boolean {
    const user = this.auth.currentUser;
    if (!user) {
      return false;
    }

    const role = user.role;

    // Rôles centraux — nom complet partout (besoin légitime national).
    if (CENTRAL_ROLES.includes(role)) {
      return true;
    }

    // Rôles à scope régional/préfectoral.
    if (REGIONAL_SCOPE_ROLES.includes(role)) {
      const userRegionId = user.region?.id ?? null;
      const targetRegionId = target?.regionId ?? null;

      // Pas de target précis → vue globale autorisée pour le scope régional.
      if (!targetRegionId) {
        return true;
      }

      // Sans région connue côté user, on dégrade au plus prudent (initiales).
      if (!userRegionId) {
        return false;
      }

      return targetRegionId === userRegionId;
    }

    // Rôles locaux école — nom complet UNIQUEMENT si l'école matche.
    if (SCHOOL_SCOPE_ROLES.includes(role)) {
      const userSchoolId = user.school?.id ?? null;
      const targetSchoolId = target?.schoolId ?? null;

      if (!userSchoolId || !targetSchoolId) {
        return false;
      }

      return targetSchoolId === userSchoolId;
    }

    // Rôle inconnu : par sécurité, initiales.
    return false;
  }

  /**
   * Retourne le nom à afficher : « Prénom Nom » si autorisé, « P. N. » sinon.
   * Tolère firstName/lastName vides ou null.
   */
  displayName(person: PrivacyPerson | null | undefined, target?: PrivacyTarget | null): string {
    const first = (person?.firstName ?? '').trim();
    const last = (person?.lastName ?? '').trim();

    if (!first && !last) {
      return '';
    }

    if (this.canSeeFullName(target)) {
      return `${first} ${last}`.trim();
    }

    return this.initials(first, last);
  }

  /**
   * Construit les initiales redacted, format « A. D. ». Tolère un côté vide.
   */
  initials(first?: string | null, last?: string | null): string {
    const f = (first ?? '').trim();
    const l = (last ?? '').trim();

    const fi = f ? `${f.charAt(0).toLocaleUpperCase('fr-FR')}.` : '';
    const li = l ? `${l.charAt(0).toLocaleUpperCase('fr-FR')}.` : '';

    if (fi && li) {
      return `${fi} ${li}`;
    }
    return fi || li;
  }

  /**
   * Indique si, dans le contexte courant (utilisateur connecté), au moins une
   * partie des noms affichés sera caviardée. Utilisé par la bannière privacy
   * pour expliquer la redaction à l'utilisateur.
   *
   * Heuristique simple : tout rôle qui n'est pas central voit potentiellement
   * des initiales pour les personnes hors scope.
   */
  hasAnyRedaction(): boolean {
    const user = this.auth.currentUser;
    if (!user) {
      return true;
    }
    return !CENTRAL_ROLES.includes(user.role);
  }
}
