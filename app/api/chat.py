"""Chat API endpoints - conversational interface for video creation."""

import logging
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user
from app.database import get_db
from app.models.db import (
    ChatConversation,
    ChatMessage,
    MessageRole,
    User,
    Project,
    Asset,
    AssetType,
)
from app.models.schemas import (
    ChatConversationOut,
    ChatMessageOut,
    ChatMessageCreate,
    CreateConversationRequest,
)
from app.services.storage import get_storage, make_asset_key
from app.workers.tasks import analyze_and_generate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/conversations", response_model=ChatConversationOut)
async def create_conversation(
    body: CreateConversationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Start a new chat conversation."""
    conversation = ChatConversation(
        user_id=current_user.id,
        title=body.title or "New TikTok Project",
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    
    # Add system message
    system_msg = ChatMessage(
        conversation_id=conversation.id,
        role=MessageRole.system,
        content="Hi! I can help you create TikTok-style videos. Share a TikTok URL you like, upload your videos/images, and I'll match that style.",
    )
    db.add(system_msg)
    await db.commit()
    
    return conversation


@router.get("/conversations", response_model=List[ChatConversationOut])
async def list_conversations(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all conversations for current user."""
    result = await db.execute(
        select(ChatConversation)
        .where(ChatConversation.user_id == current_user.id)
        .order_by(ChatConversation.updated_at.desc())
    )
    return result.scalars().all()


@router.get("/conversations/{conversation_id}", response_model=ChatConversationOut)
async def get_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get conversation with all messages."""
    result = await db.execute(
        select(ChatConversation)
        .where(
            ChatConversation.id == conversation_id,
            ChatConversation.user_id == current_user.id,
        )
        .options(selectinload(ChatConversation.messages))
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(404, "Conversation not found")
    return conversation


@router.post("/conversations/{conversation_id}/messages", response_model=ChatMessageOut)
async def send_message(
    conversation_id: uuid.UUID,
    body: ChatMessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send a message in a conversation.
    
    AI will automatically:
    - Extract TikTok URLs and analyze style
    - Create project if needed
    - Generate edit specs when ready
    """
    # Verify conversation ownership
    conversation = await db.get(ChatConversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(404, "Conversation not found")
    
    # Save user message
    user_msg = ChatMessage(
        conversation_id=conversation_id,
        role=MessageRole.user,
        content=body.content,
        attachments=body.attachments,
    )
    db.add(user_msg)
    await db.flush()
    
    # Process message and generate AI response
    from app.services.chat_processor import process_chat_message
    ai_response, metadata = await process_chat_message(
        conversation=conversation,
        user_message=body.content,
        attachments=body.attachments or {},
        db=db,
        user=current_user,
    )
    
    # Save AI response
    ai_msg = ChatMessage(
        conversation_id=conversation_id,
        role=MessageRole.assistant,
        content=ai_response,
        response_metadata=metadata,
    )
    db.add(ai_msg)
    
    # Update conversation timestamp
    from datetime import datetime, timezone
    conversation.updated_at = datetime.now(timezone.utc)
    
    await db.commit()
    await db.refresh(ai_msg)
    
    return ai_msg


@router.post("/conversations/{conversation_id}/upload", response_model=ChatMessageOut)
async def upload_files_to_chat(
    conversation_id: uuid.UUID,
    files: List[UploadFile] = File(...),
    message: str = Form(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload videos/images to a chat conversation."""
    # Verify conversation
    conversation = await db.get(ChatConversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(404, "Conversation not found")
    
    # Ensure project exists
    if not conversation.project_id:
        # Create project
        from app.models.db import Workspace
        result = await db.execute(
            select(Workspace).where(Workspace.owner_id == current_user.id).limit(1)
        )
        workspace = result.scalar_one_or_none()
        if not workspace:
            workspace = Workspace(owner_id=current_user.id, name="My Workspace")
            db.add(workspace)
            await db.flush()
        
        project = Project(
            workspace_id=workspace.id,
            title=f"Chat Project - {conversation.title}",
            target_platform="tiktok",
        )
        db.add(project)
        await db.flush()
        conversation.project_id = project.id
    
    # Upload files
    storage = get_storage()
    uploaded_files = []
    
    for file in files:
        # Determine asset type
        content_type = file.content_type or ""
        if content_type.startswith("video/"):
            asset_type = AssetType.raw_video
        elif content_type.startswith("image/"):
            asset_type = AssetType.image
        else:
            continue  # Skip unsupported files
        
        # Save to storage
        key = make_asset_key(str(conversation.project_id), asset_type.value, file.filename)
        storage_url = storage.save(file.file, key, content_type)
        
        # Create asset record
        asset = Asset(
            project_id=conversation.project_id,
            type=asset_type,
            filename=file.filename,
            storage_url=key,  # Store key, not full path
            mime_type=content_type,
        )
        db.add(asset)
        uploaded_files.append({"filename": file.filename, "type": asset_type.value})
    
    await db.commit()
    
    # Create AI response
    file_list = ", ".join([f["filename"] for f in uploaded_files])
    ai_content = f"✅ Uploaded {len(uploaded_files)} file(s): {file_list}. "
    
    if message:
        # User included a message with upload
        user_msg = ChatMessage(
            conversation_id=conversation_id,
            role=MessageRole.user,
            content=message,
            attachments={"uploaded_files": uploaded_files},
        )
        db.add(user_msg)
        
        ai_content += "Ready to analyze the style when you share a reference TikTok URL!"
    else:
        ai_content += "Upload more files or share a TikTok URL to match its style."
    
    ai_msg = ChatMessage(
        conversation_id=conversation_id,
        role=MessageRole.assistant,
        content=ai_content,
        response_metadata={"uploaded_files": uploaded_files},
    )
    db.add(ai_msg)
    await db.commit()
    await db.refresh(ai_msg)
    
    return ai_msg


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a conversation."""
    conversation = await db.get(ChatConversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(404, "Conversation not found")
    
    await db.delete(conversation)
    await db.commit()
    return {"status": "deleted"}
