from pydantic import BaseModel
from typing import Optional

class UploadResponse(BaseModel):
    session_id: str
    status: str
