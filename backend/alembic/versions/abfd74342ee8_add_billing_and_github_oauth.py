"""Add billing and github oauth

Revision ID: abfd74342ee8
Revises: e4022cf9f7fd
Create Date: 2026-09-22 03:35:08.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'abfd74342ee8'
down_revision: Union[str, Sequence[str], None] = 'e4022cf9f7fd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    org_columns = [c['name'] for c in inspector.get_columns('organizations')]
    tables = inspector.get_table_names()

    # Use batch_alter_table for SQLite compatibility
    with op.batch_alter_table('organizations') as batch_op:
        if 'credit_balance' not in org_columns:
            batch_op.add_column(sa.Column('credit_balance', sa.Integer(), nullable=False, server_default='0'))
        if 'stripe_customer_id' not in org_columns:
            batch_op.add_column(sa.Column('stripe_customer_id', sa.String(), nullable=True))
            batch_op.create_index('ix_organizations_stripe_customer_id', ['stripe_customer_id'], unique=False)
        
    if 'ledger_transactions' not in tables:
        op.create_table('ledger_transactions',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('organization_id', sa.String(), nullable=False),
        sa.Column('type', sa.Enum('TOPUP', 'MISSION_RESERVATION', 'MISSION_SETTLEMENT', 'MISSION_RELEASE', 'ADJUSTMENT', name='ledgertransactiontype'), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(), nullable=False),
        sa.Column('reference_id', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('metadata_json', sa.String(), nullable=True),
        sa.Column('idempotency_key', sa.String(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    if 'ix_ledger_transactions_organization_id' not in [idx['name'] for idx in inspector.get_indexes('ledger_transactions')]:
        op.create_index('ix_ledger_transactions_organization_id', 'ledger_transactions', ['organization_id'], unique=False)
    if 'ix_ledger_transactions_reference_id' not in [idx['name'] for idx in inspector.get_indexes('ledger_transactions')]:
        op.create_index('ix_ledger_transactions_reference_id', 'ledger_transactions', ['reference_id'], unique=False)
    if 'ix_ledger_transactions_idempotency_key' not in [idx['name'] for idx in inspector.get_indexes('ledger_transactions')]:
        op.create_index('ix_ledger_transactions_idempotency_key', 'ledger_transactions', ['idempotency_key'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_ledger_transactions_idempotency_key', table_name='ledger_transactions')
    op.drop_index('ix_ledger_transactions_reference_id', table_name='ledger_transactions')
    op.drop_index('ix_ledger_transactions_organization_id', table_name='ledger_transactions')
    op.drop_table('ledger_transactions')
    
    with op.batch_alter_table('organizations') as batch_op:
        batch_op.drop_index('ix_organizations_stripe_customer_id')
        batch_op.drop_column('stripe_customer_id')
        batch_op.drop_column('credit_balance')
