"""Add AgileActualORM table for storing released Agile prices

Revision ID: 9210cc6d1781
Revises: 
Create Date: 2026-03-21 15:28:10.664007

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9210cc6d1781'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: Create prices_agile_actual table."""
    op.create_table(
        'prices_agile_actual',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('date_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('region', sa.String(1), nullable=False),
        sa.Column('agile_actual', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('date_time', 'region', name='uq_agile_actual_dt_region')
    )
    op.create_index(
        'ix_prices_agile_actual_date_time',
        'prices_agile_actual',
        ['date_time'],
        unique=False
    )
    op.create_index(
        'ix_prices_agile_actual_region',
        'prices_agile_actual',
        ['region'],
        unique=False
    )


def downgrade() -> None:
    """Downgrade schema: Drop prices_agile_actual table."""
    op.drop_index('ix_prices_agile_actual_region', table_name='prices_agile_actual')
    op.drop_index('ix_prices_agile_actual_date_time', table_name='prices_agile_actual')
    op.drop_table('prices_agile_actual')
