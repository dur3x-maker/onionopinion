import html
import re
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.config import Settings
from app.database import get_db
from app.main import create_app
from app.models import (
    Comment,
    ModerationAudit,
    ModerationDecision,
    ModerationReview,
    Post,
    Report,
    Rule,
    RuleVersion,
    User,
)
from app.security import hash_password


PASSWORD = "correct horse battery staple"


def csrf(response) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match
    return html.unescape(match.group(1))


def register(client, login_name="member", display_name="Участник"):
    page = client.get("/register")
    return client.post(
        "/register",
        data={
            "csrf_token": csrf(page),
            "login": login_name,
            "display_name": display_name,
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
        },
        follow_redirects=False,
    )


def login(client, login_name="operator"):
    page = client.get("/login")
    return client.post(
        "/login",
        data={"csrf_token": csrf(page), "login": login_name, "password": PASSWORD},
        follow_redirects=False,
    )


def logout(client):
    page = client.get("/")
    return client.post(
        "/logout", data={"csrf_token": csrf(page)}, follow_redirects=False
    )


def create_admin(db_session_factory, login_name="operator") -> int:
    with db_session_factory() as db:
        user = User(
            login=login_name,
            display_name="Оператор",
            password_hash=hash_password(PASSWORD),
            is_admin=True,
        )
        db.add(user)
        db.commit()
        return user.id


def create_post(client, body="Проверяемый пост") -> int:
    page = client.get("/")
    response = client.post(
        "/posts",
        data={"csrf_token": csrf(page), "body": body},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[-1])


def create_comment(client, post_id, body, parent_id=None) -> int:
    page = client.get(f"/posts/{post_id}")
    data = {"csrf_token": csrf(page), "body": body}
    if parent_id:
        data["parent_id"] = str(parent_id)
    response = client.post(
        f"/posts/{post_id}/comments", data=data, follow_redirects=False
    )
    assert response.status_code == 303
    return int(response.headers["location"].split("#comment-")[-1])


