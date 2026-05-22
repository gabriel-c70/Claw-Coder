"""
FastAPI rate-limit server for Claw-Coder.
Run with: uvicorn api_server:app --port 8001
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")

# service client — bypasses RLS, used server-side only, never expose to CLI
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

app = FastAPI(title="Claw-Coder Rate Limiter")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

# ── limits ────────────────────────────────────────────────────────────────────
TOOL_LIMITS: dict[str, int] = {
    "search_stuff": 10,   # web search: 10/month
}
DEFAULT_LIMIT = 50        # every other tool: 50/month

def month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")

def get_limit(tool_name: str) -> int:
    return TOOL_LIMITS.get(tool_name, DEFAULT_LIMIT)

def verify_token(authorization: str) -> str:
    """Validate Supabase JWT and return user_id."""
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    try:
        response = supabase.auth.get_user(token)
        if not response.user:
            raise HTTPException(status_code=401, detail="Invalid token")
        return response.user.id
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Token error: {exc}") from exc

# ── models ────────────────────────────────────────────────────────────────────
class CheckRequest(BaseModel):
    tool_name: str

class CheckResponse(BaseModel):
    allowed: bool
    tool_name: str
    used: int
    limit: int
    remaining: int
    month: str

# ── endpoints ─────────────────────────────────────────────────────────────────
@app.post("/check", response_model=CheckResponse)
def check_and_increment(body: CheckRequest, authorization: str = Header(...)):
    """
    Check if the user is under their limit for tool_name.
    If yes, increment the counter and return allowed=True.
    If no, return 429.
    """
    user_id = verify_token(authorization)
    tool = body.tool_name
    limit = get_limit(tool)
    mk = month_key()

    # fetch current count
    result = (
        supabase.table("tool_usage")
        .select("count")
        .eq("user_id", user_id)
        .eq("tool_name", tool)
        .eq("month_key", mk)
        .execute()
    )

    if result.data:
        current = result.data[0]["count"]
        if current >= limit:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "rate_limit_exceeded",
                    "tool": tool,
                    "used": current,
                    "limit": limit,
                    "month": mk,
                    "message": f"{tool} allows {limit} calls/month. You've used {current}.",
                },
            )
        # increment
        supabase.table("tool_usage").update({"count": current + 1}).eq(
            "user_id", user_id
        ).eq("tool_name", tool).eq("month_key", mk).execute()
        used = current + 1
    else:
        # first use this month
        supabase.table("tool_usage").insert(
            {"user_id": user_id, "tool_name": tool, "month_key": mk, "count": 1}
        ).execute()
        used = 1

    return CheckResponse(
        allowed=True,
        tool_name=tool,
        used=used,
        limit=limit,
        remaining=limit - used,
        month=mk,
    )


@app.get("/usage")
def get_usage(authorization: str = Header(...)):
    """Return this month's usage for every tool the user has called."""
    user_id = verify_token(authorization)
    mk = month_key()

    result = (
        supabase.table("tool_usage")
        .select("tool_name, count")
        .eq("user_id", user_id)
        .eq("month_key", mk)
        .execute()
    )

    usage = {}
    for row in result.data:
        t = row["tool_name"]
        lim = get_limit(t)
        usage[t] = {
            "used": row["count"],
            "limit": lim,
            "remaining": max(0, lim - row["count"]),
        }
    return {"month": mk, "usage": usage}


@app.get("/health")
def health():
    return {"status": "ok"}