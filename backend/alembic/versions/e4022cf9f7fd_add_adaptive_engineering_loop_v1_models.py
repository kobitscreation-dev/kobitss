"""Add Adaptive Engineering Loop V1 models

Revision ID: e4022cf9f7fd
Revises: 1fc32fa52e78
Create Date: 2026-09-19 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4022cf9f7fd'
down_revision: Union[str, Sequence[str], None] = '1fc32fa52e78'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
