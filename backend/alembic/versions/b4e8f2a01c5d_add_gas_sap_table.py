"""Add prices_gas_sap table for National Gas System Average Price

Revision ID: b4e8f2a01c5d
Revises: 9210cc6d1781
Create Date: 2026-03-27 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4e8f2a01c5d'
down_revision: Union[str, None] = '9210cc6d1781'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: Create prices_gas_sap table."""
    op.create_table(
        'prices_gas_sap',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('gas_sap', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('date', name='uq_gas_sap_date'),
    )
    op.create_index(
        'ix_prices_gas_sap_date',
        'prices_gas_sap',
        ['date'],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema: Drop prices_gas_sap table."""
    op.drop_index('ix_prices_gas_sap_date', table_name='prices_gas_sap')
    op.drop_table('prices_gas_sap')
