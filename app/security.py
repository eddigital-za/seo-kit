import os
from fastapi import Header, HTTPException

def require_agent_key(x_agent_key: str | None = Header(default=None)):
    expected = os.getenv("AGENT_API_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="Protected endpoints disabled: AGENT_API_KEY is not configured")
    if x_agent_key != expected:
        raise HTTPException(status_code=401, detail="Invalid agent key")