def create_report(client, target_type, target_id, reason="Проверить нарушение") -> int:
    page = client.get(
        f"/reports/new?target_type={target_type}&target_id={target_id}"
    )
    response = client.post(
        "/reports",
        data={
            "csrf_token": csrf(page),
            "target_type": target_type,
            "target_id": target_id,
            "reason": reason,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return 0


def delete_report(client, report_id=1, rule_version_id=1, reason="Конкретная угроза"):
    detail = client.get(f"/admin/reports/{report_id}")
    assert detail.status_code == 200
    return client.post(
        f"/admin/reports/{report_id}/action",
        data={
            "csrf_token": csrf(detail),
            "action": "delete",
            "reason": reason,
            "rule_version_id": str(rule_version_id) if rule_version_id is not None else "",
        },
        follow_redirects=False,
    )


def test_rules_are_structured_and_state_opinion_philosophy(client):
    page = client.get("/rules")
    assert page.status_code == 200
    assert "R1" in page.text
    assert "версия 1" in page.text
    assert "Модерируем запрещённые действия и контент, а не мнения" in page.text
    assert "Политические взгляды" in page.text
    assert "критика администрации" in page.text
    assert "мат не являются нарушением" in page.text


def test_rule_versions_are_immutable_and_cli_creates_a_new_version(
    db_session_factory, monkeypatch
):
    from app import cli

    with db_session_factory() as db:
        old = db.get(RuleVersion, 1)
        old.title = "Переписанный исторический текст"
        with pytest.raises(ValueError, match="неизменяемы"):
            db.commit()
        db.rollback()

    monkeypatch.setattr(cli, "SessionLocal", db_session_factory)
    new_id = cli.add_rule_version(
        "R1", "Уточнённые угрозы", "Новый текст применяется только к будущим решениям."
    )
    with db_session_factory() as db:
        rule = db.scalar(select(Rule).where(Rule.code == "R1"))
        old = db.get(RuleVersion, 1)
        new = db.get(RuleVersion, new_id)
        assert old.title == "Реальные угрозы насилия"
        assert new.version == 2
        assert rule.current_version_id == new.id


@pytest.mark.parametrize("rule_version_id", [None, 999999])
def test_deletion_requires_an_existing_active_rule(
    client, db_session_factory, rule_version_id
):
    post_id = create_post(client)
    create_report(client, "post", post_id)
    create_admin(db_session_factory)
    login(client)
    response = delete_report(client, rule_version_id=rule_version_id)
    assert response.status_code == 422
    with db_session_factory() as db:
        assert not db.get(Post, post_id).is_deleted
        assert db.scalar(select(func.count(ModerationDecision.id))) == 0


def test_old_rule_version_becomes_invalid_without_mutating_history(
    client, db_session_factory, monkeypatch
):
    from app import cli

    post_id = create_post(client)
    create_report(client, "post", post_id)
    create_admin(db_session_factory)
    login(client)
    monkeypatch.setattr(cli, "SessionLocal", db_session_factory)
    new_id = cli.add_rule_version("R1", "Новая версия", "Новый исторический текст")
    assert delete_report(client, rule_version_id=1).status_code == 422
    assert delete_report(client, rule_version_id=new_id).status_code == 303
    with db_session_factory() as db:
        decision = db.scalar(select(ModerationDecision))
        assert decision.rule_version_id == new_id


def test_public_notice_and_decision_page_preserve_rule_and_escape_explanation(
    client, db_session_factory
):
    post_id = create_post(client, "Скрываемый текст")
    create_report(client, "post", post_id)
    create_admin(db_session_factory, login_name="private_operator_login")
    login(client, "private_operator_login")
    payload = '<script>alert("moderation")</script>'
    assert delete_report(client, reason=payload).status_code == 303

    post_page = client.get(f"/posts/{post_id}")
    assert "Пост удалён модерацией" in post_page.text
    assert "R1" in post_page.text
    assert "Подробнее и обсуждение решения" in post_page.text
    decision_page = client.get("/moderation/decisions/1")
    assert "Текст правила на момент решения" in decision_page.text
    assert "Реальные угрозы насилия" in decision_page.text
    assert "&lt;script&gt;" in decision_page.text
    assert "<script>" not in decision_page.text
    assert "private_operator_login" not in decision_page.text


def test_duplicate_reports_resolve_to_one_idempotent_decision(
    client, db_session_factory
):
    post_id = create_post(client)
    create_report(client, "post", post_id, "Первая жалоба")
    create_report(client, "post", post_id, "Вторая жалоба")
    create_admin(db_session_factory)
    login(client)
    assert delete_report(client, 1).status_code == 303
    with db_session_factory() as db:
        reports = list(db.scalars(select(Report).order_by(Report.id)))
        assert [report.status for report in reports] == ["resolved", "resolved"]
        assert reports[0].decision_id == reports[1].decision_id
        assert db.scalar(select(func.count(ModerationDecision.id))) == 1
        assert db.scalar(select(func.count(ModerationAudit.id))) == 1


def test_anonymous_and_registered_users_share_nested_decision_discussion(
    client, db_session_factory
):
    post_id = create_post(client)
    create_report(client, "post", post_id)
    create_admin(db_session_factory)
    login(client)
    assert delete_report(client).status_code == 303
    logout(client)

    page = client.get("/moderation/decisions/1")
    root = client.post(
        "/moderation/decisions/1/comments",
        data={"csrf_token": csrf(page), "body": "Анонимная критика решения"},
        follow_redirects=False,
    )
    assert root.status_code == 303
    root_id = int(root.headers["location"].split("#comment-")[-1])
    register(client, "registered_critic", "Критик")
    page = client.get("/moderation/decisions/1")
    reply = client.post(
        "/moderation/decisions/1/comments",
        data={
            "csrf_token": csrf(page),
            "body": "Зарегистрированный ответ",
            "parent_id": root_id,
        },
        follow_redirects=False,
    )
    assert reply.status_code == 303
    page = client.get("/moderation/decisions/1")
    assert "Анонимная критика решения" in page.text
    assert "Зарегистрированный ответ" in page.text
    assert page.text.count('class="comment depth-') == 2
    with db_session_factory() as db:
        comments = list(db.scalars(select(Comment).order_by(Comment.id)))
        assert comments[0].post_id is None
        assert comments[0].moderation_decision_id == 1
        assert comments[0].author_id is None
        assert comments[1].author_id is not None
        assert comments[1].parent_id == comments[0].id


def test_registered_decision_comment_has_a_valid_profile_link(
    client, db_session_factory
):
    post_id = create_post(client)
    create_report(client, "post", post_id)
    create_admin(db_session_factory)
    login(client)
    delete_report(client)
    logout(client)
    register(client, "decision_critic", "Критик решения")

    page = client.get("/moderation/decisions/1")
    response = client.post(
        "/moderation/decisions/1/comments",
        data={"csrf_token": csrf(page), "body": "Комментарий из профиля"},
        follow_redirects=False,
    )
    comment_id = int(response.headers["location"].split("#comment-")[-1])
    profile = client.get("/me", follow_redirects=True)
    assert f'/moderation/decisions/1#comment-{comment_id}' in profile.text
    assert "/posts/None" not in profile.text


def test_decision_discussion_xss_is_escaped(client, db_session_factory):
    post_id = create_post(client)
    create_report(client, "post", post_id)
    create_admin(db_session_factory)
    login(client)
    delete_report(client)
    page = client.get("/moderation/decisions/1")
    payload = '<img src=x onerror="alert(1)">'
    client.post(
        "/moderation/decisions/1/comments",
        data={"csrf_token": csrf(page), "body": payload},
    )
    rendered = client.get("/moderation/decisions/1")
    assert "&lt;img" in rendered.text
    assert '<img src=x onerror="alert(1)">' not in rendered.text


def test_reversal_restores_content_and_preserves_public_history(
    client, db_session_factory
):
    post_id = create_post(client, "Оригинальный текст")
    child_id = create_comment(client, post_id, "Сохранённый дочерний ответ")
    create_report(client, "post", post_id)
    create_admin(db_session_factory)
    login(client)
    delete_report(client)
    report_page = client.get("/admin/reports/1")
    response = client.post(
        "/admin/decisions/1/reverse",
        data={"csrf_token": csrf(report_page), "reason": "Ошибка применения правила"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    restored = client.get(f"/posts/{post_id}")
    assert "Оригинальный текст" in restored.text
    assert "Сохранённый дочерний ответ" in restored.text
    assert f'id="comment-{child_id}"' in restored.text
    history = client.get("/moderation/decisions/1")
    assert "Решение отменено" in history.text
    assert "контент восстановлен" in history.text
    assert "Ошибка применения правила" in history.text
    with db_session_factory() as db:
        decision = db.get(ModerationDecision, 1)
        assert decision.status == "reversed"
        assert decision.rule_version_id == 1
        assert db.scalar(select(func.count(ModerationReview.id))) == 1
        assert db.scalar(select(func.count(ModerationAudit.id))) == 2


def test_reversal_requires_admin_csrf_and_is_not_repeatable(client, db_session_factory):
    post_id = create_post(client)
    create_report(client, "post", post_id)
    create_admin(db_session_factory)
    login(client)
    delete_report(client)
    assert client.post(
        "/admin/decisions/1/reverse", data={"csrf_token": "wrong", "reason": ""}
    ).status_code == 403
    page = client.get("/admin/reports/1")
    assert client.post(
        "/admin/decisions/1/reverse",
        data={"csrf_token": csrf(page), "reason": "Пересмотр"},
    ).status_code == 200
    page = client.get("/")
    assert client.post(
        "/admin/decisions/1/reverse",
        data={"csrf_token": csrf(page), "reason": "Повтор"},
    ).status_code == 409


@contextmanager
def network_client(db_session_factory, tmp_path, *, peer, allowed, trusted=""):
    settings = Settings(
        secret_key="test-secret-key-that-is-at-least-32-characters",
        database_url="sqlite+pysqlite:///:memory:",
        allowed_hosts="testserver",
        admin_allowed_networks=allowed,
        trusted_proxy_networks=trusted,
        avatar_storage_dir=tmp_path / re.sub(r"[^a-zA-Z0-9]", "_", peer),
        rate_limit_count=100,
    )
    app = create_app(settings)

    def override_db():
        with db_session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app, client=(peer, 50000)) as custom_client:
        yield custom_client


def test_admin_whitelist_cidr_and_role_authorization(db_session_factory, tmp_path):
    create_admin(db_session_factory)
    with network_client(
        db_session_factory,
        tmp_path,
        peer="203.0.113.42",
        allowed="203.0.113.0/24,2001:db8::/32",
    ) as allowed_client:
        login(allowed_client)
        assert allowed_client.get("/admin").status_code == 200
        logout(allowed_client)
        register(allowed_client, "ordinary", "Обычный")
        assert allowed_client.get("/admin").status_code == 403

    with network_client(
        db_session_factory,
        tmp_path,
        peer="198.51.100.20",
        allowed="203.0.113.0/24",
    ) as denied_client:
        login(denied_client)
        response = denied_client.get("/admin")
        assert response.status_code == 404
        assert "203.0.113" not in response.text


def test_untrusted_proxy_headers_cannot_forge_admin_address(
    db_session_factory, tmp_path
):
    create_admin(db_session_factory)
    with network_client(
        db_session_factory,
        tmp_path,
        peer="198.51.100.20",
        allowed="203.0.113.42/32",
        trusted="10.0.0.0/8",
    ) as custom_client:
        login(custom_client)
        response = custom_client.get(
            "/admin",
            headers={
                "x-forwarded-for": "203.0.113.42",
                "x-real-ip": "203.0.113.42",
                "forwarded": "for=203.0.113.42",
            },
        )
        assert response.status_code == 404


def test_trusted_proxy_uses_rightmost_untrusted_forwarded_address(
    db_session_factory, tmp_path
):
    create_admin(db_session_factory)
    with network_client(
        db_session_factory,
        tmp_path,
        peer="10.1.2.3",
        allowed="203.0.113.42/32",
        trusted="10.0.0.0/8",
    ) as proxy_client:
        login(proxy_client)
        assert proxy_client.get(
            "/admin", headers={"x-forwarded-for": "203.0.113.42"}
        ).status_code == 200
        assert proxy_client.get(
            "/admin", headers={"x-forwarded-for": "198.51.100.5"}
        ).status_code == 404
        assert proxy_client.get(
            "/admin", headers={"x-forwarded-for": "not-an-ip"}
        ).status_code == 404


def test_trusted_proxy_without_forwarded_chain_fails_closed(
    db_session_factory, tmp_path
):
    create_admin(db_session_factory)
    with network_client(
        db_session_factory,
        tmp_path,
        peer="10.1.2.3",
        allowed="10.0.0.0/8",
        trusted="10.0.0.0/8",
    ) as proxy_client:
        login(proxy_client)
        assert proxy_client.get("/admin").status_code == 404


@pytest.mark.parametrize(
    "override",
    [
        {"secret_key": "test-secret-key-that-is-at-least-32-characters"},
        {"cookie_secure": False},
        {"allowed_hosts": "localhost"},
        {"allowed_hosts": "*.opinion.example"},
        {"database_url": "sqlite+pysqlite:///production.db"},
        {"database_url": "postgresql+psycopg://opinion:opinion@db/opinion"},
        {"database_url": "postgresql+psycopg://opinion:@db/opinion"},
        {"trusted_proxy_networks": "0.0.0.0/0"},
        {"admin_allowed_networks": "::/0"},
    ],
)
def test_production_configuration_rejects_unsafe_defaults(override):
    values = {
        "environment": "production",
        "secret_key": "A9s!production-secret-with-many-random-characters-2026",
        "cookie_secure": True,
        "allowed_hosts": "opinion.example",
        "database_url": "postgresql+psycopg://opinion:strong-random-password@db/opinion",
    }
    values.update(override)
    with pytest.raises(ValidationError):
        Settings(**values)


def test_valid_production_configuration_enables_hsts_and_secure_cookie(tmp_path):
    settings = Settings(
        environment="production",
        secret_key="A9s!production-secret-with-many-random-characters-2026",
        cookie_secure=True,
        allowed_hosts="opinion.example",
        database_url=(
            "postgresql+psycopg://opinion:"
            "strong-random-password@db/opinion"
        ),
        avatar_storage_dir=tmp_path / "production-avatars",
    )
    app = create_app(settings)
    with TestClient(app, base_url="https://opinion.example") as production_client:
        response = production_client.get("/missing-page")
    assert response.status_code == 404
    assert response.headers["strict-transport-security"] == "max-age=31536000"
    assert "secure" in response.headers["set-cookie"].lower()


def test_official_addresses_render_only_configured_safe_values(
    client, db_session_factory, tmp_path
):
    empty = client.get("/addresses")
    assert "Адреса пока не настроены" in empty.text
    assert ".onion" not in empty.text

    settings = Settings(
        secret_key="test-secret-key-that-is-at-least-32-characters",
        database_url="sqlite+pysqlite:///:memory:",
        allowed_hosts="testserver",
        official_clearnet_url="https://opinion.example",
        official_onion_url="http://abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz2345.onion",
        avatar_storage_dir=tmp_path / "official-address-avatars",
    )
    app = create_app(settings)

    def override_db():
        with db_session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as configured:
        page = configured.get("/addresses")
        assert "https://opinion.example" in page.text
        assert "Onion" in page.text

    with pytest.raises(ValidationError):
        Settings(
            secret_key="test-secret-key-that-is-at-least-32-characters",
            official_onion_url="javascript:alert(1)",
        )

    with pytest.raises(ValidationError):
        Settings(
            secret_key="test-secret-key-that-is-at-least-32-characters",
            official_clearnet_url="http://opinion.example",
        )

    with pytest.raises(ValidationError):
        Settings(
            secret_key="test-secret-key-that-is-at-least-32-characters",
            official_onion_url="http://short.onion",
        )
