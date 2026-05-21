"""Add chat tables

Revision ID: 001_chat_tables
Revises: 
Create Date: 2026-05-08

This migration adds the chat conversation and chat message tables
to support the conversational interface for video creation.

To apply this migration:
    alembic upgrade head

Or manually run the SQL below.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001_chat_tables'
down_revision = None  # Replace with your current head revision
branch_labels = None
depends_on = None


def upgrade():
    # Create chat_conversations table
    op.create_table(
        'chat_conversations',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('project_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('projects.id', ondelete='SET NULL'), nullable=True),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    
    # Create indexes for chat_conversations
    op.create_index(op.f('ix_chat_conversations_user_id'), 'chat_conversations', ['user_id'])
    op.create_index(op.f('ix_chat_conversations_project_id'), 'chat_conversations', ['project_id'])
    op.create_index(op.f('ix_chat_conversations_created_at'), 'chat_conversations', ['created_at'])
    
    # Create message_role enum
    op.execute("CREATE TYPE message_role AS ENUM ('user', 'assistant', 'system')")
    
    # Create chat_messages table
    op.create_table(
        'chat_messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('chat_conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.Enum('user', 'assistant', 'system', name='message_role'), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('attachments', postgresql.JSONB(), nullable=True),
        sa.Column('response_metadata', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    
    # Create indexes for chat_messages
    op.create_index(op.f('ix_chat_messages_conversation_id'), 'chat_messages', ['conversation_id'])
    op.create_index(op.f('ix_chat_messages_created_at'), 'chat_messages', ['created_at'])


def downgrade():
    # Drop indexes
    op.drop_index(op.f('ix_chat_messages_created_at'), table_name='chat_messages')
    op.drop_index(op.f('ix_chat_messages_conversation_id'), table_name='chat_messages')
    
    # Drop chat_messages table
    op.drop_table('chat_messages')
    
    # Drop message_role enum
    op.execute("DROP TYPE message_role")
    
    # Drop indexes
    op.drop_index(op.f('ix_chat_conversations_created_at'), table_name='chat_conversations')
    op.drop_index(op.f('ix_chat_conversations_project_id'), table_name='chat_conversations')
    op.drop_index(op.f('ix_chat_conversations_user_id'), table_name='chat_conversations')
    
    # Drop chat_conversations table
    op.drop_table('chat_conversations')
