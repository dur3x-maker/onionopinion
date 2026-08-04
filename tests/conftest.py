import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database import Base, get_db
from app.main import create_app
from app.models import Rule, RuleVersion


@pytest.fixture
def db_session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        rule = Rule(code="R1", is_active=True)
        db.add(rule)
        db.flush()
        version = RuleVersion(
            rule_id=rule.id,
            version=1,
            title="Реальные угрозы насилия",
            text="Запрещены конкретные угрозы причинить физический вред.",
        )
        db.add(version)
        db.flush()
        rule.current_version_id = version.id
        db.commit()
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(db_session_factory, tmp_path):
    settings = Settings(
        secret_key="test-secret-key-that-is-at-least-32-characters",
        database_url="sqlite+pysqlite:///:memory:",
        cookie_secure=False,
        allowed_hosts="testserver",
        rate_limit_count=100,
        avatar_storage_dir=tmp_path / "avatars",
    )
    app = create_app(settings)

    def override_db():
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client
