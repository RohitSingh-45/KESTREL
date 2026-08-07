"""add topics.consistency_json

Revision ID: b7c8d9e0f1a2
Revises: 9006db7a5a58
Create Date: 2026-08-08 00:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, Sequence[str], None] = "9006db7a5a58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("topics", schema=None) as batch_op:
        batch_op.add_column(sa.Column("consistency_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("topics", schema=None) as batch_op:
        batch_op.drop_column("consistency_json")
