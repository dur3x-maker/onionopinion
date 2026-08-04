from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
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
        Index("ix_comments_parent_created", "parent_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), index=True
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

    post: Mapped[Post] = relationship(back_populates="comments")
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    post: Mapped[Post | None] = relationship(foreign_keys=[post_id])
    comment: Mapped[Comment | None] = relationship(foreign_keys=[comment_id])
    resolved_by: Mapped[User | None] = relationship(foreign_keys=[resolved_by_id])

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
    action: Mapped[str] = mapped_column(String(24))
    target_type: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    operator: Mapped[User] = relationship(foreign_keys=[operator_id])
    report: Mapped[Report | None] = relationship(foreign_keys=[report_id])

