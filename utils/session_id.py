from __future__ import annotations

from datetime import datetime
from uuid import uuid4


def generate_session_id(participant_id: str) -> str:
    stamp = datetime.now().astimezone().strftime("%Y%m%d")
    short_uuid = uuid4().hex[:6]
    return f"session_{stamp}_{participant_id}_{short_uuid}"
