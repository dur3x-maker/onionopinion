import html
import re

from sqlalchemy import func, select

from app.models import Comment, Post, Report, User


def csrf(response) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, "CSRF field was not rendered"
    return html.unescape(match.group(1))


def register(client, login="quiet_user", display_name="Тихий голос"):
    page = client.get("/register")
    return client.post(
        "/register",
        data={
            "csrf_token": csrf(page),
            "login": login,
            "display_name": display_name,
            "password": "correct horse battery staple",
            "password_confirmation": "correct horse battery staple",
        },
        follow_redirects=False,
    )


def create_post(client, body="Первая мысль") -> int:
    page = client.get("/")
    response = client.post(
        "/posts",
        data={"csrf_token": csrf(page), "body": body},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return int(response.headers["location"].split("/")[-1])


def create_comment(client, post_id: int, body: str, parent_id=None) -> int:
    page = client.get(f"/posts/{post_id}")
    data = {"csrf_token": csrf(page), "body": body}
    if parent_id is not None:
        data["parent_id"] = str(parent_id)
    response = client.post(
        f"/posts/{post_id}/comments", data=data, follow_redirects=False
    )
    assert response.status_code == 303
    return int(response.headers["location"].split("#comment-")[-1])


def test_registration_login_logout(client, db_session_factory):
    response = register(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/me"

    with db_session_factory() as db:
        user = db.scalar(select(User).where(User.login == "quiet_user"))
        assert user is not None
        assert user.display_name == "Тихий голос"
        assert user.password_hash != "correct horse battery staple"
        assert user.password_hash.startswith("$argon2id$")

    feed = client.get("/")
    logout = client.post(
        "/logout", data={"csrf_token": csrf(feed)}, follow_redirects=False
    )
    assert logout.status_code == 303
    assert client.get("/me", follow_redirects=False).headers["location"] == "/login"

    login_page = client.get("/login")
    login = client.post(
        "/login",
        data={
            "csrf_token": csrf(login_page),
            "login": "quiet_user",
            "password": "correct horse battery staple",
        },
        follow_redirects=False,
    )
    assert login.status_code == 303
    assert login.headers["location"] == "/me"


def test_registered_and_anonymous_posts_keep_correct_authorship(
    client, db_session_factory
):
    register(client)
    registered_post_id = create_post(client, "Мысль с именем")

    feed = client.get("/")
    client.post("/logout", data={"csrf_token": csrf(feed)})
    anonymous_post_id = create_post(client, "Мысль без имени")

    with db_session_factory() as db:
        registered = db.get(Post, registered_post_id)
        anonymous = db.get(Post, anonymous_post_id)
        assert registered.author_id is not None
        assert registered.author_alias is None
        assert anonymous.author_id is None
        assert re.fullmatch(r"anon-[0-9a-f]{4}", anonymous.author_alias or "")


def test_comments_support_nested_replies(client, db_session_factory):
    post_id = create_post(client)
    root_id = create_comment(client, post_id, "Первый ответ")
    child_id = create_comment(client, post_id, "Ответ на ответ", root_id)
    grandchild_id = create_comment(client, post_id, "Ещё глубже", child_id)

    page = client.get(f"/posts/{post_id}")
    assert page.status_code == 200
    assert "Первый ответ" in page.text
    assert "Ответ на ответ" in page.text
    assert "Ещё глубже" in page.text
    assert page.text.count('class="comment depth-') == 3

    with db_session_factory() as db:
        grandchild = db.get(Comment, grandchild_id)
        assert grandchild.parent_id == child_id


def test_user_html_is_rendered_as_text(client):
    payload = '<script>alert("x")</script><b>не жирный</b>'
    post_id = create_post(client, payload)
    page = client.get(f"/posts/{post_id}")
    assert payload not in page.text
    assert "&lt;script&gt;" in page.text
    assert "&lt;b&gt;не жирный&lt;/b&gt;" in page.text
    assert "<script" not in page.text.lower()


def test_public_profile_never_exposes_login(client):
    register(client, login="private_login_73", display_name="Публичное имя")
    create_post(client, "Пост для профиля")
    me = client.get("/me", follow_redirects=False)
    profile = client.get(me.headers["location"])
    assert profile.status_code == 200
    assert "Публичное имя" in profile.text
    assert "Пост для профиля" in profile.text
    assert "private_login_73" not in profile.text


def test_report_is_persisted(client, db_session_factory):
    post_id = create_post(client)
    page = client.get(f"/posts/{post_id}")
    response = client.post(
        "/reports",
        data={
            "csrf_token": csrf(page),
            "target_type": "post",
            "target_id": post_id,
            "reason": "Публикация чужих данных",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with db_session_factory() as db:
        assert db.scalar(select(func.count(Report.id))) == 1
        report = db.scalar(select(Report))
        assert report.post_id == post_id
        assert report.comment_id is None


def test_mutating_forms_require_valid_csrf(client):
    response = client.post("/posts", data={"csrf_token": "wrong", "body": "Нет"})
    assert response.status_code == 403
