"""Phase 6 contract tests — Notifications (multi-channel parent comms).

Pydantic validation + OpenAPI surface only. End-to-end dispatch tests
(SMS / WhatsApp / Email / Push / InApp) live in tests/integration/ in a
later phase since they require live credentials.
"""
import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.modules.notifications.channels.base import (
    ChannelMessage,
    ChannelResult,
    normalize_phone,
)
from app.modules.notifications.dispatcher import get_adapter
from app.modules.notifications.schemas import (
    BulkCommunicationRequest,
    CreateCommunicationRequest,
    DispatchTestRequest,
)
from app.modules.notifications.templates import (
    attendance_absent,
    bulletin_available,
    custom,
    validation_approved,
    validation_rejected,
)
from app.shared.enums import CommunicationChannel, CommunicationStatus


# ---------------------------------------------------------------------
# OpenAPI: every Phase 6 endpoint must be discoverable
# ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_openapi_exposes_phase6_endpoints(async_client: AsyncClient) -> None:
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]

    for url in (
        "/api/communications",
        "/api/communications/bulk",
        "/api/communications/test",
        "/api/communications/{communication_id}",
        "/api/communications/{communication_id}/retry",
    ):
        assert url in paths, f"Missing endpoint: {url}"


# ---------------------------------------------------------------------
# Pydantic — request validation
# ---------------------------------------------------------------------
def test_create_communication_requires_message() -> None:
    with pytest.raises(ValidationError):
        CreateCommunicationRequest(
            parentId="p1", channel=CommunicationChannel.SMS, message=""
        )


def test_create_communication_message_too_long() -> None:
    with pytest.raises(ValidationError):
        CreateCommunicationRequest(
            parentId="p1",
            channel=CommunicationChannel.SMS,
            message="x" * 4001,
        )


def test_create_communication_default_send_now_true() -> None:
    dto = CreateCommunicationRequest(
        parentId="p1", channel=CommunicationChannel.SMS, message="Hello"
    )
    assert dto.sendNow is True


def test_create_communication_strips_whitespace() -> None:
    dto = CreateCommunicationRequest(
        parentId="p1",
        channel=CommunicationChannel.EMAIL,
        subject="  Hello  ",
        message="  Body content  ",
    )
    assert dto.subject == "Hello"
    assert dto.message == "Body content"


def test_create_communication_rejects_invalid_channel() -> None:
    with pytest.raises(ValidationError):
        CreateCommunicationRequest.model_validate(
            {"parentId": "p1", "channel": "TELEPATHY", "message": "Hi"}
        )


def test_bulk_communication_requires_at_least_one_parent() -> None:
    with pytest.raises(ValidationError):
        BulkCommunicationRequest(
            parentIds=[], channel=CommunicationChannel.SMS, message="Hi"
        )


def test_bulk_communication_caps_at_5000() -> None:
    with pytest.raises(ValidationError):
        BulkCommunicationRequest(
            parentIds=[f"p{i}" for i in range(5001)],
            channel=CommunicationChannel.SMS,
            message="Hi",
        )


def test_bulk_communication_accepts_5000() -> None:
    dto = BulkCommunicationRequest(
        parentIds=[f"p{i}" for i in range(5000)],
        channel=CommunicationChannel.SMS,
        message="Hi",
    )
    assert len(dto.parentIds) == 5000


def test_dispatch_test_requires_recipient_and_message() -> None:
    with pytest.raises(ValidationError):
        DispatchTestRequest(
            channel=CommunicationChannel.SMS, recipient="", message="Hi"
        )
    with pytest.raises(ValidationError):
        DispatchTestRequest(
            channel=CommunicationChannel.SMS, recipient="+224620", message=""
        )


# ---------------------------------------------------------------------
# Phone normalization (Guinea +224)
# ---------------------------------------------------------------------
def test_normalize_phone_already_e164() -> None:
    assert normalize_phone("+224620000000") == "+224620000000"


def test_normalize_phone_local_prefixed_zero() -> None:
    assert normalize_phone("0620000000") == "+224620000000"


def test_normalize_phone_local_no_prefix() -> None:
    assert normalize_phone("620000000") == "+224620000000"


def test_normalize_phone_strips_spaces_and_dashes() -> None:
    assert normalize_phone("+224 620-00-00-00") == "+224620000000"


def test_normalize_phone_double_zero_to_plus() -> None:
    assert normalize_phone("00224620000000") == "+224620000000"


def test_normalize_phone_other_country() -> None:
    assert normalize_phone("+33612345678") == "+33612345678"


# ---------------------------------------------------------------------
# Dispatcher routing — adapters returned per channel
# ---------------------------------------------------------------------
def test_dispatcher_returns_sms_adapter() -> None:
    adapter = get_adapter(CommunicationChannel.SMS)
    assert adapter is not None and adapter.name == "sms"


