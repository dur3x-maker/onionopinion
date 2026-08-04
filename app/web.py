from __future__ import annotations

import unicodedata
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.avatars import delete_avatar_file, process_avatar
from app.database import get_db
from app.models import Comment, Post, Report, User
from app.security import (
    hash_password,
    require_csrf,
    validate_login,
    validate_password,
    verify_password,
)
from app.services import (
    author_fields,
    build_comment_tree,
    current_user,
    require_user,
    validate_comment_depth,
)

router = APIRouter()


def context(request: Request, **extra) -> dict:
    from app.main import base_context

    return base_context(request) | extra


def render(request: Request, template: str, status_code: int = 200, **extra):
    return request.app.state.templates.TemplateResponse(
        request, template, context(request, **extra), status_code=status_code
    )


def clean_body(value: str, max_length: int, label: str) -> str:
    value = value.strip()
    if not value:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"{label} не может быть пустым."
        )
    if len(value) > max_length:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{label} не может быть длиннее {max_length} символов.",
        )
    return value


def clean_display_name(value: str) -> str:
    value = value.strip()
    if not 2 <= len(value) <= 40:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Отображаемое имя должно содержать от 2 до 40 символов.",
        )
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Отображаемое имя содержит недопустимые управляющие символы.",
        )
    return value


def require_rate_limit(request: Request, action: str) -> None:
    request.app.state.limiter.check(request, action)


def start_user_session(request: Request, user: User) -> None:
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["session_version"] = user.session_version


