from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(40), index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    avatar_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    session_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    posts: Mapped[list[Post]] = relationship(
        back_populates="author", foreign_keys="Post.author_id"
    )
    comments: Mapped[list[Comment]] = relationship(
        back_populates="author", foreign_keys="Comment.author_id"
    )

    @property
    def avatar_url(self) -> str | None:
        return f"/media/avatars/{self.avatar_id}.webp" if self.avatar_id else None


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        CheckConstraint(
            "(author_id IS NOT NULL AND author_alias IS NULL AND author_avatar IS NULL) OR "
            "(author_id IS NULL AND author_alias IS NOT NULL AND author_avatar IS NOT NULL)",
            name="ck_posts_exactly_one_author",
        ),
        Index("ix_posts_discussed", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    author_alias: Mapped[str | None] = mapped_column(String(16), nullable=True)
    author_avatar: Mapped[str | None] = mapped_column(String(16), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    deleted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )

    author: Mapped[User | None] = relationship(
        back_populates="posts", foreign_keys=[author_id]
    )
    deleted_by: Mapped[User | None] = relationship(foreign_keys=[deleted_by_id])
    comments: Mapped[list[Comment]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )

    @property
    def author_name(self) -> str:
        return self.author.display_name if self.author else (self.author_alias or "anonymous")

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (
        CheckConstraint(
            "(author_id IS NOT NULL AND author_alias IS NULL AND author_avatar IS NULL) OR "
            "(author_id IS NULL AND author_alias IS NOT NULL AND author_avatar IS NOT NULL)",
            name="ck_comments_exactly_one_author",
        ),
        Index("ix_comments_post_created", "post_id", "created_at"),
        Index("ix_comments_decision_created", "moderation_decision_id", "created_at"),
        Index("ix_comments_parent_created", "parent_id", "created_at"),
        CheckConstraint(
            "(post_id IS NOT NULL AND moderation_decision_id IS NULL) OR "
            "(post_id IS NULL AND moderation_decision_id IS NOT NULL)",
            name="ck_comments_exactly_one_discussion",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int | None] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    moderation_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("moderation_decisions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("comments.id", ondelete="CASCADE"), nullable=True, index=True
    )
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    author_alias: Mapped[str | None] = mapped_column(String(16), nullable=True)
    author_avatar: Mapped[str | None] = mapped_column(String(16), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    deleted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )

    post: Mapped[Post | None] = relationship(back_populates="comments")
    moderation_decision: Mapped[ModerationDecision | None] = relationship(
        back_populates="comments", foreign_keys=[moderation_decision_id]
    )
    author: Mapped[User | None] = relationship(
        back_populates="comments", foreign_keys=[author_id]
    )
    deleted_by: Mapped[User | None] = relationship(foreign_keys=[deleted_by_id])
    parent: Mapped[Comment | None] = relationship(
        remote_side="Comment.id", back_populates="children", foreign_keys=[parent_id]
    )
    children: Mapped[list[Comment]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )

    @property
    def author_name(self) -> str:
        return self.author.display_name if self.author else (self.author_alias or "anonymous")

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint(
            "(post_id IS NOT NULL AND comment_id IS NULL) OR "
            "(post_id IS NULL AND comment_id IS NOT NULL)",
            name="ck_reports_exactly_one_target",
        ),
        CheckConstraint(
            "status IN ('new', 'resolved', 'dismissed')",
            name="ck_reports_status",
        ),
        Index("ix_reports_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int | None] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    comment_id: Mapped[int | None] = mapped_column(
        ForeignKey("comments.id", ondelete="CASCADE"), nullable=True, index=True
    )
    reporter_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reporter_alias: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(16), default="new", server_default="new")
    resolution_action: Mapped[str | None] = mapped_column(String(24), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("moderation_decisions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    post: Mapped[Post | None] = relationship(foreign_keys=[post_id])
    comment: Mapped[Comment | None] = relationship(foreign_keys=[comment_id])
    resolved_by: Mapped[User | None] = relationship(foreign_keys=[resolved_by_id])
    decision: Mapped[ModerationDecision | None] = relationship(
        back_populates="reports", foreign_keys=[decision_id]
    )

    @property
    def target_type(self) -> str:
        return "post" if self.post_id is not None else "comment"

    @property
    def target_id(self) -> int:
        return self.post_id if self.post_id is not None else int(self.comment_id)


class ModerationAudit(Base):
    __tablename__ = "moderation_audit"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('post', 'comment')", name="ck_audit_target_type"
        ),
        Index("ix_audit_created", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    operator_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    report_id: Mapped[int | None] = mapped_column(
        ForeignKey("reports.id", ondelete="SET NULL"), nullable=True, index=True
    )
    decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("moderation_decisions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(24))
    target_type: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    operator: Mapped[User] = relationship(foreign_keys=[operator_id])
    report: Mapped[Report | None] = relationship(foreign_keys=[report_id])
    decision: Mapped[ModerationDecision | None] = relationship(foreign_keys=[decision_id])


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    current_version_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "rule_versions.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_rules_current_version_id_rule_versions",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    versions: Mapped[list[RuleVersion]] = relationship(
        back_populates="rule", foreign_keys="RuleVersion.rule_id"
    )
    current_version: Mapped[RuleVersion | None] = relationship(
        foreign_keys=[current_version_id], post_update=True
    )


class RuleVersion(Base):
    __tablename__ = "rule_versions"
    __table_args__ = (
        UniqueConstraint("rule_id", "version", name="uq_rule_versions_rule_version"),
        CheckConstraint("version > 0", name="ck_rule_versions_positive_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("rules.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(120))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    rule: Mapped[Rule] = relationship(back_populates="versions", foreign_keys=[rule_id])
    decisions: Mapped[list[ModerationDecision]] = relationship(back_populates="rule_version")


@event.listens_for(RuleVersion, "before_update")
@event.listens_for(RuleVersion, "before_delete")
def _rule_versions_are_immutable(_mapper, _connection, _target) -> None:
    raise ValueError("Исторические версии правил неизменяемы")


class ModerationDecision(Base):
    __tablename__ = "moderation_decisions"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('post', 'comment')", name="ck_decisions_target_type"
        ),
        CheckConstraint(
            "status IN ('active', 'reversed')", name="ck_decisions_status"
        ),
        UniqueConstraint("active_target_key", name="uq_decisions_active_target_key"),
        Index("ix_decisions_target", "target_type", "target_id"),
        Index("ix_decisions_created", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    target_type: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[int] = mapped_column(Integer)
    active_target_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rule_version_id: Mapped[int] = mapped_column(
        ForeignKey("rule_versions.id", ondelete="RESTRICT"), index=True
    )
    moderator_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    explanation: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    rule_version: Mapped[RuleVersion] = relationship(back_populates="decisions")
    moderator: Mapped[User] = relationship(foreign_keys=[moderator_id])
    reviews: Mapped[list[ModerationReview]] = relationship(
        back_populates="decision", order_by="ModerationReview.created_at"
    )
    comments: Mapped[list[Comment]] = relationship(
        back_populates="moderation_decision",
        foreign_keys="Comment.moderation_decision_id",
    )
    reports: Mapped[list[Report]] = relationship(
        back_populates="decision", foreign_keys="Report.decision_id"
    )

    @property
    def is_active(self) -> bool:
        return self.status == "active"


class ModerationReview(Base):
    __tablename__ = "moderation_reviews"
    __table_args__ = (
        CheckConstraint("action IN ('reversed')", name="ck_reviews_action"),
        Index("ix_reviews_decision_created", "decision_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    decision_id: Mapped[int] = mapped_column(
        ForeignKey("moderation_decisions.id", ondelete="RESTRICT"), index=True
    )
    reviewer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    action: Mapped[str] = mapped_column(String(16))
    explanation: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    decision: Mapped[ModerationDecision] = relationship(back_populates="reviews")
    reviewer: Mapped[User] = relationship(foreign_keys=[reviewer_id])
