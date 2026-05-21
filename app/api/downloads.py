"""Secure download endpoints with signed URLs."""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.config import get_settings
from app.services.storage import get_storage

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


@router.get("/download/{token}")
async def download_file(token: str):
    """Download a file using a signed token.
    
    Tokens expire after 1 hour for security.
    """
    signing_key = settings.url_signing_key or settings.secret_key
    serializer = URLSafeTimedSerializer(signing_key)
    
    try:
        # Verify token and extract key (expires after 1 hour)
        data = serializer.loads(token, max_age=3600)
        key = data.get("key")
        if not key:
            raise HTTPException(400, "Invalid token format")
        
        # Get file path from storage
        storage = get_storage()
        file_path = storage.get_local_path(key)
        
        if not Path(file_path).exists():
            raise HTTPException(404, "File not found")
        
        # Return file with proper headers
        filename = Path(key).name
        return FileResponse(
            file_path,
            media_type="application/octet-stream",
            filename=filename,
        )
        
    except SignatureExpired:
        raise HTTPException(403, "Download link expired")
    except BadSignature:
        raise HTTPException(403, "Invalid download link")
    except Exception as exc:
        logger.exception("Download failed for token: %s", token[:20])
        raise HTTPException(500, "Download failed")