@router.get("/healthz")
def healthcheck(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok"}


@router.get("/")
def feed(
    request: Request,
    sort: str = "new",
    page: int = 1,
    db: Session = Depends(get_db),
):
    if sort not in {"new", "discussed"}:
        sort = "new"
    page = max(page, 1)
    per_page = request.app.state.settings.posts_per_page
    comment_count = (
        select(func.count(Comment.id))
        .where(Comment.post_id == Post.id, Comment.deleted_at.is_(None))
        .correlate(Post)
        .scalar_subquery()
    )
    statement = (
        select(Post, comment_count.label("comment_count"))
        .where(Post.deleted_at.is_(None))
        .options(joinedload(Post.author))
    )
    if sort == "discussed":
        statement = statement.order_by(desc(comment_count), desc(Post.created_at))
    else:
        statement = statement.order_by(desc(Post.created_at))
    total = (
        db.scalar(select(func.count(Post.id)).where(Post.deleted_at.is_(None))) or 0
    )
    rows = db.execute(statement.offset((page - 1) * per_page).limit(per_page)).all()
    return render(
        request,
        "feed.html",
        title="Лента",
        current_user=current_user(request, db),
        posts=[{"post": row[0], "comment_count": row[1]} for row in rows],
        sort=sort,
        page=page,
        has_previous=page > 1,
        has_next=page * per_page < total,
        post_max_length=request.app.state.settings.post_max_length,
    )


@router.post("/posts")
def create_post(
    request: Request,
    body: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_rate_limit(request, "publish")
    body = clean_body(body, request.app.state.settings.post_max_length, "Пост")
    post = Post(body=body, **author_fields(request, current_user(request, db)))
    db.add(post)
    db.commit()
    return RedirectResponse(f"/posts/{post.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/posts/{post_id}")
def post_detail(request: Request, post_id: int, db: Session = Depends(get_db)):
    post = db.scalar(
        select(Post).where(Post.id == post_id).options(joinedload(Post.author))
    )
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пост не найден.")
    comments = list(
        db.scalars(
            select(Comment)
            .where(Comment.post_id == post_id)
            .options(joinedload(Comment.author))
            .order_by(Comment.created_at, Comment.id)
        )
    )
    return render(
        request,
        "post.html",
        title=f"Обсуждение #{post.id}",
        current_user=current_user(request, db),
        post=post,
        comment_tree=build_comment_tree(comments),
        comment_count=sum(not comment.is_deleted for comment in comments),
        comment_max_length=request.app.state.settings.comment_max_length,
    )


@router.post("/posts/{post_id}/comments")
def create_comment(
    request: Request,
    post_id: int,
    body: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    parent_id: Annotated[int | None, Form()] = None,
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_rate_limit(request, "publish")
    post = db.get(Post, post_id)
    if not post or post.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Активный пост не найден.")
    if parent_id is not None:
        parent = db.get(Comment, parent_id)
        if not parent or parent.post_id != post_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Некорректный ответ."
            )
        validate_comment_depth(
            db, parent, request.app.state.settings.max_comment_depth
        )
    body = clean_body(
        body, request.app.state.settings.comment_max_length, "Комментарий"
    )
    comment = Comment(
        post_id=post_id,
        parent_id=parent_id,
        body=body,
        **author_fields(request, current_user(request, db)),
    )
    db.add(comment)
    db.commit()
    return RedirectResponse(
        f"/posts/{post_id}#comment-{comment.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/register")
def register_form(request: Request, db: Session = Depends(get_db)):
    if current_user(request, db):
        return RedirectResponse("/me", status_code=status.HTTP_303_SEE_OTHER)
    return render(request, "register.html", title="Регистрация")


@router.post("/register")
def register(
    request: Request,
    login: Annotated[str, Form()],
    display_name: Annotated[str, Form()],
    password: Annotated[str, Form()],
    password_confirmation: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_rate_limit(request, "register")
    login = login.strip().lower()
    try:
        display_name = clean_display_name(display_name)
        error = validate_login(login) or validate_password(password)
        if error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, error)
        if password != password_confirmation:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Пароли не совпадают."
            )
    except HTTPException as exc:
        return render(
            request,
            "register.html",
            status_code=exc.status_code,
            title="Регистрация",
            error=str(exc.detail),
            login=login,
            display_name=display_name,
        )
    user = User(
        login=login, display_name=display_name, password_hash=hash_password(password)
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "register.html",
            status_code=409,
            title="Регистрация",
            error="Этот логин уже занят.",
            display_name=display_name,
        )
    start_user_session(request, user)
    return RedirectResponse("/me", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login")
def login_form(request: Request, db: Session = Depends(get_db)):
    if current_user(request, db):
        return RedirectResponse("/me", status_code=status.HTTP_303_SEE_OTHER)
    return render(request, "login.html", title="Вход")


@router.post("/login")
def login(
    request: Request,
    login: Annotated[str, Form()],
    password: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_rate_limit(request, "auth")
    user = db.scalar(select(User).where(User.login == login.strip().lower()))
    if not user or not verify_password(user.password_hash, password):
        return render(
            request,
            "login.html",
            status_code=401,
            title="Вход",
            error="Неверный логин или пароль.",
            login=login,
        )
    user.session_version += 1
    db.commit()
    start_user_session(request, user)
    return RedirectResponse("/me", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
def logout(
    request: Request,
    csrf_token: Annotated[str, Form()],
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    user = current_user(request, db)
    if user:
        user.session_version += 1
        db.commit()
    request.session.clear()
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/me")
def own_profile(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(f"/users/{user.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/users/{user_id}")
def public_profile(request: Request, user_id: int, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Профиль не найден.")
    posts = list(
        db.scalars(
            select(Post)
            .where(Post.author_id == user.id, Post.deleted_at.is_(None))
            .order_by(desc(Post.created_at))
            .limit(50)
        )
    )
    comments = list(
        db.scalars(
            select(Comment)
            .where(Comment.author_id == user.id, Comment.deleted_at.is_(None))
            .order_by(desc(Comment.created_at))
            .limit(50)
        )
    )
    return render(
        request,
        "profile.html",
        title=user.display_name,
        current_user=current_user(request, db),
        profile=user,
        posts=posts,
        comments=comments,
        is_owner=request.session.get("user_id") == user.id,
    )


@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    return render(
        request,
        "settings.html",
        title="Настройки профиля",
        current_user=user,
    )


@router.post("/settings/profile")
def update_profile(
    request: Request,
    display_name: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_rate_limit(request, "profile")
    user = require_user(request, db)
    user.display_name = clean_display_name(display_name)
    db.commit()
    request.session["notice"] = "Отображаемое имя обновлено."
    return RedirectResponse("/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/avatar")
def upload_avatar(
    request: Request,
    avatar: Annotated[UploadFile, File()],
    csrf_token: Annotated[str, Form()],
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_rate_limit(request, "avatar")
    user = require_user(request, db)
    settings = request.app.state.settings
    new_avatar_id = process_avatar(avatar, settings)
    old_avatar_id = user.avatar_id
    user.avatar_id = new_avatar_id
    try:
        db.commit()
    except Exception:
        db.rollback()
        delete_avatar_file(settings, new_avatar_id)
        raise
    delete_avatar_file(settings, old_avatar_id)
    request.session["notice"] = "Аватар обработан и сохранён."
    return RedirectResponse("/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/avatar/delete")
def remove_avatar(
    request: Request,
    csrf_token: Annotated[str, Form()],
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_rate_limit(request, "avatar")
    user = require_user(request, db)
    old_avatar_id = user.avatar_id
    user.avatar_id = None
    db.commit()
    delete_avatar_file(request.app.state.settings, old_avatar_id)
    request.session["notice"] = "Установлен стандартный аватар."
    return RedirectResponse("/settings", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/reports/new")
def report_form(
    request: Request,
    target_type: str,
    target_id: int,
    db: Session = Depends(get_db),
):
    if target_type == "post":
        target = db.get(Post, target_id)
        post_id = target.id if target else None
    elif target_type == "comment":
        target = db.get(Comment, target_id)
        post_id = target.post_id if target else None
    else:
        target = None
        post_id = None
    if not target or target.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Публикация не найдена.")
    return render(
        request,
        "report_form.html",
        title="Пожаловаться",
        current_user=current_user(request, db),
        target=target,
        target_type=target_type,
        target_id=target_id,
        post_id=post_id,
    )


@router.post("/reports")
def create_report(
    request: Request,
    target_type: Annotated[str, Form()],
    target_id: Annotated[int, Form()],
    reason: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    require_rate_limit(request, "report")
    reason = clean_body(reason, 300, "Причина")
    user = current_user(request, db)
    common = {
        "reporter_user_id": user.id if user else None,
        "reporter_alias": None if user else request.session.get("anonymous_alias"),
        "reason": reason,
    }
    if target_type == "post":
        post = db.get(Post, target_id)
        if not post or post.is_deleted:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Пост не найден.")
        report = Report(post_id=post.id, comment_id=None, **common)
        return_to = f"/posts/{post.id}"
    elif target_type == "comment":
        comment = db.get(Comment, target_id)
        if not comment or comment.is_deleted:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Комментарий не найден.")
        report = Report(post_id=None, comment_id=comment.id, **common)
        return_to = f"/posts/{comment.post_id}#comment-{comment.id}"
    else:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Некорректная цель."
        )
    db.add(report)
    db.commit()
    request.session["notice"] = "Жалоба сохранена."
    return RedirectResponse(return_to, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/rules")
def rules(request: Request, db: Session = Depends(get_db)):
    return render(
        request,
        "rules.html",
        title="Правила",
        current_user=current_user(request, db),
    )
