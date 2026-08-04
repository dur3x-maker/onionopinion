"""Transparent versioned moderation and decision discussions.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-04
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rules_code", "rules", ["code"], unique=True)

    op.create_table(
        "rule_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_rule_versions_positive_version"),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id", "version", name="uq_rule_versions_rule_version"),
    )
    op.create_index("ix_rule_versions_rule_id", "rule_versions", ["rule_id"])
    op.create_foreign_key(
        "fk_rules_current_version_id_rule_versions",
        "rules",
        "rule_versions",
        ["current_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "moderation_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("active_target_key", sa.String(length=64), nullable=True),
        sa.Column("rule_version_id", sa.Integer(), nullable=False),
        sa.Column("moderator_id", sa.Integer(), nullable=False),
        sa.Column("explanation", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'active'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active', 'reversed')", name="ck_decisions_status"),
        sa.CheckConstraint("target_type IN ('post', 'comment')", name="ck_decisions_target_type"),
        sa.ForeignKeyConstraint(["moderator_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rule_version_id"], ["rule_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("active_target_key", name="uq_decisions_active_target_key"),
    )
    op.create_index("ix_decisions_created", "moderation_decisions", ["created_at", "id"])
    op.create_index("ix_decisions_target", "moderation_decisions", ["target_type", "target_id"])
    op.create_index("ix_moderation_decisions_moderator_id", "moderation_decisions", ["moderator_id"])
    op.create_index("ix_moderation_decisions_rule_version_id", "moderation_decisions", ["rule_version_id"])

    op.create_table(
        "moderation_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("decision_id", sa.Integer(), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("explanation", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("action IN ('reversed')", name="ck_reviews_action"),
        sa.ForeignKeyConstraint(["decision_id"], ["moderation_decisions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_moderation_reviews_decision_id", "moderation_reviews", ["decision_id"])
    op.create_index("ix_moderation_reviews_reviewer_id", "moderation_reviews", ["reviewer_id"])
    op.create_index("ix_reviews_decision_created", "moderation_reviews", ["decision_id", "created_at"])

    op.add_column("comments", sa.Column("moderation_decision_id", sa.Integer(), nullable=True))
    op.alter_column("comments", "post_id", existing_type=sa.Integer(), nullable=True)
    op.create_foreign_key(
        "fk_comments_moderation_decision_id",
        "comments",
        "moderation_decisions",
        ["moderation_decision_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_comments_exactly_one_discussion",
        "comments",
        "(post_id IS NOT NULL AND moderation_decision_id IS NULL) OR "
        "(post_id IS NULL AND moderation_decision_id IS NOT NULL)",
    )
    op.create_index("ix_comments_moderation_decision_id", "comments", ["moderation_decision_id"])
    op.create_index("ix_comments_decision_created", "comments", ["moderation_decision_id", "created_at"])

    op.add_column("reports", sa.Column("decision_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_reports_decision_id", "reports", "moderation_decisions", ["decision_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_reports_decision_id", "reports", ["decision_id"])

    op.add_column("moderation_audit", sa.Column("decision_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_moderation_audit_decision_id",
        "moderation_audit",
        "moderation_decisions",
        ["decision_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_moderation_audit_decision_id", "moderation_audit", ["decision_id"])

    _seed_rules_and_legacy_decisions()


def _seed_rules_and_legacy_decisions() -> None:
    now = datetime.now(timezone.utc)
    rules = sa.table(
        "rules",
        sa.column("id", sa.Integer),
        sa.column("code", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("current_version_id", sa.Integer),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    versions = sa.table(
        "rule_versions",
        sa.column("id", sa.Integer),
        sa.column("rule_id", sa.Integer),
        sa.column("version", sa.Integer),
        sa.column("title", sa.String),
        sa.column("text", sa.Text),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    seed = [
        (1, "R0", False, "Решение до введения структурированных правил", "Историческая запись перенесена из прежней системы модерации."),
        (2, "R1", True, "Реальные угрозы насилия", "Запрещены конкретные и достоверные угрозы причинить физический вред человеку или группе людей."),
        (3, "R2", True, "Чужие персональные данные", "Запрещена публикация персональных данных другого человека без его согласия, включая doxxing."),
        (4, "R3", True, "Спам и автоматизированный флуд", "Запрещены массовые повторные публикации, автоматизированный флуд и намеренное техническое засорение обсуждений."),
        (5, "R4", True, "Незаконный контент", "Запрещён контент, хранение или распространение которого прямо запрещено применимым законом."),
    ]
    op.bulk_insert(
        rules,
        [
            {"id": item[0], "code": item[1], "is_active": item[2], "current_version_id": None, "created_at": now}
            for item in seed
        ],
    )
    op.bulk_insert(
        versions,
        [
            {"id": item[0], "rule_id": item[0], "version": 1, "title": item[3], "text": item[4], "created_at": now}
            for item in seed
        ],
    )
    connection = op.get_bind()
    connection.execute(sa.text("UPDATE rules SET current_version_id = id"))
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                "SELECT setval(pg_get_serial_sequence('rules', 'id'), "
                "(SELECT MAX(id) FROM rules), true)"
            )
        )
        connection.execute(
            sa.text(
                "SELECT setval(pg_get_serial_sequence('rule_versions', 'id'), "
                "(SELECT MAX(id) FROM rule_versions), true)"
            )
        )

    connection.execute(
        sa.text(
            "INSERT INTO moderation_decisions "
            "(target_type, target_id, active_target_key, rule_version_id, moderator_id, explanation, status, created_at) "
            "SELECT 'post', id, 'post:' || CAST(id AS VARCHAR), 1, deleted_by_id, "
            "'Историческое решение перенесено из прежней системы модерации.', 'active', deleted_at "
            "FROM posts WHERE deleted_at IS NOT NULL"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO moderation_decisions "
            "(target_type, target_id, active_target_key, rule_version_id, moderator_id, explanation, status, created_at) "
            "SELECT 'comment', id, 'comment:' || CAST(id AS VARCHAR), 1, deleted_by_id, "
            "'Историческое решение перенесено из прежней системы модерации.', 'active', deleted_at "
            "FROM comments WHERE deleted_at IS NOT NULL"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE moderation_audit SET decision_id = ("
            "SELECT d.id FROM moderation_decisions d "
            "WHERE d.target_type = moderation_audit.target_type AND d.target_id = moderation_audit.target_id "
            "ORDER BY d.id DESC LIMIT 1) WHERE action = 'delete'"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE reports SET decision_id = ("
            "SELECT d.id FROM moderation_decisions d WHERE "
            "(d.target_type = 'post' AND d.target_id = reports.post_id) OR "
            "(d.target_type = 'comment' AND d.target_id = reports.comment_id) "
            "ORDER BY d.id DESC LIMIT 1) WHERE resolution_action = 'delete'"
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    discussion_count = connection.scalar(
        sa.text("SELECT COUNT(*) FROM comments WHERE moderation_decision_id IS NOT NULL")
    )
    if discussion_count:
        raise RuntimeError("Downgrade остановлен: существуют комментарии к решениям модерации")

    op.drop_index("ix_moderation_audit_decision_id", table_name="moderation_audit")
    op.drop_constraint("fk_moderation_audit_decision_id", "moderation_audit", type_="foreignkey")
    op.drop_column("moderation_audit", "decision_id")

    op.drop_index("ix_reports_decision_id", table_name="reports")
    op.drop_constraint("fk_reports_decision_id", "reports", type_="foreignkey")
    op.drop_column("reports", "decision_id")

    op.drop_index("ix_comments_decision_created", table_name="comments")
    op.drop_index("ix_comments_moderation_decision_id", table_name="comments")
    op.drop_constraint("ck_comments_exactly_one_discussion", "comments", type_="check")
    op.drop_constraint("fk_comments_moderation_decision_id", "comments", type_="foreignkey")
    op.drop_column("comments", "moderation_decision_id")
    op.alter_column("comments", "post_id", existing_type=sa.Integer(), nullable=False)

    op.drop_table("moderation_reviews")
    op.drop_table("moderation_decisions")
    op.drop_constraint("fk_rules_current_version_id_rule_versions", "rules", type_="foreignkey")
    op.drop_table("rule_versions")
    op.drop_table("rules")
