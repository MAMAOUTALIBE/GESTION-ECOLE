"""Module 18 — Abstraction provider WhatsApp.

Deux implémentations :

* :class:`MockWhatsAppProvider` — log-only, utilisée en dev/test (aucun
  appel réseau, compteur monotone pour assertion ``provider_id``).
* :class:`CloudApiWhatsAppProvider` — squelette pour l'API Cloud
  WhatsApp Business (Meta). Pas activé en prod par défaut tant que les
  credentials ne sont pas fournis (backlog 18.2 : finir l'intégration).

Sélection : :func:`get_provider` lit ``WHATSAPP_PROVIDER`` env
(``mock`` par défaut).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import httpx
from loguru import logger


@dataclass(slots=True, frozen=True)
class WhatsAppSendResult:
    """Résultat normalisé d'un envoi WhatsApp."""

    success: bool
    provider_id: str | None = None
    error: str | None = None
    provider_name: str = "unknown"


class WhatsAppProvider(Protocol):
    """Interface minimale d'un provider WhatsApp."""

    name: str

    async def send(self, to: str, body: str) -> WhatsAppSendResult:
        ...


# ---------------------------------------------------------------------------
# Mock provider — log-only, utilisé en dev et dans 100% des tests.
# ---------------------------------------------------------------------------
class MockWhatsAppProvider:
    """Provider WhatsApp mock : log + provider_id monotone."""

    name: str = "mock"

    def __init__(self) -> None:
        self._counter: int = 0

    async def send(self, to: str, body: str) -> WhatsAppSendResult:
        self._counter += 1
        provider_id = f"wa-mock-{self._counter:08d}"
        logger.info(
            "[WA-MOCK] to={} body=({} chars) provider_id={}",
            to, len(body), provider_id,
        )
        return WhatsAppSendResult(
            success=True, provider_id=provider_id,
            provider_name=self.name,
        )


# ---------------------------------------------------------------------------
# Cloud API provider — squelette HTTP. Pas branché en prod par défaut.
# ---------------------------------------------------------------------------
class CloudApiWhatsAppProvider:
    """Provider Cloud API WhatsApp (Meta) — squelette HTTP.

    Endpoint utilisé :
        POST https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages

    Si les credentials manquent (env), on renvoie un :class:`WhatsAppSendResult`
    en erreur — l'appelant marquera le message en FAILED.
    """

    name: str = "cloud_api"
    _BASE_URL = "https://graph.facebook.com/v18.0"

    def __init__(
        self,
        *,
        phone_number_id: str | None = None,
        access_token: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._phone_number_id = (
            phone_number_id or os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        )
        self._access_token = (
            access_token or os.getenv("WHATSAPP_ACCESS_TOKEN")
        )
        self._client = client
        self._timeout = timeout

    async def send(self, to: str, body: str) -> WhatsAppSendResult:
        if not (self._phone_number_id and self._access_token):
            logger.warning(
                "CloudApiWhatsAppProvider: missing credentials, fail-safe",
            )
            return WhatsAppSendResult(
                success=False,
                error="WhatsApp credentials missing (phone_number_id/access_token)",
                provider_name=self.name,
            )

        url = f"{self._BASE_URL}/{self._phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body},
        }
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        try:
            client = self._client or httpx.AsyncClient(timeout=self._timeout)
            owns_client = self._client is None
            try:
                response = await client.post(url, json=payload, headers=headers)
            finally:
                if owns_client:
                    await client.aclose()
        except httpx.HTTPError as exc:
            logger.error("CloudApiWhatsAppProvider HTTP error: {}", exc)
            return WhatsAppSendResult(
                success=False, error=f"http_error: {exc}",
                provider_name=self.name,
            )

        if response.status_code >= 400:
            return WhatsAppSendResult(
                success=False,
                error=f"http_{response.status_code}: {response.text[:200]}",
                provider_name=self.name,
            )

        try:
            data = response.json()
        except Exception:  # pragma: no cover - defensive
            data = {}
        msgs = data.get("messages") or []
        provider_id = msgs[0].get("id") if msgs else None
        return WhatsAppSendResult(
            success=True, provider_id=provider_id,
            provider_name=self.name,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
_singleton: WhatsAppProvider | None = None


def get_provider() -> WhatsAppProvider:
    """Renvoie le provider WhatsApp configuré par ``WHATSAPP_PROVIDER`` env.

    Valeurs reconnues : ``mock`` (défaut) | ``cloud_api``. Singleton
    process-wide — utiliser :func:`reset_provider_cache` pour basculer
    en cours de process (tests).
    """
    global _singleton
    if _singleton is not None:
        return _singleton

    choice = os.getenv("WHATSAPP_PROVIDER", "mock").strip().lower()
    _singleton = (
        CloudApiWhatsAppProvider() if choice == "cloud_api"
        else MockWhatsAppProvider()
    )
    return _singleton


def reset_provider_cache() -> None:
    """Reset le singleton — utilisé par les tests."""
    global _singleton
    _singleton = None


def set_provider(provider: WhatsAppProvider) -> None:
    """Inject explicitement un provider (tests)."""
    global _singleton
    _singleton = provider
