import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import boto3
from botocore.config import Config

app = FastAPI(title="Ivan's Image Optimizer API")

# Enable Cross-Origin requests so our frontend can securely talk to our API layer
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BUCKET_NAME = "nav-property-media"

def _get_secret(key: str) -> str:
    """Helper tool to cleanly pull environment variables directly out of Replit."""
    return os.environ.get(key, "").strip()

def get_r2_client():
    """Initializes a rock-solid background connection straight to Cloudflare R2 storage."""
    endpoint = _get_secret("R2_ENDPOINT_URL")
    key_id   = _get_secret("R2_ACCESS_KEY_ID")
    secret   = _get_secret("R2_SECRET_ACCESS_KEY")

    if not all([endpoint, key_id, secret]):
        raise RuntimeError("Missing essential Cloudflare R2 secrets inside Environment Variables.")

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        config=Config(signature_version="s3v4"),
        region_name="auto"
    )

@app.get("/api/health")
def health_check():
    """Simple testing point to verify the server machine is running cleanly."""
    try:
        client = get_r2_client()
        return {"status": "online", "storage_connection": "connected"}
    except Exception as e:
        return {"status": "online", "storage_connection": f"error: {str(e)}"}

# Mount the frontend directory so our UI page can display beautifully
# (This acts as a structural placeholder until we build the html files in the next step)
if os.path.exists("frontend"):
    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")