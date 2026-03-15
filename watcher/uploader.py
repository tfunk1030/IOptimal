import httpx
from pathlib import Path

class IBTUploader:
    def __init__(self, server_url: str, auth_token: str, driver_id: str):
        self.server_url = server_url.rstrip("/")
        self.auth_token = auth_token
        self.driver_id = driver_id
        self.client = httpx.Client(timeout=120)  # IBTs can be large

    def upload(self, ibt_path: str, car: str, wing: float = None):
        path = Path(ibt_path)
        with open(path, "rb") as f:
            response = self.client.post(
                f"{self.server_url}/api/upload-ibt",
                files={"file": (path.name, f, "application/octet-stream")},
                data={"car": car, "wing": str(wing) if wing else "", "driver_id": self.driver_id},
                headers={"Authorization": f"Bearer {self.auth_token}"},
            )
        response.raise_for_status()
        result = response.json()
        print(f"[iOptimal] Uploaded {path.name} → session {result['session_id']}")
        return result
