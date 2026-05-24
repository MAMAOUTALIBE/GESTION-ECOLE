# Module 14 — SMS / USSD gateway

## Pourquoi ?

La cible business GESTION-EE est l'éducation **rurale guinéenne** : la
majorité des parents n'ont pas de smartphone, encore moins de connexion
3G fiable. Le canal SMS/USSD est le **seul moyen** d'atteindre ces
familles depuis la plateforme.

Deux usages :

* **Outbound SMS** : notifications "bulletin disponible", "votre enfant
  est absent aujourd'hui", "diplôme CEPE émis pour…". Envoyé via un
  **provider abstrait** (Twilio en prod, Mock en dev). Templating i18n
  réutilise le moteur du Module 6 (fr / ff / sus / man).
* **USSD inbound** : menu interactif déclenché par `*999*CODE#` — le
  parent compose, le réseau pousse un webhook chez nous, on renvoie une
  string `CON ...` (continuer) ou `END ...` (terminer). Trois options
  MVP : **moyenne de l'enfant, présence cette semaine, vérifier un
  diplôme**.

Le tout sans crédentiels externes en dev (MockProvider par défaut), ce
qui permet de jouer **100% des tests** sans compte Twilio.

## Architecture

```
                 ┌─────────────────────────────────────────────┐
                 │                FastAPI router               │
                 │  POST /api/sms/send          (RBAC ≥ DIR)   │
                 │  POST /api/sms/send-templated(RBAC ≥ DIR)   │
                 │  GET  /api/sms/messages      (RBAC ≥ DIR)   │
                 │  POST /api/sms/ussd/callback (PUBLIC +HMAC) │
                 │  POST /api/sms/delivery-report (PUBLIC)     │
                 │  GET  /api/sms/stats         (RBAC ≥ REG)   │
                 └────────────┬─────────────────────┬──────────┘
                              │                     │
                ┌─────────────▼─────────────┐   ┌───▼───────────┐
                │       SmsService          │   │  handle_ussd  │
                │  send_sms / send_templated│   │ (state machine)│
                │  list_messages / status   │   └───┬───────────┘
                └────────┬─────────┬────────┘       │
                         │         │                ▼
                         │         │     SELECT Student.guardianPhone
                         │         │     SELECT ReportCard.average
                         │         │     SELECT Diploma WHERE serial=…
                         │         │
                ┌────────▼──┐  ┌───▼─────────┐
                │ providers │  │ render_template (Module 6 i18n)
                │ MockProv. │  │ → langue préférée du destinataire
                │ TwilioProv│  └────────────────┘
                └───────────┘
```

## Provider abstraction

`app/modules/sms/providers.py` expose un `SmsProvider` Protocol :

```python
class SmsProvider(Protocol):
    name: str
    async def send(self, to: str, body: str) -> SendResult: ...
```

Deux implémentations :

* **MockProvider** — log seulement, statut SENT immédiat, `provider_id`
  monotone (`mock-00000001`, `mock-00000002`, …). Utilisé en dev et dans
  100% des tests. Aucun appel réseau.
* **TwilioProvider** — appel HTTP direct à `https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json`
  via `httpx` + HTTP Basic Auth `(account_sid, auth_token)`. **Pas de
  dépendance** sur le SDK `twilio` — on reste léger.

Sélection par variable d'environnement `SMS_PROVIDER` (`twilio` ou
`mock`, défaut `mock`). Helpers `reset_provider_cache()` / `set_provider()`
pour les tests.

## Menu USSD

```
Bienvenue GESTION-EE
1. Moyenne de mon enfant
2. Presence cette semaine
3. Verifier diplome (CEPE/BEPC)
0. Quitter
```

