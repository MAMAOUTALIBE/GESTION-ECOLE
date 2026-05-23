"""Test smoke de l'infrastructure d'integration (Module 0).

Ce test prouve que toute la chaine fonctionne :
* DB de test connectee, schema cree, transaction isolee.
* Factory `UserFactory` insere un User en DB.
* `auth_headers` forge un JWT valide pour ce User.
* L'endpoint `GET /api/auth/me` retrouve l'utilisateur via la session
  partagee (override de `get_session`).
* Redis (DB 15) accepte ecriture + lecture, puis flush.

Si ce test passe, les 20 modules suivants peuvent ecrire leurs propres
tests d'integration en re-utilisant les memes fixtures.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.modules.auth.models import User
from app.shared.enums import UserRole
from tests.integration import factories


@pytest.mark.integration
async def test_infrastructure_end_to_end(
    db_session: AsyncSession,
    redis_client: Redis,
    client: AsyncClient,
    auth_headers,
) -> None:
    # 1. Bind les factories sur la session du test (isolation transactionnelle).
    factories.bind(db_session)

    # 2. Cree un User TEACHER + force un email connu pour l'assertion.
    user = await factories.UserFactory.create_async(
        email="smoke-teacher@test.local",
        fullName="Smoke Teacher",
        role=UserRole.TEACHER,
    )

    # 3. Forge un JWT signe pour ce User en re-utilisant `auth_headers`
    #    (le helper cree un *nouveau* user ; ici, on veut le notre).
    from app.core.security import create_access_token

    token = create_access_token(
        user.id,
        claims={"role": user.role.value},
    )
    headers = {"Authorization": f"Bearer {token}"}

    # 4. Appel API : la session injectee voit le user qu'on vient de creer.
    response = await client.get("/api/auth/me", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["id"] == user.id
    assert body["user"]["email"] == "smoke-teacher@test.local"
    assert body["user"]["role"] == "TEACHER"

    # 5. Sanity check Redis (DB 15) : ecriture + lecture + cleanup auto.
    await redis_client.set("smoke:test:key", "ok", ex=10)
    value = await redis_client.get("smoke:test:key")
    assert value == "ok"

    # 6. Bonus : auth_headers factory cree son propre user et l'expose.
    director_headers = await auth_headers(UserRole.SCHOOL_DIRECTOR)
    r2 = await client.get("/api/auth/me", headers=director_headers)
    assert r2.status_code == 200
    assert r2.json()["user"]["role"] == "SCHOOL_DIRECTOR"


@pytest.mark.integration
async def test_rollback_isolates_writes(db_engine: AsyncEngine) -> None:
    """L'isolation transactionnelle de db_session est verifiee localement,
    sans dependre de l'ordre d'execution avec un autre test (compatible
    pytest-xdist).

    On utilise deux AsyncSession independantes branchees directement sur
    db_engine (et NON la fixture db_session, qui enveloppe deja tout dans
    une transaction rollback-only). La 1re session insere un canary puis
    rollback ; la 2nde verifie que le canary n'existe pas — preuve que le
    rollback isole bien les ecritures.
    """
    canary_email = f"canary-isolation-{uuid.uuid4()}@test.local"

    # Premiere session : insere + rollback explicite (rien ne doit etre
    # persiste apres la sortie du with).
    async with AsyncSession(db_engine) as session1:
        user = factories.UserFactory.build(email=canary_email)
        session1.add(user)
        await session1.flush()
        # Sanity : on voit le canary AVANT le rollback dans cette session.
        seen = (
            await session1.execute(select(User).where(User.email == canary_email))
        ).scalar_one_or_none()
        assert seen is not None, "Le canary devrait etre visible avant rollback."
        await session1.rollback()

    # Seconde session : verifie absence du canary (preuve du rollback).
    async with AsyncSession(db_engine) as session2:
        result = await session2.execute(
            select(User).where(User.email == canary_email)
        )
        assert result.scalar_one_or_none() is None, (
            f"Le canary {canary_email} a survecu au rollback — isolation cassee"
        )


@pytest.mark.integration
async def test_factories_can_build_territorial_tree(db_session: AsyncSession) -> None:
    """Verifie make_territorial_tree() : la coherence des FK est respectee."""
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    assert tree["region"].id
    assert tree["prefecture"].regionId == tree["region"].id
    assert tree["subPrefecture"].prefectureId == tree["prefecture"].id
    assert tree["school"].regionId == tree["region"].id
    assert tree["school"].subPrefectureId == tree["subPrefecture"].id
    # Les coords doivent etre dans la bounding box Guinee
    assert 7.2 <= tree["school"].latitude <= 12.7
    assert -15.0 <= tree["school"].longitude <= -7.6
