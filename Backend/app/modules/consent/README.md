# Module 5B — Consentement utilisateur + mentions légales

## Base légale

* **Loi 037/AN/2016 (Guinée)** — protection des données personnelles.
* **RGPD** — Art. 6 (base légale = consentement), Art. 7 (recueil
  éclairé et tracé), Art. 12-14 (information transparente).

## Flux

```
[Login OK] ──► GET /api/consent/status
                    │
       needsAcceptance=true ?
                    │
            ┌───────┴─────────┐
            │                  │
        OUI                  NON
            │                  │
   [Modal consent]        [accès libre]
            │
       POST /api/consent/accept (consentVersion)
            │
       UserConsent (upsert) + User.consentVersion ← version
```

## Versioning

`CURRENT_CONSENT_VERSION` (`consent/enums.py`) est une date ISO. Toute
modification matérielle de la politique de confidentialité **incrémente**
cette constante ; au prochain login, le frontend recevra
``needsAcceptance=true`` et redéclenchera le modal.

## Traçabilité

Chaque acceptation enregistre :
* `consentVersion` — version acceptée.
* `acceptedAt` — date/heure UTC.
* `ip` — IP cliente résolue via `client_ip()` (compat XFF).
* `userAgent` — UA tronqué + assaini (defense in depth).

En cas de contestation, ces champs constituent la preuve de l'acte.

## Endpoints

| Méthode | URL                       | Auth | Description |
|---------|---------------------------|------|-------------|
| GET     | `/api/consent/status`     | ✅   | Statut courant |
| POST    | `/api/consent/accept`     | ✅   | Accepter version |

## Lien avec autres modules

* **5C — Audit PII** : la table `PiiAccessLog` reste la source de vérité
  pour les accès à de la PII tierce. Le consentement utilisateur est
  consigné via loguru (pas dans `PiiAccessLog` — l'enum DB n'expose
  pas `USER` comme type d'entité).
* **5D — Droit à l'oubli** : le modal mentionne explicitement les
  droits d'accès aux logs (5C) et d'oubli (5D), avec contact du DPO
  ministère.