def test_dispatcher_returns_whatsapp_adapter() -> None:
    adapter = get_adapter(CommunicationChannel.WHATSAPP)
    assert adapter is not None and adapter.name == "whatsapp"


def test_dispatcher_returns_email_adapter() -> None:
    adapter = get_adapter(CommunicationChannel.EMAIL)
    assert adapter is not None and adapter.name == "email"


def test_dispatcher_phone_returns_none() -> None:
    """PHONE has no automated transport — manual call log only."""
    assert get_adapter(CommunicationChannel.PHONE) is None


def test_dispatcher_inapp_requires_session() -> None:
    with pytest.raises(ValueError):
        get_adapter(CommunicationChannel.IN_APP)


# ---------------------------------------------------------------------
# Channel adapters — short-circuit when not configured
# ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sms_adapter_short_circuits_without_credentials() -> None:
    adapter = get_adapter(CommunicationChannel.SMS)
    assert adapter is not None
    result: ChannelResult = await adapter.send(
        ChannelMessage(recipient="+224620000000", message="ping")
    )
    # In test env settings.twilio_* are unset → not_configured (no network call)
    assert result.ok is False
    assert result.error == "not_configured"


@pytest.mark.asyncio
async def test_whatsapp_adapter_short_circuits_without_credentials() -> None:
    adapter = get_adapter(CommunicationChannel.WHATSAPP)
    assert adapter is not None
    result = await adapter.send(
        ChannelMessage(recipient="+224620000000", message="ping")
    )
    assert result.ok is False
    assert result.error == "not_configured"


@pytest.mark.asyncio
async def test_email_adapter_short_circuits_without_credentials() -> None:
    adapter = get_adapter(CommunicationChannel.EMAIL)
    assert adapter is not None
    result = await adapter.send(
        ChannelMessage(recipient="dest@example.com", message="ping")
    )
    assert result.ok is False
    assert result.error == "not_configured"


# ---------------------------------------------------------------------
# Templates — pure functions
# ---------------------------------------------------------------------
def test_template_bulletin_available_includes_url_and_name() -> None:
    subject, message = bulletin_available(
        "Aïssata Camara", "Trimestre 1", "https://gestionee.gn/v/ABC"
    )
    assert "Bulletin" in subject
    assert "Aïssata Camara" in message
    assert "Trimestre 1" in message
    assert "https://gestionee.gn/v/ABC" in message


def test_template_attendance_absent() -> None:
    subject, message = attendance_absent("Mamadou Bah", "2026-05-05")
    assert "Absence" in subject
    assert "Mamadou Bah" in message
    assert "2026-05-05" in message


def test_template_validation_approved() -> None:
    subject, message = validation_approved("école Conakry-Centre")
    assert subject == "Validation approuvée"
    assert "école Conakry-Centre" in message


def test_template_validation_rejected_with_reason() -> None:
    subject, message = validation_rejected("école X", "données incomplètes")
    assert "rejetée" in subject.lower()
    assert "Motif" in message
    assert "données incomplètes" in message


def test_template_validation_rejected_without_reason() -> None:
    _subject, message = validation_rejected("école Y", None)
    assert "Motif" not in message


def test_template_custom_passthrough() -> None:
    subject, message = custom("Hello", "World")
    assert subject == "Hello"
    assert message == "World"

    subject, message = custom(None, "Just a body")
    assert subject == "Message"
    assert message == "Just a body"


# ---------------------------------------------------------------------
# Communication status round-trip via Pydantic
# ---------------------------------------------------------------------
def test_communication_status_enum_values() -> None:
    assert CommunicationStatus.DRAFT == "DRAFT"
    assert CommunicationStatus.SENT == "SENT"
    assert CommunicationStatus.FAILED == "FAILED"
    assert CommunicationStatus.READ == "READ"


# ---------------------------------------------------------------------
# Auth-required endpoints
# ---------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "/api/communications",
    "/api/communications/some-id",
])
async def test_phase6_get_endpoints_require_bearer_token(
    async_client: AsyncClient, url: str
) -> None:
    response = await async_client.get(url)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_phase6_create_requires_bearer_token(async_client: AsyncClient) -> None:
    response = await async_client.post(
        "/api/communications",
        json={"parentId": "p1", "channel": "SMS", "message": "Hi"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_phase6_test_endpoint_requires_bearer_token(
    async_client: AsyncClient,
) -> None:
    response = await async_client.post(
        "/api/communications/test",
        json={"channel": "SMS", "recipient": "+224620000000", "message": "Hi"},
    )
    assert response.status_code == 401
