"""Avatars, moderation, audit log and soft delete.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-04
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("session_version", sa.Integer(), server_default="1", nullable=False),
    )

    op.add_column("posts", sa.Column("author_avatar", sa.String(length=16), nullable=True))
    op.add_column("posts", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("posts", sa.Column("deleted_by_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE posts SET author_avatar = "
        "'anon-avatar-' || (((id - 1) % 8) + 1)::text "
        "WHERE author_id IS NULL"
    )
    op.drop_constraint("ck_posts_exactly_one_author", "posts", type_="check")
    op.create_check_constraint(
        "ck_posts_exactly_one_author",
        "posts",
        "(author_id IS NOT NULL AND author_alias IS NULL AND author_avatar IS NULL) OR "
        "(author_id IS NULL AND author_alias IS NOT NULL AND author_avatar IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_posts_deleted_by_id_users",
        "posts",
        "users",
        ["deleted_by_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_posts_deleted_at", "posts", ["deleted_at"])

    op.add_column(
        "comments", sa.Column("author_avatar", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "comments", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("comments", sa.Column("deleted_by_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE comments SET author_avatar = "
        "'anon-avatar-' || (((id - 1) % 8) + 1)::text "
        "WHERE author_id IS NULL"
    )
    op.drop_constraint("ck_comments_exactly_one_author", "comments", type_="check")
    op.create_check_constraint(
        "ck_comments_exactly_one_author",
        "comments",
        "(author_id IS NOT NULL AND author_alias IS NULL AND author_avatar IS NULL) OR "
        "(author_id IS NULL AND author_alias IS NOT NULL AND author_avatar IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_comments_deleted_by_id_users",
        "comments",
        "users",
        ["deleted_by_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_comments_deleted_at", "comments", ["deleted_at"])

    op.add_column(
        "reports",
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'new'"),
            nullable=False,
        ),
    )
    op.add_column(
        "reports", sa.Column("resolution_action", sa.String(length=24), nullable=True)
    )
    op.add_column(
        "reports", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("reports", sa.Column("resolved_by_id", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_reports_status",
        "reports",
        "status IN ('new', 'resolved', 'dismissed')",
    )
    op.create_foreign_key(
        "fk_reports_resolved_by_id_users",
        "reports",
        "users",
        ["resolved_by_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_reports_status_created", "reports", ["status", "created_at"])

    op.create_table(
        "moderation_audit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "target_type IN ('post', 'comment')", name="ck_audit_target_type"
        ),
        sa.ForeignKeyConstraint(
            ["operator_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["report_id"], ["reports.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_created", "moderation_audit", ["created_at", "id"])
    op.create_index(
        "ix_moderation_audit_created_at", "moderation_audit", ["created_at"]
    )
    op.create_index(
        "ix_moderation_audit_operator_id", "moderation_audit", ["operator_id"]
    )
    op.create_index(
        "ix_moderation_audit_report_id", "moderation_audit", ["report_id"]
    )


def downgrade() -> None:
    op.drop_table("moderation_audit")

    op.drop_index("ix_reports_status_created", table_name="reports")
    op.drop_constraint(
        "fk_reports_resolved_by_id_users", "reports", type_="foreignkey"
    )
    op.drop_constraint("ck_reports_status", "reports", type_="check")
    op.drop_column("reports", "resolved_by_id")
    op.drop_column("reports", "resolved_at")
    op.drop_column("reports", "resolution_action")
    op.drop_column("reports", "status")

    op.drop_index("ix_comments_deleted_at", table_name="comments")
    op.drop_constraint(
        "fk_comments_deleted_by_id_users", "comments", type_="foreignkey"
    )
    op.drop_constraint("ck_comments_exactly_one_author", "comments", type_="check")
    op.create_check_constraint(
        "ck_comments_exactly_one_author",
        "comments",
        "(author_id IS NOT NULL AND author_alias IS NULL) OR "
        "(author_id IS NULL AND author_alias IS NOT NULL)",
    )
    op.drop_column("comments", "deleted_by_id")
    op.drop_column("comments", "deleted_at")
    op.drop_column("comments", "author_avatar")

    op.drop_index("ix_posts_deleted_at", table_name="posts")
    op.drop_constraint("fk_posts_deleted_by_id_users", "posts", type_="foreignkey")
    op.drop_constraint("ck_posts_exactly_one_author", "posts", type_="check")
    op.create_check_constraint(
        "ck_posts_exactly_one_author",
        "posts",
        "(author_id IS NOT NULL AND author_alias IS NULL) OR "
        "(author_id IS NULL AND author_alias IS NOT NULL)",
    )
    op.drop_column("posts", "deleted_by_id")
    op.drop_column("posts", "deleted_at")
    op.drop_column("posts", "author_avatar")

    op.drop_column("users", "session_version")
    op.drop_column("users", "is_admin")

