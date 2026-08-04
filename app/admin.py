from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Comment, ModerationAudit, Post, Report
from app.security import require_csrf
from app.services import require_admin

router = APIRouter(prefix="/admin")


def render(request: Request, template: str, **extra):
    from app.main import base_context

    return request.app.state.templates.TemplateResponse(
        request, template, base_context(request) | extra
    )


def report_options():
    return (
        joinedload(Report.post).joinedload(Post.author),
        joinedload(Report.comment).joinedload(Comment.author),
    )


@router.get("")
def admin_dashboard(
    request: Request,
    queue: str = "new",
    db: Session = Depends(get_db),
):
    operator = require_admin(request, db)
    statuses = ["new"] if queue == "new" else ["resolved", "dismissed"]
    reports = list(
        db.scalars(
            select(Report)
            .where(Report.status.in_(statuses))
            .options(*report_options())
            .order_by(desc(Report.created_at))
            .limit(100)
        )
    )
    return render(
        request,
        "admin/dashboard.html",
        title="Панель оператора",
        current_user=operator,
        reports=reports,
        queue=queue,
    )


@router.get("/reports/{report_id}")
def report_detail(
    request: Request, report_id: int, db: Session = Depends(get_db)
):
    operator = require_admin(request, db)
    report = db.scalar(
        select(Report).where(Report.id == report_id).options(*report_options())
    )
    if not report:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Жалоба не найдена.")
    target = report.post if report.post_id is not None else report.comment
    post = report.post if report.post_id is not None else report.comment.post
    parent_context: list[Comment] = []
    if report.comment and report.comment.parent_id:
        seen: set[int] = set()
        cursor = db.get(Comment, report.comment.parent_id)
        while cursor and cursor.id not in seen and len(parent_context) < 12:
            seen.add(cursor.id)
            parent_context.append(cursor)
            cursor = db.get(Comment, cursor.parent_id) if cursor.parent_id else None
        parent_context.reverse()
    return render(
        request,
        "admin/report_detail.html",
        title=f"Жалоба #{report.id}",
        current_user=operator,
        report=report,
        target=target,
        post=post,
        parent_context=parent_context,
    )


@router.post("/reports/{report_id}/action")
def moderate_report(
    request: Request,
    report_id: int,
    action: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    reason: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
):
    require_csrf(request, csrf_token)
    request.app.state.limiter.check(request, "admin")
    operator = require_admin(request, db)
    report = db.scalar(select(Report).where(Report.id == report_id).with_for_update())
    if not report:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Жалоба не найдена.")
    if report.status != "new":
        raise HTTPException(status.HTTP_409_CONFLICT, "Жалоба уже обработана.")
    if action not in {"keep", "delete", "dismiss"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Неизвестное действие.")
    reason = reason.strip()
    if len(reason) > 300:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Причина слишком длинная.")

    target = db.get(Post, report.post_id) if report.post_id else db.get(Comment, report.comment_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Объект жалобы не найден.")
    now = datetime.now(timezone.utc)
    if action == "delete" and not target.is_deleted:
        target.deleted_at = now
        target.deleted_by_id = operator.id
    report.status = "dismissed" if action == "dismiss" else "resolved"
    report.resolution_action = action
    report.resolved_at = now
    report.resolved_by_id = operator.id
    db.add(
        ModerationAudit(
            operator_id=operator.id,
            report_id=report.id,
            action=action,
            target_type=report.target_type,
            target_id=report.target_id,
            reason=reason or None,
        )
    )
    db.commit()
    request.session["notice"] = "Решение сохранено в журнале модерации."
    return RedirectResponse(
        f"/admin/reports/{report.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/audit")
def audit_log(request: Request, db: Session = Depends(get_db)):
    operator = require_admin(request, db)
    entries = list(
        db.scalars(
            select(ModerationAudit)
            .options(joinedload(ModerationAudit.operator))
            .order_by(desc(ModerationAudit.created_at))
            .limit(200)
        )
    )
    return render(
        request,
        "admin/audit.html",
        title="Журнал модерации",
        current_user=operator,
        entries=entries,
    )
