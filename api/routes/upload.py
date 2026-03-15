from fastapi import APIRouter, File, UploadFile, Form, BackgroundTasks, Depends, HTTPException
from api.models.schemas import UploadResponse
from api.services.upload_service import save_upload_file_tmp
from api.workers.process_ibt import process_ibt
from api.models.database import get_supabase_client
import uuid

router = APIRouter()

@router.post("/upload-ibt", response_model=UploadResponse)
async def upload_ibt_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    car: str = Form(...),
    wing: float = Form(None),
    driver_id: str = Form(...),
    team_id: str = Form(None)
):
    if not file.filename.endswith(".ibt"):
        raise HTTPException(status_code=400, detail="Invalid file type. Must be an IBT file.")

    # In a real app, you would extract driver_id and team_id from the auth token
    
    db = get_supabase_client()
    
    # 1. Create a session record in Supabase
    session_id = str(uuid.uuid4())
    db.table("sessions").insert({
        "id": session_id,
        "driver_id": driver_id,
        "team_id": team_id,
        "car": car,
        "track": "unknown", # will be updated by processor
        "wing_angle": wing,
        "status": "processing"
    }).execute()
    
    # 2. Save file temporarily
    tmp_path = save_upload_file_tmp(file)
    
    # 3. Enqueue background task
    background_tasks.add_task(
        process_ibt,
        session_id=session_id,
        ibt_path=tmp_path,
        car=car,
        wing=wing,
        driver_id=driver_id,
        team_id=team_id,
        db=db
    )
    
    return UploadResponse(session_id=session_id, status="processing")
