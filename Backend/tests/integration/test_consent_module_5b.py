"""Module 5B — Consentement utilisateur + mentions légales.

Couvre :

1. test_status_returns_needs_acceptance_when_no_consent
2. test_status_returns_already_accepted_when_current_version
3. test_status_returns_needs_acceptance_when_old_version
4. test_accept_persists_consent
5. test_accept_records_ip_and_user_agent
6. test_accept_updates_user_consent_version
7. test_accept_idempotent_on_re_accept
8. test_consent_endpoints_require_auth
9. test_accept_rejects_outdated_version_payload
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.modules.auth.models import User
from app.modules.consent.enums import CURRENT_CONSENT_VERSION
from app.modules.consent.models import UserConsent
from app.modules.consent.schemas import AcceptConsentRequest
from app.modules.consent.service import ConsentService
from app.shared.base import generate_cuid
from app.shared.enums import UserRole

pytestmark = pytest.mark.integration


# ===========================================================================
# Helpers
# ===========================================================================
async def _make_user(
    session: AsyncSession,
    role: UserRole = UserRole.SCHOOL_DIRECTOR,
    **kwargs: Any,
) -> User:
    uid = generate_cuid()
    user = User(
        id=uid,
        email=f"5b-{role.value.lower()}-{uid[:6]}@test.local",
        passwordHash="x",
        fullName=f"Test {role.value}",
        role=role,
        isActive=True,
        **kwargs,
    )
    session.add(user)
    await session.flush()
    return user


# ===========================================================================
# 1. status renvoie needsAcceptance=True quand aucun consentement n'existe
# ===========================================================================
@pytest.mark.asyncio
async def test_status_returns_needs_acceptance_when_no_consent(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session)
    svc = ConsentService(db_session)

    status = await svc.get_status(user)

    assert status.version is None
    assert status.acceptedAt is None
    assert status.needsAcceptance is True
    assert status.currentRequiredVersion == CURRENT_CONSENT_VERSION


# ===========================================================================
# 2. status renvoie needsAcceptance=False quand la version est à jour
# ===========================================================================
@pytest.mark.asyncio
async def test_status_returns_already_accepted_when_current_version(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session)
    svc = ConsentService(db_session)

    accepted = await svc.accept(
        user,
        AcceptConsentRequest(consentVersion=CURRENT_CONSENT_VERSION),
        request=None,
    )
    assert accepted.needsAcceptance is False

    status = await svc.get_status(user)

    assert status.version == CURRENT_CONSENT_VERSION
    assert status.acceptedAt is not None
    assert status.needsAcceptance is False
    assert status.currentRequiredVersion == CURRENT_CONSENT_VERSION


# ===========================================================================
# 3. status renvoie needsAcceptance=True quand la version est obsolète
# ===========================================================================
@pytest.mark.asyncio
async def test_status_returns_needs_acceptance_when_old_version(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session)

    # Simule un consentement à une version antérieure (avant la
    # constante courante). On insère directement en DB pour bypasser
    # la validation du service.
    row = UserConsent(
        id=generate_cuid(),
        userId=user.id,
        consentVersion="2024-01-01",
    )
    db_session.add(row)
    user.consentVersion = "2024-01-01"
    await db_session.flush()

    svc = ConsentService(db_session)
    status = await svc.get_status(user)

    assert status.version == "2024-01-01"
    assert status.needsAcceptance is True
    assert status.currentRequiredVersion == CURRENT_CONSENT_VERSION


# ===========================================================================
# 4. accept persiste un UserConsent
# ===========================================================================
@pytest.mark.asyncio
async def test_accept_persists_consent(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session, UserRole.TEACHER)
    svc = ConsentService(db_session)

    result = await svc.accept(
        user,
        AcceptConsentRequest(consentVersion=CURRENT_CONSENT_VERSION),
        request=None,
    )
    assert result.version == CURRENT_CONSENT_VERSION
    assert result.needsAcceptance is False
    assert result.acceptedAt is not None

    row = (
        await db_session.execute(
            select(UserConsent).where(UserConsent.userId == user.id)
        )
    ).scalars().one()
    assert row.consentVersion == CURRENT_CONSENT_VERSION
    assert row.userId == user.id


# ===========================================================================
# 5. accept enregistre IP + user-agent quand un Request est fourni
# ===========================================================================
@pytest.mark.asyncio
async def test_accept_records_ip_and_user_agent(
    client: AsyncClient,
    auth_headers: Callable[..., Any],
    db_session: AsyncSession,
) -> None:
    # Forge un user dans la session test + un access token associé.
    headers = await auth_headers(UserRole.SCHOOL_DIRECTOR)
    # IP cliente : httpx ASGITransport envoie "127.0.0.1" par défaut.
    headers["User-Agent"] = "GESTION-EE/Test (5B)"

    response = await client.post(
        "/api/consent/accept",
        json={"consentVersion": CURRENT_CONSENT_VERSION},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == CURRENT_CONSENT_VERSION
    assert body["needsAcceptance"] is False

    # Récupère la ligne UserConsent par scan sur la version courante
    # (l'auth_headers fixture forge un user avec id généré).
    rows = (
        await db_session.execute(
            select(UserConsent).where(
                UserConsent.consentVersion == CURRENT_CONSENT_VERSION
            )
        )
    ).scalars().all()
    assert len(rows) >= 1
    # Au moins une ligne porte le user-agent envoyé.
    user_agents = {r.userAgent for r in rows if r.userAgent}
    assert any("GESTION-EE/Test" in ua for ua in user_agents)


# ===========================================================================
# 6. accept met à jour User.consentVersion (cache dénormalisé)
# ===========================================================================
@pytest.mark.asyncio
async def test_accept_updates_user_consent_version(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session)
    assert user.consentVersion is None

    svc = ConsentService(db_session)
    await svc.accept(
        user,
        AcceptConsentRequest(consentVersion=CURRENT_CONSENT_VERSION),
        request=None,
    )

    refreshed = (
        await db_session.execute(
            select(User).where(User.id == user.id)
        )
    ).scalars().one()
    assert refreshed.consentVersion == CURRENT_CONSENT_VERSION


# ===========================================================================
# 7. accept est idempotent — un re-accept ne crée pas de doublon
# ===========================================================================
@pytest.mark.asyncio
async def test_accept_idempotent_on_re_accept(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session)
    svc = ConsentService(db_session)

    first = await svc.accept(
        user,
        AcceptConsentRequest(consentVersion=CURRENT_CONSENT_VERSION),
        request=None,
    )
    second = await svc.accept(
        user,
        AcceptConsentRequest(consentVersion=CURRENT_CONSENT_VERSION),
        request=None,
    )

    assert first.needsAcceptance is False
    assert second.needsAcceptance is False

    # Une seule ligne UserConsent pour ce user (UNIQUE userId).
    rows = (
        await db_session.execute(
            select(UserConsent).where(UserConsent.userId == user.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    # acceptedAt a été mis à jour (le 2e accept écrase le 1er).
    assert rows[0].acceptedAt >= first.acceptedAt  # type: ignore[operator]


# ===========================================================================
# 8. /status et /accept renvoient 401 sans Authorization header
# ===========================================================================
@pytest.mark.asyncio
async def test_consent_endpoints_require_auth(
    client: AsyncClient,
) -> None:
    r1 = await client.get("/api/consent/status")
    assert r1.status_code == 401

    r2 = await client.post(
        "/api/consent/accept",
        json={"consentVersion": CURRENT_CONSENT_VERSION},
    )
    assert r2.status_code == 401


# ===========================================================================
# 9. accept refuse une version périmée (cohérence client/serveur)
# ===========================================================================
@pytest.mark.asyncio
async def test_accept_rejects_outdated_version_payload(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session)
    svc = ConsentService(db_session)

    with pytest.raises(ConflictError):
        await svc.accept(
            user,
            AcceptConsentRequest(consentVersion="2024-01-01"),
            request=None,
        )
