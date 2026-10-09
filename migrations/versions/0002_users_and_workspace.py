"""User accounts, per-user documents, all-documents chat, summaries, OCR stage.

- users table
- documents: owner (user_id), processing stage, summary + suggested questions,
  the embedding model that indexed it; duplicate detection becomes per user
- chat_messages: owner (user_id); document_id is optional (NULL = a chat
  across all of the user's documents)

Existing rows keep user_id = NULL until the first account is created, which
then claims them (see app/api/auth.py).

Revision ID: 0002_users_and_workspace
Revises: 0001_baseline
Create Date: 2026-09-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_users_and_workspace"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("user_id", sa.String(32), nullable=True))
        batch.add_column(sa.Column("stage", sa.String(120), nullable=True))
        batch.add_column(sa.Column("summary", sa.Text(), nullable=True))
        batch.add_column(sa.Column("suggested_questions", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("embedding_model", sa.String(120), nullable=True))
        # The same file may now be uploaded by different users.
        batch.drop_index("ix_documents_file_hash")
        batch.create_index("ix_documents_file_hash", ["file_hash"], unique=False)
        batch.create_index("ix_documents_user_id", ["user_id"])
        batch.create_unique_constraint("uq_documents_user_file", ["user_id", "file_hash"])
        batch.create_foreign_key(
            "fk_documents_user_id", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )

    with op.batch_alter_table("chat_messages") as batch:
        batch.add_column(sa.Column("user_id", sa.String(32), nullable=True))
        batch.alter_column("document_id", existing_type=sa.String(32), nullable=True)
        batch.create_index("ix_chat_messages_user_id", ["user_id"])
        batch.create_foreign_key(
            "fk_chat_messages_user_id", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )


def downgrade():
    with op.batch_alter_table("chat_messages") as batch:
        batch.drop_constraint("fk_chat_messages_user_id", type_="foreignkey")
        batch.drop_index("ix_chat_messages_user_id")
        batch.alter_column("document_id", existing_type=sa.String(32), nullable=False)
        batch.drop_column("user_id")

    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint("fk_documents_user_id", type_="foreignkey")
        batch.drop_constraint("uq_documents_user_file", type_="unique")
        batch.drop_index("ix_documents_user_id")
        batch.drop_index("ix_documents_file_hash")
        batch.create_index("ix_documents_file_hash", ["file_hash"], unique=True)
        for column in ("embedding_model", "suggested_questions", "summary", "stage", "user_id"):
            batch.drop_column(column)

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
