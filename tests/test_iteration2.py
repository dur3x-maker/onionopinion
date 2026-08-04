import base64
import html
import io
import json
import re

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Comment, ModerationAudit, Post, Report, User
from app.security import hash_password, verify_password
from app.services import build_comment_tree


PASSWORD = "correct horse battery staple"


def csrf(response) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match
    return html.unescape(match.group(1))


def register(client, login="member", display_name="Участник"):
    page = client.get("/register")
    return client.post(
        "/register",
        data={
            "csrf_token": csrf(page),
            "login": login,
            "display_name": display_name,
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
        },
        follow_redirects=False,
    )


def login(client, login_name, password=PASSWORD):
    page = client.get("/login")
    return client.post(
        "/login",
        data={
            "csrf_token": csrf(page),
            "login": login_name,
            "password": password,
        },
        follow_redirects=False,
    )


def logout(client):
    page = client.get("/")
    return client.post(
        "/logout", data={"csrf_token": csrf(page)}, follow_redirects=False
    )


def create_post(client, body="Пост"):
    page = client.get("/")
    response = client.post(
        "/posts",
        data={"csrf_token": csrf(page), "body": body},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[-1])


def create_comment(client, post_id, body, parent_id=None):
    page = client.get(f"/posts/{post_id}")
    data = {"csrf_token": csrf(page), "body": body}
    if parent_id is not None:
        data["parent_id"] = str(parent_id)
    response = client.post(
        f"/posts/{post_id}/comments", data=data, follow_redirects=False
    )
    return response


def image_bytes(fmt: str, size=(420, 240), exif=False) -> bytes:
    image = Image.new("RGB", size, (104, 181, 89))
    output = io.BytesIO()
    kwargs = {}
    if exif and fmt == "JPEG":
        metadata = Image.Exif()
        metadata[0x010E] = "private test metadata"
        kwargs["exif"] = metadata
    image.save(output, format=fmt, **kwargs)
    return output.getvalue()


def upload(client, payload: bytes, filename: str, content_type: str):
    page = client.get("/settings")
    assert page.status_code == 200
    return client.post(
        "/settings/avatar",
        data={"csrf_token": csrf(page)},
        files={"avatar": (filename, payload, content_type)},
        follow_redirects=False,
    )


def create_admin(db_session_factory, login_name="operator"):
    with db_session_factory() as db:
        admin = User(
            login=login_name,
            display_name="Оператор",
            password_hash=hash_password(PASSWORD),
            is_admin=True,
        )
        db.add(admin)
        db.commit()
        return admin.id


