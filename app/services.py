from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Comment, User
from app.security import get_anonymous_alias, get_anonymous_avatar


@dataclass
class CommentNode:
    comment: Comment
    children: list[CommentNode] = field(default_factory=list)
    descendant_count: int = 0


def build_comment_tree(comments: list[Comment]) -> list[CommentNode]:
    """Build and count a tree iteratively; malformed cycles become safe roots."""
    nodes = {comment.id: CommentNode(comment) for comment in comments}
    roots: list[CommentNode] = []

    for comment in comments:
        node = nodes[comment.id]
        parent = nodes.get(comment.parent_id) if comment.parent_id else None
        if not parent or parent is node or _would_cycle(comment.id, comment.parent_id, nodes):
            roots.append(node)
        else:
            parent.children.append(node)

    stack: list[tuple[CommentNode, bool]] = [(root, False) for root in reversed(roots)]
    while stack:
        node, visited = stack.pop()
        if visited:
            node.descendant_count = sum(
                1 + child.descendant_count for child in node.children
            )
            continue
        stack.append((node, True))
        stack.extend((child, False) for child in reversed(node.children))
    return roots


def _would_cycle(
    comment_id: int, parent_id: int | None, nodes: dict[int, CommentNode]
) -> bool:
    seen = {comment_id}
    cursor = parent_id
    while cursor is not None and cursor in nodes:
        if cursor in seen:
            return True
        seen.add(cursor)
        cursor = nodes[cursor].comment.parent_id
    return False


def validate_comment_depth(db: Session, parent: Comment, max_depth: int) -> None:
    seen: set[int] = set()
    cursor: Comment | None = parent
    depth = 1
    while cursor is not None:
        if cursor.id in seen:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Обнаружена некорректная циклическая ветка.",
            )
        seen.add(cursor.id)
        if depth >= max_depth:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Достигнута максимальная безопасная глубина ветки.",
            )
        cursor = db.get(Comment, cursor.parent_id) if cursor.parent_id else None
        depth += 1


def current_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.scalar(select(User).where(User.id == user_id))
    if not user or request.session.get("session_version") != user.session_version:
        request.session.clear()
        return None
    return user


def require_user(request: Request, db: Session) -> User:
    user = current_user(request, db)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход.")
    return user


def require_admin(request: Request, db: Session) -> User:
    user = require_user(request, db)
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Доступ только для оператора.")
    return user


def author_fields(request: Request, user: User | None) -> dict[str, int | str | None]:
    if user:
        return {"author_id": user.id, "author_alias": None, "author_avatar": None}
    return {
        "author_id": None,
        "author_alias": get_anonymous_alias(request),
        "author_avatar": get_anonymous_avatar(request),
    }
