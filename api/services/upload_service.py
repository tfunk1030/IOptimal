import os
import tempfile
import uuid
from fastapi import UploadFile

def save_upload_file_tmp(upload_file: UploadFile) -> str:
    """Save an uploaded file to a temporary directory and return the path."""
    _, ext = os.path.splitext(upload_file.filename)
    tmp_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}{ext}")
    
    with open(tmp_path, "wb") as f:
        f.write(upload_file.file.read())
        
    return tmp_path
