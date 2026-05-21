"""Create api_keys table for API key authentication.

Revision ID: add_api_keys
Revises: 
Create Date: 2026-04-15 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'add_api_keys'
down_revision: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # Add password_hash to users table
    op.add_column('users', sa.Column('password_hash', sa.String(255), nullable=True))
    op.add_column('users', sa.Column('is_active', sa.Boolean(), default=True, nullable=False, server_default='true'))
    op.add_column('users', sa.Column('email_verified', sa.Boolean(), default=False, nullable=False, server_default='false'))
    
    # Create API keys table
    op.create_table(
        'api_keys',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('key_hash', sa.String(255), nullable=False, index=True, unique=True),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), default=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    
    # Add indexes for performance
    op.create_index('idx_api_keys_user_id', 'api_keys', ['user_id'])
    op.create_index('idx_api_keys_key_hash', 'api_keys', ['key_hash'])
    
    # Add indexes to existing tables
    op.create_index('idx_projects_workspace_id', 'projects', ['workspace_id'])
    op.create_index('idx_projects_status', 'projects', ['status'])
    op.create_index('idx_assets_project_id', 'assets', ['project_id'])
    op.create_index('idx_assets_type', 'assets', ['type'])
    op.create_index('idx_renders_project_id', 'renders', ['project_id'])
    op.create_index('idx_renders_status', 'renders', ['status'])
    op.create_index('idx_jobs_project_id', 'jobs', ['project_id'])
    op.create_index('idx_jobs_status', 'jobs', ['status'])


def downgrade() -> None:
    # Remove indexes
    op.drop_index('idx_jobs_status')
    op.drop_index('idx_jobs_project_id')
    op.drop_index('idx_renders_status')
    op.drop_index('idx_renders_project_id')
    op.drop_index('idx_assets_type')
    op.drop_index('idx_assets_project_id')
    op.drop_index('idx_projects_status')
    op.drop_index('idx_projects_workspace_id')
    
    op.drop_index('idx_api_keys_key_hash')
    op.drop_index('idx_api_keys_user_id')
    op.drop_table('api_keys')
    
    op.drop_column('users', 'email_verified')
    op.drop_column('users', 'is_active')
    op.drop_column('users', 'password_hash')
