from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

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


def target_key(target_type: str, target_id: int) -> str:
    if target_type not in {"post", "comment"} or target_id < 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Некорректная цель модерации.")
    return f"{target_type}:{target_id}"


def load_target(db: Session, target_type: str, target_id: int) -> Post | Comment:
    model = Post if target_type == "post" else Comment if target_type == "comment" else None
    target = db.get(model, target_id) if model else None
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Объект модерации не найден.")
    return target


def valid_rule_version(db: Session, rule_version_id: int | None) -> RuleVersion:
    if rule_version_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Для удаления необходимо выбрать действующую версию правила.",
        )
    version = db.scalar(
        select(RuleVersion)
        .join(Rule, Rule.id == RuleVersion.rule_id)
        .where(
            RuleVersion.id == rule_version_id,
            Rule.is_active.is_(True),
            Rule.current_version_id == RuleVersion.id,
        )
        .options(joinedload(RuleVersion.rule))
    )
    if not version:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Выбрана несуществующая или неактивная версия правила.",
        )
    return version


def active_rule_versions(db: Session) -> list[RuleVersion]:
    return list(
        db.scalars(
            select(RuleVersion)
            .join(Rule, Rule.id == RuleVersion.rule_id)
            .where(Rule.is_active.is_(True), Rule.current_version_id == RuleVersion.id)
            .options(joinedload(RuleVersion.rule))
            .order_by(Rule.code)
        )
    )


def create_decision(
    db: Session,
    *,
    target_type: str,
    target_id: int,
    rule_version_id: int | None,
    moderator: User,
    explanation: str,
    report_id: int | None = None,
) -> tuple[ModerationDecision, bool]:
    key = target_key(target_type, target_id)
    version = valid_rule_version(db, rule_version_id)
    existing = db.scalar(
        select(ModerationDecision)
        .where(ModerationDecision.active_target_key == key)
        .with_for_update()
    )
    if existing:
        _resolve_related_reports(db, existing, moderator, report_id)
        return existing, False

    target = load_target(db, target_type, target_id)
    if target.is_deleted:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Удалённый объект не имеет активного структурированного решения.",
        )
    explanation = explanation.strip()
    if not explanation:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Коротко поясните применение правила к этому объекту.",
        )
    if len(explanation) > 300:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Пояснение слишком длинное.")

    now = datetime.now(timezone.utc)
    decision = ModerationDecision(
        target_type=target_type,
        target_id=target_id,
        active_target_key=key,
        rule_version_id=version.id,
        moderator_id=moderator.id,
        explanation=explanation,
        status="active",
        created_at=now,
    )
    db.add(decision)
    db.flush()
    target.deleted_at = now
    target.deleted_by_id = moderator.id
    _resolve_related_reports(db, decision, moderator, report_id, now=now)
    db.add(
        ModerationAudit(
            operator_id=moderator.id,
            report_id=report_id,
            decision_id=decision.id,
            action="delete",
            target_type=target_type,
            target_id=target_id,
            reason=explanation,
            created_at=now,
        )
    )
    return decision, True


def _resolve_related_reports(
    db: Session,
    decision: ModerationDecision,
    moderator: User,
    report_id: int | None,
    *,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(timezone.utc)
    target_filter = (
        Report.post_id == decision.target_id
        if decision.target_type == "post"
        else Report.comment_id == decision.target_id
    )
    reports = list(
        db.scalars(
            select(Report)
            .where(target_filter, Report.status == "new")
            .with_for_update()
        )
    )
    if report_id and not any(report.id == report_id for report in reports):
        report = db.get(Report, report_id)
        if report and report.status == "new":
            reports.append(report)
    for report in reports:
        report.status = "resolved"
        report.resolution_action = "delete"
        report.resolved_at = now
        report.resolved_by_id = moderator.id
        report.decision_id = decision.id


def reverse_decision(
    db: Session,
    *,
    decision_id: int,
    reviewer: User,
    explanation: str,
) -> ModerationDecision:
    decision = db.scalar(
        select(ModerationDecision)
        .where(ModerationDecision.id == decision_id)
        .with_for_update()
    )
    if not decision:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Решение не найдено.")
    if not decision.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "Решение уже отменено.")

    target = load_target(db, decision.target_type, decision.target_id)
    explanation = explanation.strip()
    if len(explanation) > 300:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Пояснение слишком длинное.")
    now = datetime.now(timezone.utc)
    decision.status = "reversed"
    decision.active_target_key = None
    target.deleted_at = None
    target.deleted_by_id = None
    db.add(
        ModerationReview(
            decision_id=decision.id,
            reviewer_id=reviewer.id,
            action="reversed",
            explanation=explanation or None,
            created_at=now,
        )
    )
    db.add(
        ModerationAudit(
            operator_id=reviewer.id,
            decision_id=decision.id,
            action="reverse",
            target_type=decision.target_type,
            target_id=decision.target_id,
            reason=explanation or None,
            created_at=now,
        )
    )
    return decision


def active_decision_map(
    db: Session, target_type: str, target_ids: list[int]
) -> dict[int, ModerationDecision]:
    if not target_ids:
        return {}
    decisions = db.scalars(
        select(ModerationDecision)
        .where(
            ModerationDecision.target_type == target_type,
            ModerationDecision.target_id.in_(target_ids),
            ModerationDecision.status == "active",
        )
        .options(
            joinedload(ModerationDecision.rule_version).joinedload(RuleVersion.rule)
        )
    )
    return {decision.target_id: decision for decision in decisions}


def decision_context_url(db: Session, decision: ModerationDecision) -> str | None:
    if decision.target_type == "post":
        return f"/posts/{decision.target_id}" if db.get(Post, decision.target_id) else None
    comment = db.get(Comment, decision.target_id)
    if not comment:
        return None
    if comment.post_id:
        return f"/posts/{comment.post_id}#comment-{comment.id}"
    if comment.moderation_decision_id:
        return f"/moderation/decisions/{comment.moderation_decision_id}#comment-{comment.id}"
    return None
