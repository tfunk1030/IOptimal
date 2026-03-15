import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / "iOptimal"
CONFIG_FILE = CONFIG_DIR / "config.json"

def load_config():
    if not CONFIG_FILE.exists():
        return {
            "server_url": "http://localhost:8000",
            "auth_token": "",
            "default_car": "bmw",
            "driver_id": ""
        }
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)
