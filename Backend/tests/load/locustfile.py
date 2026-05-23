"""Scenarios de charge Locust (Module 0).

Trois scenarios :

* `LoginScenario`  : POST /api/auth/login — simule l'authentification.
* `ReadScenario`   : GET /api/schools — simule la consultation
  d'un user deja authentifie (token reutilise).
* `WriteScenario`  : POST /api/attendance/scan — simule l'ecriture
  d'un evenement de presence depuis un scanneur QR mobile.

Lancement :

    locust -f tests/load/locustfile.py --host http://localhost:8000

Puis ouvrir http://localhost:8089 et configurer :
    users         = 100
    spawn rate    = 10 / sec

Ou en headless :

    locust -f tests/load/locustfile.py --host http://localhost:8000 \\
        --headless -u 100 -r 10 -t 2m --csv reports/locust

Pre-requis : un compte test connu de l'API. On lit ses credentials dans les
variables d'environnement LOCUST_EMAIL / LOCUST_PASSWORD (defaut :
admin@scolarite.gov.gn / Admin@2026 — a adapter selon les seeds locales).
"""

from __future__ import annotations

import os
import random

from locust import HttpUser, between, events, task

LOGIN_EMAIL = os.environ.get("LOCUST_EMAIL", "admin@scolarite.gov.gn")
LOGIN_PASSWORD = os.environ.get("LOCUST_PASSWORD", "Admin@2026")


@events.init.add_listener
def _on_locust_init(environment, **_kwargs):  # type: ignore[no-untyped-def]
    """Affiche un rappel de config au demarrage."""
    print(
        f"[locust] target host = {environment.host} | "
        f"login as {LOGIN_EMAIL} (override via LOCUST_EMAIL/LOCUST_PASSWORD)"
    )


class _AuthenticatedUser(HttpUser):
    """Base abstraite : effectue un login a l'`on_start`, garde le token."""

    abstract = True
    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        self.token: str | None = None
        with self.client.post(
            "/api/auth/login",
            json={"email": LOGIN_EMAIL, "password": LOGIN_PASSWORD},
            name="on_start: POST /api/auth/login",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 201):
                self.token = resp.json().get("accessToken")
                resp.success()
            else:
                resp.failure(f"login failed: {resp.status_code} {resp.text[:120]}")

    @property
    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}


class LoginScenario(HttpUser):
    """Stress endpoint /auth/login (pas d'auth persistante)."""

    wait_time = between(1, 3)
    weight = 1  # poids relatif vs autres scenarios

    @task
    def login(self) -> None:
        self.client.post(
            "/api/auth/login",
            json={"email": LOGIN_EMAIL, "password": LOGIN_PASSWORD},
            name="POST /api/auth/login",
        )


class ReadScenario(_AuthenticatedUser):
    """Lecture authentifiee — list ecoles + /me."""

    weight = 3  # plus de lecture que d'ecriture (realiste)

    @task(3)
    def list_schools(self) -> None:
        self.client.get(
            "/api/schools",
            headers=self.auth_headers,
            name="GET /api/schools",
        )

    @task(1)
    def get_me(self) -> None:
        self.client.get(
            "/api/auth/me",
            headers=self.auth_headers,
            name="GET /api/auth/me",
        )


class WriteScenario(_AuthenticatedUser):
    """Ecriture authentifiee — scan presence eleve."""

    weight = 1

    @task
    def scan_attendance(self) -> None:
        # Payload stub — quand le module attendance sera finalise, on
        # remplacera par un vrai QR signe. Pour le Module 0, on accepte que
        # l'API renvoie 4xx (l'objectif est de mesurer la latence
        # endpoint+middleware, pas la logique metier).
        payload = {
            "qrToken": f"fake-token-{random.randint(0, 1_000_000)}",
            "scannedAt": "2026-05-23T08:00:00Z",
        }
        with self.client.post(
            "/api/attendance/scan",
            json=payload,
            headers=self.auth_headers,
            name="POST /api/attendance/scan",
            catch_response=True,
        ) as resp:
            # 4xx attendus tant que le module n'est pas implemente — on les
            # marque success() pour ne pas polluer la stats failure rate.
            if resp.status_code in (200, 201, 400, 401, 404, 422):
                resp.success()
            else:
                resp.failure(
                    f"unexpected status: {resp.status_code} {resp.text[:120]}"
                )
