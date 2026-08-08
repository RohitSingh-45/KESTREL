"""add topics.ai_security_domain

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-08-08 16:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, Sequence[str], None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("topics", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("ai_security_domain", sa.String(length=64), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("topics", schema=None) as batch_op:
        batch_op.drop_column("ai_security_domain")
