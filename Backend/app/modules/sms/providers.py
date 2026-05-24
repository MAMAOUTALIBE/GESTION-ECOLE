"""Module 14 — Provider abstraction pour l'envoi de SMS.

Le service de haut niveau (``SmsService``) ne connaît jamais Twilio,
Orange ou un autre opérateur : il appelle ``provider.send(to, body)`` et
reçoit un :class:`SendResult` normalisé. Cela permet :

* De jouer tous les tests sans crédentiels via :class:`MockProvider`
  (envoi log-only + statut SENT immédiat).
* De brancher Twilio en prod via httpx (pas de dépendance optionnelle
  forcée — on n'ajoute PAS le SDK ``twilio`` aux requirements pour
  rester léger). L'API REST Twilio est trivialement appellable en HTTP
  basic auth.

Sélection
---------
La fabrique :func:`get_provider` lit la variable d'environnement
``SMS_PROVIDER`` (``twilio`` ou ``mock``). Défaut : ``mock`` en
développement → aucun risque d'envoi accidentel pendant les tests.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import httpx
from loguru import logger

from app.core.config import settings


@dataclass(slots=True, frozen=True)
class SendResult:
    """Résultat normalisé d'un appel ``provider.send``.

    ``provider_id`` = identifiant retourné par le provider (utile pour
    réconcilier les callbacks de delivery report). ``error`` non-null
    indique un échec — le service marque alors la ligne ``SmsMessage``
    avec ``status=FAILED`` et ``errorMessage=error``.
    """

    success: bool
    provider_id: str | None = None
    error: str | None = None
    provider_name: str = "unknown"


class SmsProvider(Protocol):
    """Interface minimale d'un provider SMS."""

    name: str

    async def send(self, to: str, body: str) -> SendResult:
        ...


# ---------------------------------------------------------------------------
# Mock provider — log-only, utilisé en dev et dans 100% des tests.
# ---------------------------------------------------------------------------
class MockProvider:
    """Provider mock qui log et renvoie SENT immédiatement.

    Utilise un compteur monotone pour générer des ``provider_id`` uniques
    et reproductibles (`mock-1`, `mock-2`, ...) — utile pour assertions.
    """

    name: str = "mock"

    def __init__(self) -> None:
        self._counter: int = 0

    async def send(self, to: str, body: str) -> SendResult:
        self._counter += 1
        provider_id = f"mock-{self._counter:08d}"
        logger.info(
            "[SMS-MOCK] to={} body=({} chars) provider_id={}",
            to, len(body), provider_id,
        )
        return SendResult(
            success=True, provider_id=provider_id,
            provider_name=self.name,
        )


# ---------------------------------------------------------------------------
# Twilio provider — appel HTTP direct (pas de SDK pour rester léger).
# ---------------------------------------------------------------------------
class TwilioProvider:
    """Provider Twilio via API REST + auth basic.

    Endpoint utilisé :
        POST https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json
        body: To=..., From=..., Body=...

    On utilise httpx (déjà dans les deps) et HTTP Basic Auth
    (account_sid:auth_token). Si les credentials manquent, on fail-safe en
    renvoyant un :class:`SendResult` avec ``success=False`` plutôt que de
    crasher — l'appelant marquera le SMS comme FAILED.
    """

    name: str = "twilio"
    _BASE_URL = "https://api.twilio.com/2010-04-01"

    def __init__(
        self,
        *,
        account_sid: str | None = None,
        auth_token: str | None = None,
        from_number: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._account_sid = account_sid or settings.twilio_account_sid
        self._auth_token = auth_token or settings.twilio_auth_token
        self._from_number = from_number or settings.twilio_from_number
        self._client = client
        self._timeout = timeout

    async def send(self, to: str, body: str) -> SendResult:
        if not (self._account_sid and self._auth_token and self._from_number):
            logger.warning(
                "TwilioProvider: missing credentials, falling back to FAILED",
            )
            return SendResult(
                success=False,
                error="Twilio credentials missing (account_sid/auth_token/from)",
                provider_name=self.name,
            )

        url = f"{self._BASE_URL}/Accounts/{self._account_sid}/Messages.json"
        data = {"To": to, "From": self._from_number, "Body": body}
        auth = (self._account_sid, self._auth_token)

        try:
            client = self._client or httpx.AsyncClient(timeout=self._timeout)
            owns_client = self._client is None
            try:
                response = await client.post(url, data=data, auth=auth)
            finally:
                if owns_client:
                    await client.aclose()
        except httpx.HTTPError as exc:
            logger.error("TwilioProvider HTTP error: {}", exc)
            return SendResult(
                success=False, error=f"http_error: {exc}",
                provider_name=self.name,
            )

        if response.status_code >= 400:
            return SendResult(
                success=False,
                error=f"http_{response.status_code}: {response.text[:200]}",
                provider_name=self.name,
            )

        try:
            payload = response.json()
        except Exception:  # pragma: no cover - defensive
            payload = {}
        provider_id = payload.get("sid") or payload.get("messageSid")
        return SendResult(
            success=True, provider_id=provider_id,
            provider_name=self.name,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
# Singleton volontaire : on garde le même MockProvider pour conserver le
# compteur monotone tout au long du process — pratique pour les tests.
_singleton: SmsProvider | None = None


def get_provider() -> SmsProvider:
    """Renvoie l'instance unique de provider SMS configurée par env.

    * ``SMS_PROVIDER=twilio`` → :class:`TwilioProvider` (avec les
      crédentiels lus depuis ``settings``).
    * ``SMS_PROVIDER=mock`` (défaut) → :class:`MockProvider`.

    Le choix est pris la PREMIÈRE FOIS que ``get_provider`` est appelé ;
    pour basculer en cours de process (tests), utiliser
    :func:`reset_provider_cache` puis re-appeler.
    """
    global _singleton
    if _singleton is not None:
        return _singleton

    choice = os.getenv("SMS_PROVIDER", "mock").strip().lower()
    _singleton = TwilioProvider() if choice == "twilio" else MockProvider()
    return _singleton


def reset_provider_cache() -> None:
    """Hook utilisé par les tests pour repartir d'un provider neuf."""
    global _singleton
    _singleton = None


def set_provider(provider: SmsProvider) -> None:
    """Inject explicitement un provider (utile pour les tests)."""
    global _singleton
    _singleton = provider
