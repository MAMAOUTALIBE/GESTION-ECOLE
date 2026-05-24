# Module 11 — Diplômes signés numériquement (Ed25519)

GESTION-EE émet et vérifie des diplômes nationaux (CEPE, BEPC, CFEE)
signés cryptographiquement, vérifiables PUBLIQUEMENT par n'importe qui
via un QR code. L'objectif business : couper court à la fraude
documentaire à grande échelle (faux diplômes circulant sur le marché du
travail africain).

## Architecture

```
   ┌──────────────────────────────────────────────────────────────┐
   │                  POST /api/diplomas (admin)                  │
   │  -> canonicalize(payload) -> SHA-256 -> Ed25519 sign        │
   │  -> persist (status=ISSUED, signature, hash, fingerprint)   │
   │  -> AuditLog                                                 │
   └──────────────────────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────────┐
   │         GET /api/diplomas/verify/{serial} (PUBLIC)          │
   │  -> renvoie payload + signature + status (VALID|REVOKED)    │
   │  -> jamais d'ID interne, jamais de date de naissance         │
   └──────────────────────────────────────────────────────────────┘
```

## Fichiers

| Fichier | Responsabilité |
|---|---|
| `enums.py` | `DiplomaType` (CEPE/BEPC/CFEE), `DiplomaStatus` (DRAFT/ISSUED/REVOKED) |
| `models.py` | Table `Diploma` — signature, hash, fingerprint, audit |
| `serial.py` | `generate_serial("CEPE", 2026)` → `"CEPE-2026-3F2A91BC"` |
| `crypto.py` | Ed25519 sign/verify, canonicalisation RFC 8785 simplifiée |
| `service.py` | `DiplomaService.issue_diploma / verify_diploma / revoke_diploma / list_diplomas / get_diploma_pdf` |
| `router.py` | 5 endpoints : POST, GET (list), GET /verify (PUBLIC), GET /pdf, POST /revoke |
| `schemas.py` | Pydantic — distinction CLAIRE entre `DiplomaRead` (interne) et `DiplomaVerification` (public) |
| `templates/diploma.html` | Page web statique de vérification (sans framework, copiable sur nginx) |

## Pourquoi Ed25519 plutôt que RSA ?

* RFC 8410 — courbe edwards25519, sécurité 128-bit.
* Signature ~64 bytes (RSA-2048 → 256 bytes) → tient confortablement dans
  un QR code de niveau M.
* Clé publique 32 bytes → distribuable hors-ligne sans difficulté.
* Signatures déterministes → tests reproductibles, pas de RNG à la vérif.
* `cryptography.hazmat.primitives.asymmetric.ed25519` — déjà dans `deps`,
  validé NIST, pas de paramètre à choisir.

## Gestion de la clé privée

* **Production** : env var `DIPLOMA_SIGNING_KEY_PEM` contenant la clé
  Ed25519 au format PKCS8 PEM. Pour générer :

  ```bash
  python -c "
  from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
  from cryptography.hazmat.primitives import serialization
  k = Ed25519PrivateKey.generate()
  print(k.private_bytes(
      encoding=serialization.Encoding.PEM,
      format=serialization.PrivateFormat.PKCS8,
      encryption_algorithm=serialization.NoEncryption(),
  ).decode())"
  ```

  La clé DOIT vivre dans un coffre (Vault, AWS Secrets Manager, HSM).
  L'app ne la lit qu'au démarrage et la garde en cache process-local.

* **Dev / tests** : si l'env var est absente, le module génère un keypair
  éphémère et log un WARN clair. Cohérent pour la durée du process.

## Rotation de clé (Module 11.x)

La colonne `Diploma.publicKeyFingerprint` est dénormalisée
volontairement : on sait toujours quelle clé a signé un diplôme passé.
La rotation consistera à :

1. Générer un nouveau keypair, déployer la nouvelle clé en env.
2. Publier la nouvelle clé publique sur le site officiel ; conserver
   l'ancienne dans une liste "clés actives historiques".
3. Le code de vérification choisira automatiquement la bonne clé via
   `publicKeyFingerprint`.

## Sécurité publique (vérification offline)

Un vérificateur externe (recruteur, université étrangère, app auditrice)
peut confirmer un diplôme **sans appeler notre API** :

1. Scanner le QR du diplôme → URL `{public_base}/diplomas/{serial}`.
2. Récupérer la clé publique officielle du Ministère (publiée sur un
   domaine .gouv en `.pem`).
3. À partir du payload affiché par la page :

   ```python
   from app.modules.diplomas.crypto import (
       canonicalize_payload, verify_signature,
   )
   import json, base64, hashlib

   payload = json.loads("...")          # le dict retourné par /verify
   signature_b64 = "..."                # idem
   public_key_pem = open("ministere-mfen.pem").read()

   ok = verify_signature(payload, signature_b64, public_key_pem)
   assert ok, "Diplôme falsifié"

   # Optionnel : recomputer le hash et comparer
   expected = hashlib.sha256(canonicalize_payload(payload)).hexdigest()
   assert expected == response["payloadSha256"]
   ```

   La vérification ne dépend QUE de :
   * la clé publique (32 bytes, distribuable),
   * le payload (renvoyé par l'API ou recomposé depuis le PDF),
   * la signature stockée.

   Aucun accès réseau à notre infrastructure n'est nécessaire pour
   confirmer l'authenticité — c'est la propriété centrale qui rend
   l'anti-fraude crédible.

## Endpoints

| Méthode | URL | RBAC | Description |
|---|---|---|---|
| `POST` | `/api/diplomas` | `MINISTRY_ADMIN+` | Émet un nouveau diplôme |
| `GET` | `/api/diplomas` | `SCHOOL_DIRECTOR+` (scope) | Liste avec scope territorial |
| `GET` | `/api/diplomas/verify/{serial}` | **PUBLIC** | Vérification universelle |
| `GET` | `/api/diplomas/{serial}/pdf` | `SCHOOL_DIRECTOR+` (owner) | Télécharge le PDF (MVP : indispo) |
| `POST` | `/api/diplomas/{serial}/revoke` | `NATIONAL_ADMIN` | Révocation |

## Backlog Module 11.x

* PDF officiel signé visuellement (logo Ministère + QR + signature visible) — WeasyPrint + upload S3.
* Endpoint `GET /api/diplomas/public-key` pour distribuer la clé publique.
* Rotation de clé multi-fingerprint (vérification d'historique).
* Vérification offline via SMS USSD (ex : `*999*SERIAL#` → SMS retour).
* Limitation de débit (rate-limit) sur `/verify` pour éviter le scrapping
  d'identités d'élèves (anti-énumération).
