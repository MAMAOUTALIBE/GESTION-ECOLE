# Module 18 — Portail parent (WhatsApp + USSD enrichi + page publique)

## Objectif

Ouvrir une "porte d'entrée parent" multi-canal sur GESTION-EE :

* **WhatsApp Business** — webhook entrant : un parent écrit un message libre
  ("moyenne", "presence", "bulletin") et reçoit la donnée de son enfant.
* **USSD enrichi** — extension du menu Module 14 avec deux options
  supplémentaires (bulletins récents / prochain événement). Exposé via
  `ParentPortalService.enrich_ussd_menu()` (intégration au menu Module 14 dans
  backlog 18.5).
* **Page publique HTML légère** — `/api/parent-portal/parent/{phone_hash}` :
  vue ANONYMISÉE (initiales + classe + dernière moyenne) accessible sans
  login, pour les parents qui ont un smartphone mais pas WhatsApp.

## Endpoints

| Méthode | Chemin | Auth | Rate-limit |
|---------|--------|------|------------|
| POST | `/api/parent-portal/whatsapp/webhook` | HMAC env `WHATSAPP_HMAC_SECRET` | — |
| GET  | `/api/parent-portal/overview/{phone_hash}` | PUBLIC | 20/min/hash |
| GET  | `/api/parent-portal/parent/{phone_hash}` | PUBLIC | 20/min/hash |

## Modèles

* `ParentSession(phoneNumberHash, channel, startedAt, lastActivityAt, expiresAt)`
  — expiration 30 min, bumpée à chaque hit.
* `WhatsAppMessage(direction, phoneNumber, messageId UNIQUE, body, status, ...)`
  — journal append-only, idempotency via `messageId`.

## Sécurité / anonymisation

* Le numéro de téléphone n'apparaît JAMAIS dans une URL : on expose un
  hash SHA-256 hex (64 chars) calculé après normalisation guinéenne.
* La page publique HTML n'affiche que les INITIALES (`A.D.`) + classe +
  dernière moyenne. Pas de nom complet, pas de photo, pas de DOB.
* Webhook WhatsApp : HMAC-SHA256 hex obligatoire si
  `WHATSAPP_HMAC_SECRET` est défini.
* Rate-limit Redis 20 req/min/phone-hash (anti-scrape).

## Providers

* **Mock** (défaut tests/dev) : log-only, compteur monotone.
* **CloudApiWhatsAppProvider** : squelette pour Meta Cloud API (backlog 18.2
  pour la finalisation crédentiels + retries).

Sélection via env `WHATSAPP_PROVIDER` (`mock` | `cloud_api`).

## Réutilisation

* `app.modules.sms.providers.get_provider()` — disponible si on veut fallback
  SMS en cas d'échec WhatsApp.
* `app.modules.notifications.i18n.render_template` — pour multilingue
  (utilisé en option dans les réponses, fallback FR ASCII GSM-7).
* `app.modules.census.Student.guardianPhone` + `app.modules.academics.Parent.phone`
  — résolution parent → enfant.

## Backlog

* 18.1 — NLP régex sur dates ("rentree septembre") + multilingue ff/sus/man.
* 18.2 — Finaliser CloudApiWhatsAppProvider (token rotation, retries 429).
* 18.3 — Brancher `EVENEMENT` sur un futur Module "calendrier scolaire".
* 18.4 — Index dédié `guardianPhoneHash` précalculé sur `Student`.
* 18.5 — Intégrer `enrich_ussd_menu` au handler `handle_ussd` (feature flag).
* 18.6 — Cron job de purge des `ParentSession` expirées.
