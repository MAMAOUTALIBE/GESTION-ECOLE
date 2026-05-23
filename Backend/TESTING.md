# Tests — GESTION-EE Backend

Ce document decrit l'infrastructure de tests posee au **Module 0**. Tous les
modules suivants (auth durci, RBAC, attendance QR, etc.) viendront ajouter
leurs propres tests sans avoir a re-bidouiller la plomberie ici.

## Vue d'ensemble

Trois suites de tests cohabitent :

| Suite                       | Repertoire                | Quoi                                                     |
|-----------------------------|---------------------------|----------------------------------------------------------|
| **Unit / contrats**         | `tests/test_*.py`         | Tests rapides (~2 s), pas de DB. ~240 tests existants.   |
| **Integration**             | `tests/integration/`      | Vraie DB Postgres + Redis (DB 15). ~secondes par test.   |
| **Charge (Locust)**         | `tests/load/`             | Scenarios HTTP / latence. Hors CI standard.              |

Toute la stack est pilotee par `pytest`. Les fixtures lourdes (engine,
session DB, Redis client, HTTP client) sont **session-scoped**, partagees
entre tous les tests d'une meme run pour rester rapide.

## Lancer les tests

```bash
cd Backend

# tout d'un coup
make test

# uniquement les 240 tests unitaires/contract (sans DB)
make test-unit

# uniquement la suite d'integration (DB + Redis requis)
make test-integration

# en parallele (xdist) — utile quand la suite grossit
make test-integration-parallel

# couverture (rapport texte)
make coverage

# couverture HTML (ouvre htmlcov/index.html dans le navigateur)
make coverage-html
```

Sans `make`, les memes commandes brutes sont :

```bash
.venv/bin/pytest tests/                       # tout
.venv/bin/pytest tests/integration/ -q        # integration
.venv/bin/pytest tests/ -q -n auto            # parallele (pytest-xdist)
.venv/bin/pytest tests/ --cov=app             # couverture
```

## Pre-requis pour la suite d'integration

1. **Postgres** : un serveur accessible (par defaut sur `127.0.0.1:5433`,
   user `gestionee`, password `gestionee_dev_2026`).
2. **Redis** : un serveur accessible sur `localhost:6379`. La DB 15 est
   utilisee, jamais touchee par dev / Celery.
3. **Base de test cree** : `make db-test-create` (idempotent).

Pour repartir d'une DB vierge (utile si une fixture a corrompu le schema) :

```bash
make db-test-recreate
```

## Ajouter un nouveau test d'integration — recette

```python
# tests/integration/test_my_module.py
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.enums import UserRole
from tests.integration import factories


@pytest.mark.integration
async def test_create_school_requires_admin(
    db_session: AsyncSession,
    client: AsyncClient,
    auth_headers,
) -> None:
    # 1. Bind les factories sur la session DU TEST (essentiel pour
    #    l'isolation transactionnelle).
    factories.bind(db_session)

    # 2. Cree les pre-requis territoriaux.
    tree = await factories.make_territorial_tree()

    # 3. Recupere des credentials pour un role precis.
    teacher_headers = await auth_headers(UserRole.TEACHER)
    admin_headers = await auth_headers(UserRole.NATIONAL_ADMIN)

    # 4. Verifie qu'un teacher ne peut pas creer d'ecole.
    payload = {
        "name": "Nouvelle Ecole",
        "code": "SCH-XXX",
        "regionId": tree["region"].id,
    }
    r = await client.post("/api/schools", json=payload, headers=teacher_headers)
    assert r.status_code == 403

    # 5. Mais l'admin national, lui, peut.
    r2 = await client.post("/api/schools", json=payload, headers=admin_headers)
    assert r2.status_code in (200, 201)
```

Points cles :