def create_report(client, post_id):
    page = client.get(f"/reports/new?target_type=post&target_id={post_id}")
    response = client.post(
        "/reports",
        data={
            "csrf_token": csrf(page),
            "target_type": "post",
            "target_id": post_id,
            "reason": "Проверить публикацию",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_display_name_update_and_xss_escaping(client):
    register(client)
    page = client.get("/settings")
    response = client.post(
        "/settings/profile",
        data={
            "csrf_token": csrf(page),
            "display_name": '<img src=x onerror="alert(1)">',
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    profile = client.get("/me", follow_redirects=True)
    assert "<img src=x" not in profile.text
    assert "&lt;img src=x" in profile.text


@pytest.mark.parametrize(
    ("fmt", "filename", "content_type"),
    [
        ("JPEG", "avatar.jpg", "image/jpeg"),
        ("PNG", "avatar.png", "image/png"),
        ("WEBP", "avatar.webp", "image/webp"),
    ],
)
def test_supported_avatars_are_reencoded_to_clean_webp(
    client, db_session_factory, fmt, filename, content_type
):
    register(client)
    response = upload(
        client, image_bytes(fmt, exif=fmt == "JPEG"), filename, content_type
    )
    assert response.status_code == 303
    with db_session_factory() as db:
        user = db.scalar(select(User).where(User.login == "member"))
        avatar_path = client.app.state.settings.avatar_storage_dir / f"{user.avatar_id}.webp"
    with Image.open(avatar_path) as result:
        assert result.format == "WEBP"
        assert result.size == (256, 256)
        assert len(result.getexif()) == 0
        assert "exif" not in result.info


@pytest.mark.parametrize(
    ("payload", "filename", "content_type"),
    [
        (image_bytes("GIF"), "avatar.gif", "image/gif"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', "avatar.png", "image/png"),
        (b"not an image", "avatar.png", "image/png"),
        (image_bytes("PNG"), "avatar.jpg", "image/jpeg"),
        (image_bytes("PNG"), "avatar.png", "application/octet-stream"),
        (b"\x89PNG\r\n\x1a\nbroken", "avatar.png", "image/png"),
    ],
)
def test_invalid_avatar_inputs_are_rejected(
    client, payload, filename, content_type
):
    register(client)
    response = upload(client, payload, filename, content_type)
    assert response.status_code == 422
    assert list(client.app.state.settings.avatar_storage_dir.glob("*.webp")) == []


def test_oversized_avatar_file_is_rejected(client):
    register(client)
    settings = client.app.state.settings
    response = upload(
        client,
        b"x" * (settings.avatar_max_bytes + 1),
        "large.png",
        "image/png",
    )
    assert response.status_code == 413


def test_actual_multipart_size_is_bounded_when_content_length_lies(client):
    settings = client.app.state.settings
    response = client.post(
        "/settings/avatar",
        headers={"content-length": "1"},
        files={
            "avatar": (
                "large.png",
                b"x" * (settings.avatar_max_bytes + 256 * 1024 + 1),
                "image/png",
            )
        },
    )
    assert response.status_code == 413


def test_decompression_bomb_dimensions_are_rejected(client):
    register(client)
    client.app.state.settings.avatar_max_pixels = 10_000
    response = upload(
        client, image_bytes("PNG", size=(101, 101)), "large.png", "image/png"
    )
    assert response.status_code == 422


def test_avatar_replacement_and_deletion_remove_old_files(
    client, db_session_factory
):
    register(client)
    assert upload(client, image_bytes("PNG"), "one.png", "image/png").status_code == 303
    with db_session_factory() as db:
        old_id = db.scalar(select(User).where(User.login == "member")).avatar_id
    assert upload(client, image_bytes("JPEG"), "two.jpg", "image/jpeg").status_code == 303
    directory = client.app.state.settings.avatar_storage_dir
    assert not (directory / f"{old_id}.webp").exists()
    with db_session_factory() as db:
        new_id = db.scalar(select(User).where(User.login == "member")).avatar_id
    page = client.get("/settings")
    response = client.post(
        "/settings/avatar/delete",
        data={"csrf_token": csrf(page)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert not (directory / f"{new_id}.webp").exists()
    with db_session_factory() as db:
        assert db.scalar(select(User).where(User.login == "member")).avatar_id is None


def test_avatar_filename_cannot_escape_storage(client, db_session_factory, tmp_path):
    register(client)
    response = upload(
        client, image_bytes("PNG"), "../../outside.png", "image/png"
    )
    assert response.status_code == 303
    with db_session_factory() as db:
        avatar_id = db.scalar(select(User).where(User.login == "member")).avatar_id
    assert re.fullmatch(r"[0-9a-f]{32}", avatar_id)
    assert not (tmp_path / "outside.png").exists()


def test_avatar_file_is_removed_when_database_commit_fails(
    client, monkeypatch
):
    register(client)

    def fail_commit(_session):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(Session, "commit", fail_commit)
    with pytest.raises(RuntimeError):
        upload(client, image_bytes("PNG"), "avatar.png", "image/png")
    assert list(client.app.state.settings.avatar_storage_dir.glob("*.webp")) == []


def test_anonymous_user_cannot_upload_avatar(client):
    page = client.get("/")
    response = client.post(
        "/settings/avatar",
        data={"csrf_token": csrf(page)},
        files={"avatar": ("avatar.png", image_bytes("PNG"), "image/png")},
    )
    assert response.status_code == 401


def test_cli_create_admin_uses_argon2_password(
    monkeypatch,
    db_session_factory,
):
    from app import cli as cli_module

    password = "cli operator password 2026"
    answers = iter((password, password))
    monkeypatch.setattr(cli_module, "SessionLocal", db_session_factory)
    monkeypatch.setattr(cli_module.getpass, "getpass", lambda _prompt: next(answers))

    cli_module.create_admin("cli_operator", "CLI Operator")

    with db_session_factory() as db:
        admin = db.scalar(select(User).where(User.login == "cli_operator"))

    assert admin is not None
    assert admin.is_admin is True
    assert verify_password(admin.password_hash, password) is True


def test_ordinary_user_cannot_access_admin(client):
    register(client)
    assert client.get("/admin").status_code == 403


def test_forged_admin_flag_does_not_escalate_privileges(
    client, db_session_factory
):
    register(client)
    with db_session_factory() as db:
        user = db.scalar(select(User).where(User.login == "member"))
        payload = {
            "user_id": user.id,
            "session_version": user.session_version,
            "is_admin": True,
        }
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    signed = TimestampSigner(
        "test-secret-key-that-is-at-least-32-characters"
    ).sign(encoded.encode()).decode()
    client.cookies.set("opinion_session", signed)
    assert client.get("/admin").status_code == 403


def test_logout_invalidates_a_copied_session_cookie(client):
    register(client)
    stolen_cookie = client.cookies.get("opinion_session")
    logout(client)
    client.cookies.set("opinion_session", stolen_cookie)
    response = client.get("/me", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_admin_queue_moderation_soft_deletes_post_and_writes_audit(
    client, db_session_factory
):
    post_id = create_post(client, "Контент для проверки")
    create_report(client, post_id)
    create_admin(db_session_factory)
    login(client, "operator")
    dashboard = client.get("/admin")
    assert dashboard.status_code == 200
    assert "Проверить публикацию" in dashboard.text
    detail = client.get("/admin/reports/1")
    response = client.post(
        "/admin/reports/1/action",
        data={
            "csrf_token": csrf(detail),
            "action": "delete",
            "reason": "Нарушение подтверждено",
            "rule_version_id": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with db_session_factory() as db:
        assert db.get(Post, post_id).is_deleted
        assert db.get(Report, 1).status == "resolved"
        assert db.scalar(select(func.count(ModerationAudit.id))) == 1
    assert "Контент для проверки" not in client.get("/").text
    assert "Пост удалён модерацией" in client.get(f"/posts/{post_id}").text
    audit = client.get("/admin/audit")
    assert "Нарушение подтверждено" in audit.text


def test_soft_deleted_comment_preserves_child_branch(
    client, db_session_factory
):
    post_id = create_post(client)
    root = create_comment(client, post_id, "Удаляемый родитель")
    root_id = int(root.headers["location"].split("#comment-")[-1])
    child = create_comment(client, post_id, "Дочерний ответ", root_id)
    child_id = int(child.headers["location"].split("#comment-")[-1])
    page = client.get(f"/reports/new?target_type=comment&target_id={root_id}")
    client.post(
        "/reports",
        data={
            "csrf_token": csrf(page),
            "target_type": "comment",
            "target_id": root_id,
            "reason": "Проверить комментарий",
        },
    )
    create_admin(db_session_factory)
    login(client, "operator")
    detail = client.get("/admin/reports/1")
    client.post(
        "/admin/reports/1/action",
        data={
            "csrf_token": csrf(detail),
            "action": "delete",
            "reason": "Подтверждённая угроза",
            "rule_version_id": "1",
        },
    )
    page = client.get(f"/posts/{post_id}")
    assert "Комментарий удалён модерацией" in page.text
    assert "Дочерний ответ" in page.text
    assert f'id="comment-{child_id}"' in page.text


def test_cross_post_parent_and_depth_limit_are_rejected(client):
    first = create_post(client, "Первый")
    second = create_post(client, "Второй")
    root = create_comment(client, first, "Корень")
    root_id = int(root.headers["location"].split("#comment-")[-1])
    cross = create_comment(client, second, "Чужой parent", root_id)
    assert cross.status_code == 422

    client.app.state.settings.max_comment_depth = 2
    child = create_comment(client, first, "Уровень два", root_id)
    child_id = int(child.headers["location"].split("#comment-")[-1])
    too_deep = create_comment(client, first, "Лишний уровень", child_id)
    assert too_deep.status_code == 422


def test_cycle_in_database_does_not_recurse_forever(db_session_factory):
    with db_session_factory() as db:
        first = Comment(
            post_id=1,
            body="one",
            author_id=None,
            author_alias="anon-1111",
            author_avatar="anon-avatar-1",
        )
        second = Comment(
            post_id=1,
            body="two",
            author_id=None,
            author_alias="anon-2222",
            author_avatar="anon-avatar-2",
        )
        db.add_all([first, second])
        db.flush()
        first.parent_id = second.id
        second.parent_id = first.id
        comments = [first, second]
        tree = build_comment_tree(comments)
        assert len(tree) == 2


def test_rate_limit_blocks_repeated_publication(client):
    client.app.state.limiter.limit = 1
    create_post(client, "Первый разрешён")
    page = client.get("/")
    blocked = client.post(
        "/posts", data={"csrf_token": csrf(page), "body": "Второй заблокирован"}
    )
    assert blocked.status_code == 429


def test_whitespace_only_content_and_invalid_ids_are_rejected(client):
    page = client.get("/")
    empty = client.post(
        "/posts",
        data={"csrf_token": csrf(page), "body": " \r\n\t "},
    )
    assert empty.status_code == 422
    assert client.get("/posts/-1").status_code == 404
    assert client.get("/posts/not-an-id").status_code == 422


def test_control_characters_are_rejected_in_display_name(
    client, db_session_factory
):
    register(client)
    page = client.get("/settings")
    response = client.post(
        "/settings/profile",
        data={
            "csrf_token": csrf(page),
            "display_name": "Имя\u202eскрытым текстом",
        },
    )
    assert response.status_code == 422
    with db_session_factory() as db:
        user = db.scalar(select(User).where(User.login == "member"))
        assert user.display_name == "Участник"


def test_tampered_session_cookie_is_rejected(client):
    register(client)
    cookie = client.cookies.get("opinion_session")
    assert cookie
    parts = cookie.split(".")
    signature = parts[-1]
    index = len(signature) // 2
    replacement = "A" if signature[index] != "A" else "B"
    parts[-1] = signature[:index] + replacement + signature[index + 1 :]
    client.cookies.set("opinion_session", ".".join(parts))
    response = client.get("/me", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_session_cookie_contains_no_login_or_password(client):
    register(client, login="private_cookie_login")
    cookie = client.cookies.get("opinion_session")
    assert cookie
    payload = base64.b64decode(cookie.split(".", 1)[0])
    assert b"private_cookie_login" not in payload
    assert PASSWORD.encode() not in payload
    assert b"password" not in payload.lower()


def test_html_errors_have_consistent_privacy_and_security_headers(client):
    responses = (
        client.get("/missing-page"),
        client.get("/posts/not-an-id"),
        client.post("/posts", headers={"content-length": "99999999"}),
    )
    assert [response.status_code for response in responses] == [404, 422, 413]
    for response in responses:
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["content-security-policy"].startswith(
            "default-src 'self'"
        )
        assert response.headers["cross-origin-opener-policy"] == "same-origin"
        assert response.headers["cross-origin-resource-policy"] == "same-origin"

    assert "Страница не найдена" in responses[0].text
    assert "Некорректный запрос" in responses[1].text


def test_admin_action_requires_csrf(client, db_session_factory):
    post_id = create_post(client)
    create_report(client, post_id)
    create_admin(db_session_factory)
    login(client, "operator")
    response = client.post(
        "/admin/reports/1/action",
        data={"csrf_token": "wrong", "action": "delete", "reason": ""},
    )
    assert response.status_code == 403
