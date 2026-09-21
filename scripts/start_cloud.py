"""Koyeb startup: initialize schema, optional first admin, then run one web worker."""
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from config import settings
    from services.object_storage import verify_storage
    from sqlalchemy import select
    from database import SessionLocal
    from models import User, UserRole
    if settings.STORAGE_BACKEND != "supabase":
        raise RuntimeError("Koyeb requires STORAGE_BACKEND=supabase for durable files")
    verify_storage()
    subprocess.run([sys.executable, "scripts/initialize_database.py"], check=True)
    username = os.environ.get("FIRST_ADMIN_USERNAME", "").strip()
    if username:
        with SessionLocal() as db:
            exists = db.scalar(select(User.id).where(User.role == UserRole.admin))
        if exists is None:
            if not os.environ.get("FIRST_ADMIN_PASSWORD"):
                raise RuntimeError("FIRST_ADMIN_PASSWORD is required for first administrator setup")
            subprocess.run([sys.executable, "scripts/create_admin.py", "--username", username], check=True)
    # Do not pass bootstrap secrets to the long-running web process.
    os.environ.pop("FIRST_ADMIN_PASSWORD", None)
    port = int(os.environ.get("PORT", "8000"))
    os.execvp(sys.executable, [sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0",
                             "--port", str(port), "--no-proxy-headers"])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Connection/key values must not leak through driver exception messages.
        print("Cloud startup failed. Check private bucket, database/session-pooler settings and first-admin configuration.", file=sys.stderr)
        raise SystemExit(1) from None
