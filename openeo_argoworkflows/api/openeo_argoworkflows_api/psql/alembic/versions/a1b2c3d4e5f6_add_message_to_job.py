"""add message column to job table

Revision ID: a1b2c3d4e5f6
Revises: 28fe2ce196c8
Create Date: 2026-05-21

"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = '28fe2ce196c8'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('jobs', sa.Column('message', sa.VARCHAR(), nullable=True))


def downgrade():
    op.drop_column('jobs', 'message')
