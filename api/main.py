from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routes import upload, results, setups, sessions, team, auth

app = FastAPI(title="iOptimal Platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router, prefix="/api", tags=["upload"])
app.include_router(results.router, prefix="/api", tags=["results"])
app.include_router(setups.router, prefix="/api", tags=["setups"])
app.include_router(sessions.router, prefix="/api", tags=["sessions"])
app.include_router(team.router, prefix="/api", tags=["team"])
app.include_router(auth.router, prefix="/api", tags=["auth"])

@app.get("/health")
def health_check():
    return {"status": "ok"}