* Le decorateur `@pytest.mark.integration` n'est **pas obligatoire** pour
  fonctionner (l'auto-detection asyncio fait le job), mais il documente
  l'intention et permet `-m integration` pour filtrer.
* `factories.bind(db_session)` **doit** etre appele en premier — sinon les
  inserts atterissent sur une session orpheline et le test echoue.
* Toutes les ecritures sont rollback en fin de test. Aucune fuite entre
  tests, aucun nettoyage manuel necessaire.

## Ajouter une factory

`tests/integration/factories.py` contient des factories pour : `Region`,
`Prefecture`, `SubPrefecture`, `School`, `ClassRoom`, `Student`, `Teacher`,
`User`. Pour en ajouter une :

```python
# tests/integration/factories.py
import factory

from app.modules.attendance.models import AttendanceRecord
from app.shared.base import generate_cuid
from app.shared.enums import AttendanceStatus


class AttendanceRecordFactory(_AsyncBaseFactory):
    class Meta:
        model = AttendanceRecord

    id = factory.LazyFunction(generate_cuid)
    studentId = ""              # a passer par le test
    schoolId = ""               # a passer par le test
    status = AttendanceStatus.PRESENT
    scannedAt = factory.Faker("date_time_this_year", locale="fr_FR")
```

Puis :

```python
attendance = await AttendanceRecordFactory.create_async(
    studentId=student.id, schoolId=school.id,
)
```

Conventions :

* Toujours `id = factory.LazyFunction(generate_cuid)` pour rester compatible
  avec le format cuid Prisma.
* Pour les FK obligatoires : valeur vide par defaut (`""` ou `None`) et le
  test passe l'id explicitement (plus lisible que des sub-factory recursives).
* Si la factory genere du Faker locale-sensible, utiliser `locale="fr_FR"`.

## Ecrire un test authentifie pour un role donne

Le fixture `auth_headers` est une **factory async** qui prend un `UserRole`
et renvoie un header `Authorization: Bearer <jwt>` pret a injecter :

```python
async def test_inspector_can_list_inspections(client, auth_headers):
    headers = await auth_headers(UserRole.INSPECTOR)
    r = await client.get("/api/inspections", headers=headers)
    assert r.status_code == 200
```

Pour rattacher l'utilisateur a une entite territoriale :

```python
tree = await factories.make_territorial_tree()
headers = await auth_headers(
    UserRole.SCHOOL_DIRECTOR,
    schoolId=tree["school"].id,
)
```

Roles supportes (depuis `app.shared.enums.UserRole`) : `NATIONAL_ADMIN`,
`MINISTRY_ADMIN`, `REGIONAL_ADMIN`, `PREFECTURE_ADMIN`,
`SUB_PREFECTURE_ADMIN`, `INSPECTOR`, `SCHOOL_DIRECTOR`, `TEACHER`,
`CENSUS_AGENT`.

## Tester auth + MFA (Module 1)

Le module 1 (auth durci) ajoute un fixture session-scoped autouse qui
**rebascule Argon2 sur un profil rapide** (`time_cost=1, memory_cost=8`)
pour la duree de la suite — sinon chaque test passerait 1 seconde dans
les hash. Voir `tests/integration/conftest.py::_fast_argon2_for_tests`.

Le fichier `tests/integration/test_auth_module1.py` montre les patterns :

```python
# 1) Creer un user (PWD_OK = "Test@Pa55word!") :
user = await factories.UserFactory.create_async(
    email="alice@test.local",
    passwordHash=hash_password("Test@Pa55word!"),
)

# 2) Activer la MFA + recuperer le secret en clair pour generer un TOTP :
secret, plain_recovery_codes, _ = await _enable_mfa(db_session, user)
import pyotp
code = pyotp.TOTP(secret).now()

# 3) Loger, recuperer le challenge, puis verifier la MFA :
login = await client.post("/api/auth/login", json={...})
challenge = login.json()["mfaChallenge"]
r = await client.post(
    "/api/auth/mfa/verify",
    json={"challengeToken": challenge, "code": code},
)
```

Pour tester les TTL JWT, on utilise `freezegun` :

```python
from freezegun import freeze_time

with freeze_time("2026-05-23T12:00:00Z"):
    challenge = create_mfa_challenge_token(user.id)  # 5-min TTL
with freeze_time("2026-05-23T12:06:00Z"):
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(challenge, expected_type="mfa_challenge")
```

Rate limiter : la DB Redis 15 est `flushdb()` apres chaque test (geree par
la fixture `redis_client`), donc 5 echecs successifs dans le test N
n'affectent pas le test N+1.

## Lancer Locust (tests de charge)

Voir `tests/load/README.md` pour les details. Resume :

```bash
# 1. Demarrer l'API en local
.venv/bin/uvicorn app.main:app --port 8000

# 2. Lancer Locust (UI web sur http://localhost:8089)
make test-load
```

## Generer le rapport de couverture

```bash
make coverage           # terminal, term-missing
make coverage-html      # HTML + open du navigateur
```

Le seuil `fail_under` est a **0 %** pour le Module 0 — on **mesure** sans
bloquer le merge. Les modules 1+ pourront augmenter ce seuil dans
`[tool.coverage.report]` au fur et a mesure.

## Tests dependant de PostGIS

L'extension PostGIS est requise par le module Cartography (Phase 3+) et
par la colonne `School.geom`. Si elle est presente sur le serveur Postgres,
les tests tournent normalement. Si elle est **absente** (cas frequent en
dev local sur Postgres bare), la conftest :

1. Detecte l'absence au demarrage de la session.
2. Replie la colonne `Geography` par un `Text` nullable pour permettre
   `Base.metadata.create_all()` de fonctionner.
3. Supprime les indexes GIST associes.
4. Auto-skip tous les tests marques `@pytest.mark.postgis`.

Donc si vous ecrivez un test qui interroge `ST_DWithin` ou un autre
operateur PostGIS, **marquez-le** :

```python
@pytest.mark.postgis
async def test_search_schools_within_5km(db_session): ...
```

Pour activer PostGIS en local :

```bash
brew install postgis           # macOS
# Puis :
psql -h 127.0.0.1 -p 5433 -U gestionee -d gestionee_test \
    -c "CREATE EXTENSION postgis;"
```

## Depannage

| Probleme                                                      | Solution                                                                                                                |
|---------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------|
| `psycopg2.errors.InvalidCatalogName: database "gestionee_test" does not exist` | `make db-test-create`                                                                                                  |
| `RuntimeError: Event loop is closed` au teardown              | Verifier que les fixtures DB/Redis sont bien `loop_scope="session"`. Le defaut est deja configure dans `pyproject.toml`. |
| `Redis indisponible — skip tests Redis`                       | Demarrer Redis : `brew services start redis` (ou docker run -p 6379:6379 redis).                                        |
| `data type text has no default operator class for "gist"`     | PostGIS absent : la conftest devrait gerer automatiquement. Si l'erreur persiste, verifier qu'on est bien sur la DB `gestionee_test`. |
| `No AsyncSession bound. Call factories.bind(db_session) first.` | Ajouter `factories.bind(db_session)` au debut du test avant tout `*.create_async()`.                                   |
| `403 Forbidden` inattendu dans un test admin                  | Verifier le role passe a `auth_headers(...)`. Les permissions sont dans `app/shared/permissions.py`.                    |

## Migration vers TestContainers (plus tard)

Quand Docker sera disponible sur les machines dev / CI, on pourra
remplacer la DB jetable `gestionee_test` par un container ephemere :

1. Ajouter `testcontainers[postgres]>=4` aux dev deps.
2. Dans `tests/integration/conftest.py`, remplacer le corps de
   `_test_database_url()` par :

   ```python
   from testcontainers.postgres import PostgresContainer

   _container = PostgresContainer("postgis/postgis:16-3.4")
   _container.start()
   atexit.register(_container.stop)
   url = _container.get_connection_url().replace("psycopg2", "asyncpg")
   ```

3. Idem pour Redis avec `testcontainers.redis.RedisContainer`.

Aucun autre fichier n'a besoin d'etre touche — les fixtures aval (`db_session`,
`auth_headers`, factories, client) sont decouplees de la source de l'URL.
