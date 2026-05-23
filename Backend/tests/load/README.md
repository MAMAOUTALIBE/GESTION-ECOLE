# Tests de charge — Locust

Trois scenarios sont definis dans `locustfile.py` :

| Scenario         | Endpoint                  | Poids | Objectif                          |
|------------------|---------------------------|-------|-----------------------------------|
| `LoginScenario`  | `POST /api/auth/login`    | 1     | Stresser le hashing Argon2 + JWT  |
| `ReadScenario`   | `GET  /api/schools` + `/me` | 3     | Charge de lecture (cache, DB)     |
| `WriteScenario`  | `POST /api/attendance/scan` | 1     | Charge d'ecriture (scan QR mobile)|

## Lancement

### 1. Demarrer l'API

```bash
# depuis Backend/
uv run uvicorn app.main:app --reload --port 8000
```

### 2. Mode interactif (UI web)

```bash
# depuis Backend/
.venv/bin/locust -f tests/load/locustfile.py --host http://localhost:8000
```

Puis ouvrir [http://localhost:8089](http://localhost:8089) et entrer :

* **Number of users** : `100`
* **Spawn rate**      : `10` /s
* **Run time**        : `2m`

### 3. Mode headless (CI / production check)

```bash
mkdir -p reports
.venv/bin/locust \
  -f tests/load/locustfile.py \
  --host http://localhost:8000 \
  --headless \
  --users 100 \
  --spawn-rate 10 \
  --run-time 2m \
  --csv reports/locust
```

Les CSV sont generes dans `reports/locust_*.csv` (stats, failures, history).

## Credentials de test

Par defaut, le scenario s'authentifie avec :

```
LOCUST_EMAIL=admin@scolarite.gov.gn
LOCUST_PASSWORD=Admin@2026
```

Override via env si vos seeds locales different :

```bash
LOCUST_EMAIL=director@ee.gov.gn LOCUST_PASSWORD='hunter2!' \
  .venv/bin/locust -f tests/load/locustfile.py --host http://localhost:8000
```

## Seuils SLO de reference (a affiner Module 14+)

* p95 `/api/auth/login`    < 800 ms (Argon2 dominant)
* p95 `/api/schools`       < 200 ms (cacheable)
* p95 `/api/attendance/scan` < 300 ms (write critical path mobile)
* Failure rate < 0.1 % sur 2 minutes a 100 users

Note : ces seuils sont **indicatifs** pour le Module 0. La vraie cible
de scalabilite (10 000 ecoles, 4M eleves) sera dimensionnee au Module 14
quand le caching Redis et les indexes auront ete profiles.
