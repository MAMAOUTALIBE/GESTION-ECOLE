# Module 8 — Predictions ML (détection précoce du décrochage scolaire)

## Objectif

Pour chaque élève, calculer une probabilité (0..1) d'abandon scolaire dans
les 90 jours et alerter le directeur. Cible : **sauver 50K élèves par an**
en intervenant tôt (notification, rendez-vous parents, plan de
remédiation).

## Pipeline

```
Student ──▶ extract_features (6 features SQL) ──▶ DropoutModel (sklearn
        LogisticRegression + StandardScaler) ──▶ (proba, riskLevel) ──▶
        persist DropoutPrediction
```

### Les 6 features

| Feature | Source | Default si absent |
|---|---|---|
| `attendance_rate_90d` | AttendanceRecord 90j | 0.85 |
| `attendance_rate_30d` | AttendanceRecord 30j | 0.85 |
| `grade_avg_last_period` | Grade × AcademicPeriod | 10.0 |
| `grade_trend` | Delta moyenne 2 dernières périodes | 0.0 |
| `incidents_count_180d` | Incident 180j | 0.0 |
| `late_count_30d` | AttendanceRecord LATE 30j | 0.0 |

### Pourquoi Logistic Regression ?

* **Interprétable** : on peut expliquer le score à un parent ("le facteur
  principal est l'absentéisme").
* **Rapide** : < 1ms par élève à l'inférence, ~1s à entraîner sur 5k
  samples.
* **Probabilités quasi-calibrées** par défaut (pas besoin de Platt scaling
  pour le MVP).

XGBoost / CatBoost / réseaux de neurones / calibration isotonique →
Module 8.1, quand on aura des **données labellisées réelles** (aujourd'hui
on bootstrap avec un dataset synthétique).

## Endpoints REST

| Endpoint | Méthode | RBAC | Description |
|---|---|---|---|
| `/students/{id}/predict` | POST | ≥ SCHOOL_DIRECTOR | Score + persist 1 élève |
| `/schools/{id}/batch-predict` | POST | ≥ SCHOOL_DIRECTOR | 202 + count |
| `/schools/{id}/at-risk` | GET | ≥ SCHOOL_DIRECTOR | Liste paginée |
| `/model/train` | POST | NATIONAL_ADMIN | Entraîne un nouveau modèle |
| `/model/info` | GET | ≥ SCHOOL_DIRECTOR | Métadonnées du modèle courant |

## Stockage

* `DropoutPrediction` : 1 row par élève par calcul (= 1 par mois en
  cadence cible). Avec 3M élèves on est à ~3M rows/mois → partitionnement
  prévu en Module 8.1 si dépassement.
* `DropoutModelMetadata` : registry minimaliste (~1 row par entraînement).

## Synthetic training set (à clarifier pour ops)

`generate_synthetic_training_set` produit 5000 lignes selon des règles
déterministes + 5% de bruit. **Ce n'est PAS un dataset réel** — c'est un
bootstrap pour que le pipeline soit testable en CI. La règle :

```
abandon = 1 si :
    (attendance_30d < 0.60 ET grade < 8)
    OU (incidents > 3 ET attendance_90d < 0.75)
    OU (late > 10 ET trend < -3)
```

Quand le ministère fournira un dataset labellisé (cohorte 2024-2026), on
remplacera `generate_synthetic_training_set` par un loader DB sans toucher
au reste du pipeline.

## Limites MVP & dette technique (Module 8.1)

* **`/tmp/dropout_model.joblib`** : artefact local single-instance. En
  multi-worker production, il faut S3 + signed URL. Cf. issue 8.1-MLflow.
* **Cache process-level** : si on entraîne un nouveau modèle on doit
  redémarrer tous les workers (ou implémenter un signal Redis pubsub).
* **Pas de monitoring drift** : on ne détecte pas si la distribution des
  features change. À ajouter avec Evidently AI ou un dashboard custom.
* **Pas de fairness audit** : on devrait vérifier que le modèle n'est pas
  biaisé par genre / région. `featuresSnapshot` contient déjà les inputs
  nécessaires pour faire ce calcul a posteriori.
* **Batch predict synchrone** : pour > 1000 élèves on doit déléguer à
  `predict.batch_predict_school` Celery task (existante mais pas branchée
  par défaut sur l'endpoint pour rester simple).

## Tests

`tests/integration/test_predictions_module8.py` couvre :
* extraction features avec / sans données
* shape + qualité du training set synthétique
* accuracy raisonnable (>0.7) à l'entraînement
* range proba + seuils riskLevel
* persistance des prédictions
* RBAC (SCHOOL_DIRECTOR, NATIONAL_ADMIN)
* gestion 404 pour student inconnu

## Ops

Pour bootstrap un nouvel environnement :

```bash
# 1. Migrations
alembic upgrade head

# 2. Entraîner le premier modèle (POST sur l'API ou directement en Python)
curl -X POST $API/api/predictions/model/train \
    -H "Authorization: Bearer $NATIONAL_ADMIN_TOKEN"

# 3. Vérifier
curl $API/api/predictions/model/info \
    -H "Authorization: Bearer $DIRECTOR_TOKEN"
```