Le `text` envoyé par l'opérateur est l'historique CONCATÉNÉ avec `*`
comme séparateur (convention Africa's Talking / Orange) :

| `text`                | Interprétation                                   |
| --------------------- | ------------------------------------------------ |
| `""`                  | Menu d'accueil                                   |
| `"1"`                 | Option 1 (moyenne), recherche par `guardianPhone`|
| `"3"`                 | Option 3 (diplôme), demande le serial            |
| `"3*CEPE-2026-ABC..."`| Option 3 + serial → renvoie le statut            |
| `"9"` (option inexis.)| Re-affiche le menu avec message d'erreur         |

### Identification du parent

Le numéro USSD (`phoneNumber`) est normalisé via
`normalize_phone_guinea` (Module 2) puis comparé à `Student.guardianPhone`.
Si plusieurs enfants partagent le numéro, on demande un code élève
(`Student.uniqueCode`).

## Anti-spam

* **USSD** : 5 sessions / minute / numéro via Redis fixed-window
  (`RateLimiter` partagé avec auth / opendata). Au-delà, on renvoie une
  string `END Trop de tentatives. Reessayez dans 1 minute.` avec un
  HTTP 200 (l'opérateur attend toujours du 200).
* **SMS outbound** : pas de rate-limit applicatif côté serveur — c'est
  le provider qui facture, et le RBAC `≥ SCHOOL_DIRECTOR` est déjà une
  barrière forte.

## Signature HMAC (optionnelle)

Si la variable d'environnement `USSD_HMAC_SECRET` est définie, le router
vérifie que le header `X-USSD-Signature` correspond à
`HMAC-SHA256(secret, raw_body)` (comparaison en temps constant via
`hmac.compare_digest`). Sinon, on accepte sans contrôle — compatible
avec les opérateurs qui ne signent pas leurs callbacks (encore le cas en
Guinée en 2026).

## Schéma de base

Deux tables (migration `0019_sms_ussd`) :

* `SmsMessage` — un par envoi. `direction` (OUTBOUND/INBOUND), `to`,
  `from`, `body`, `status` (PENDING/SENT/DELIVERED/FAILED), `providerId`
  (pour réconcilier les callbacks), `errorMessage`, `actorId`,
  `deliveredAt`, `createdAt`. Indexes sur `to`, `status`, `createdAt`,
  `providerId`.
* `UssdSession` — un par session. `sessionId` UNIQUE, `phoneNumber`,
  `serviceCode`, `lastInput`, `currentStep` (state machine), `completedAt`.
  Indexes sur `phoneNumber`, `createdAt`.

Pas de FK vers `User` ou `Student` — un SMS peut être envoyé à un numéro
qui n'a pas (encore) de compte ; une session USSD peut venir d'un numéro
inconnu. La traçabilité reste assurée via `to` / `phoneNumber`.

## Sécurité

* `POST /send` et `/send-templated` exigent `SCHOOL_DIRECTOR+` (RBAC).
* `GET /messages` même contrainte.
* `GET /stats` exige `REGIONAL_ADMIN+`.
* `POST /ussd/callback` est **PUBLIC** mais protégé par :
  * Signature HMAC optionnelle (cf. ci-dessus).
  * Rate limit 5/min/numéro.
  * Pas de leak d'identifiants internes dans les réponses USSD.
* `POST /delivery-report` est **PUBLIC** (webhook provider). À durcir
  avec une auth provider-spécifique (signature Twilio) en backlog 14.1.

## Tests

`tests/integration/test_sms_module14.py` — 13 tests :

1. `test_mock_provider_send_persists_message` — provider crée la ligne SENT.
2. `test_send_endpoint_requires_director` — RBAC 403 / 202.
3. `test_send_templated_uses_user_language` — fr vs ff.
4. `test_ussd_callback_returns_welcome_menu_on_empty_text` — menu CON.
5. `test_ussd_option_1_returns_average_for_known_student` — moyenne 14.75.
6. `test_ussd_option_1_unknown_student_returns_helpful_error` — END d'aide.
7. `test_ussd_option_3_returns_diploma_status` — intègre Module 11.
8. `test_ussd_session_persisted_and_resumed` — étape avance.
9. `test_ussd_rate_limit_5_per_minute_per_phone` — 6e refusée.
10. `test_ussd_unknown_phone_returns_message` — END d'aide.
11. `test_ussd_invalid_option_returns_menu_again` — option 9 → menu.
12. `test_ussd_signature_validation_when_secret_set` — HMAC valide / fausse.
13. `test_sms_status_updated_on_provider_callback` — SENT → DELIVERED.

## Backlog 14.1 (reporté)

* **Auth signature Twilio** sur `/delivery-report` (le webhook actuel
  est public — un attaquant pourrait spammer des updates DELIVERED).
* **Table de liaison `User.phone` ↔ `SmsRecipient`** — pour rompre
  l'hypothèse "email = numéro" utilisée dans `send_templated`.
* **Orange Guinée provider** — l'API a une auth OAuth2 différente de
  Twilio ; il faut un `OrangeProvider` séparé.
* **WhatsApp Cloud API** — extension naturelle (Module 14.2).
* **Métriques Prometheus** — compteurs `sms_sent_total{status,provider}`.
* **Backpressure** — queue Celery pour les bulks > 100 SMS.
* **Re-try exponential** sur échec provider (3 essais @ 1/5/30s).
* **Réception SMS entrants** (INBOUND direction) — un parent répond
  "STOP" → on dé-inscrit du programme.
