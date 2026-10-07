"""initial_schema

Revision ID: a830fcce5179
Revises: 
Create Date: 2026-09-18 18:19:29.766607

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from backend.models.base import Base
import backend.models  # ensure all models registered in Base.metadata
from backend.alembic.schema_verifier import verify_and_migrate_schema

# revision identifiers, used by Alembic.
revision: str = 'a830fcce5179'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema safely using Schema Verifier & Adoption Engine."""
    bind = op.get_bind()
    verify_and_migrate_schema(op, bind, Base.metadata)


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    for table in reversed(Base.metadata.sorted_tables):
        table.drop(bind, checkfirst=True)
