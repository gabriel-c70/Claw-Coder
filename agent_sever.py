"""
FastAPI rate-limit + search proxy server for Claw-Coder.
Run with: uvicorn api_server:app --port 8001
Deploy to: Render / Railway / Fly.io
"""
from __future__ import annotations

import json
import os
import hmac
import hashlib
import base64
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from supabase import create_client, Client
import urllib.request
import urllib.error
from dodopayments import DodoPayments

load_dotenv()

SUPABASE_URL      = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
TAVILY_API_KEY    = os.getenv("TAVILY_API_KEY", "")
DODO_PAYMENTS_API_KEY = os.getenv("DODO_PAYMENTS_API_KEY", "")
DODO_PAYMENTS_WEBHOOK_KEY = os.getenv("DODO_PAYMENTS_WEBHOOK_KEY", "")
DODO_PAYMENTS_BASE_URL = os.getenv("DODO_PAYMENTS_BASE_URL", "https://test.dodopayments.com")
DODO_MONTHLY_PRODUCT_ID = os.getenv("DODO_MONTHLY_PRODUCT_ID", os.getenv("DODO_PRODUCT_ID", ""))
DODO_MONTHLY_CREDITS = int(os.getenv("DODO_MONTHLY_CREDITS", os.getenv("DODO_CREDITS_PER_PURCHASE", "1000")))
DODO_TOPUP_PRODUCT_ID = os.getenv("DODO_TOPUP_PRODUCT_ID", "")
DODO_TOPUP_CREDITS = int(os.getenv("DODO_TOPUP_CREDITS", "500"))
DODO_RETURN_URL = os.getenv("DODO_RETURN_URL", "https://claw-coder-3.onrender.com")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in environment")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

app = FastAPI(title="Claw-Coder API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

# ── Free tier limits (per month) ─────────────────────────────────────────────
# Tools that cost YOU money or compute — limit these
# Tools that run purely locally — don't bother limiting them
TOOL_LIMITS: dict[str, int] = {
    # cloud tools — cost you money
    "search_stuff":           10,   # Tavily API costs per search

    # heavy compute — Docker containers, embeddings
    "execute_code_in_docker": 10,   # Docker sandbox runs
    "ingest_paths_knowledge": 12,   # heavy: tree-sitter + embeddings on whole dirs
    "ingest_code_knowledge":  5,   # tree-sitter + embeddings
    "ingest_pdf_knowledge":   10,   # PDF parsing + embeddings

    # RAG searches — embedding API calls
    "search_knowledge_base":  20,   # ChromaDB + embedding query
    "search_knowledge_graph": 20,   # graph traversal

    # run tests in docker
    "run_tests":              5,   # Docker container per run
}

# Pro tier — soft limit per tool (can be exceeded with credits)
PRO_LIMIT = 999_999
PRO_SOFT_LIMIT = 400

# Tools NOT in TOOL_LIMITS run unlimited for everyone (they're purely local)
# read_files, list_files, edit_file, create_file, delete_file,
# run_terminal, manage_memory, manage_plan, git_diff, git_status,
# apply_patch, git_apply_patch, gnu_patch, extract_functions,
# open_default_browser, search_code, ask_user

def month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def get_limit(tool_name: str, plan: str = "free") -> int:
    if plan == "pro":
        return PRO_SOFT_LIMIT  # Soft limit for PRO plans
    return TOOL_LIMITS.get(tool_name, PRO_LIMIT)  # not in limits = unlimited
TOOL_CREDIT_COSTS: dict[str, int] = {
    "search_knowledge_base":  20,
    "search_knowledge_graph": 20,
    "ingest_code_knowledge":  25,
    "ingest_pdf_knowledge":   15,
    "execute_code_in_docker": 30,
    "search_stuff":           40,
    "run_tests":              30,
    "ingest_paths_knowledge": 40,
}
WORKSPACE_CONNECT_COST = 15
DODO_MONTHLY_TOOL_CREDITS = int(os.getenv("DODO_MONTHLY_CREDITS", "1000"))
DODO_MONTHLY_WORKSPACE_CREDITS = int(os.getenv("DODO_MONTHLY_WORKSPACE_CREDITS", "100"))


def verify_token(authorization: str) -> str:
    """Accept both Supabase JWT and GitHub access tokens."""
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")

    # try Supabase JWT first
    try:
        response = supabase.auth.get_user(token)
        if response.user:
            return response.user.id
    except Exception:
        pass

    # fall back — verify with GitHub API directly
    try:
        import urllib.request as _req
        import ssl

        # fix Mac SSL certificate issue
        ssl_context = ssl.create_default_context()
        try:
            import certifi
            ssl_context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            pass

        req = _req.Request(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
        with _req.urlopen(req, timeout=10, context=ssl_context) as resp:
            user = json.loads(resp.read().decode("utf-8"))
            github_id = user.get("id")
            if not github_id:
                raise HTTPException(status_code=401, detail="Invalid token")
            # find matching Supabase user by github_id in user_metadata
            users_resp = supabase.auth.admin.list_users()
            for u in users_resp:
                meta = u.user_metadata or {}
                if str(meta.get("github_id")) == str(github_id):
                    return u.id
            # no Supabase user matched — use github_{id} as fallback user key
            return f"github_{github_id}"
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Token verification failed: {exc}")

    raise HTTPException(status_code=401, detail="Invalid token")

def get_user_plan(user_id: str) -> str:
    """Check if user has an active pro subscription."""
    try:
        result = (
            supabase.table("subscriptions")
            .select("plan, valid_until")
            .eq("user_id", user_id)
            .execute()
        )
        if result.data:
            sub = result.data[0]
            valid_until = sub.get("valid_until")
            if valid_until:
                expiry = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
                if expiry > datetime.now(timezone.utc):
                    return sub.get("plan", "free")
    except Exception:
        pass
    return "free"


def get_credit_balance(user_id: str, bucket: str = "tools") -> int:
    result = (
        supabase.table("credit_balances")
        .select("balance")
        .eq("user_id", user_id)
        .eq("bucket", bucket)
        .execute()
    )
    if not result.data:
        return 0
    return int(result.data[0].get("balance") or 0)


def consume_credit(user_id: str, tool_name: str, amount: int, bucket: str = "tools") -> bool:
    """Atomically consume one paid credit. Requires supabase/schema.sql."""
    try:
        result = supabase.rpc(
            "consume_user_credit",
            {"p_user_id": user_id, "p_tool_name": tool_name, "p_amount": amount, "p_bucket": bucket},
        ).execute()
        return bool(result.data)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Credit accounting is not installed. Run the Supabase SQL in "
                "supabase/schema.sql, then retry."
            ),
        ) from exc


def grant_credits(user_id: str, amount: int, reason: str, reference_id: str, metadata: dict, bucket: str = "tools") -> None:
    try:
        supabase.rpc(
            "grant_user_credits",
            {
                "p_user_id": user_id,
                "p_amount": amount,
                "p_reason": reason,
                "p_reference_id": reference_id,
                "p_metadata": metadata,
                "p_bucket": bucket
            },
        ).execute()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Could not grant credits. Check the Supabase credit RPC functions.",
        ) from exc


def upsert_subscription(user_id: str, subscription: dict, plan: str = "pro") -> None:
    subscription_id = subscription.get("subscription_id")
    status = subscription.get("status") or "active"
    next_billing_date = subscription.get("next_billing_date")
    valid_until = next_billing_date or (datetime.now(timezone.utc) + timedelta(days=31)).isoformat()

    supabase.table("subscriptions").upsert({
        "user_id": user_id,
        "plan": plan,
        "status": status,
        "dodo_subscription_id": subscription_id,
        "valid_until": valid_until,
        "raw_event": subscription,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }, on_conflict="user_id").execute()


def user_id_from_metadata(data: dict) -> str | None:
    metadata = data.get("metadata") or {}
    customer = data.get("customer") or {}
    customer_metadata = customer.get("metadata") or {}
    return (
        metadata.get("supabase_user_id")
        or metadata.get("user_id")
        or customer_metadata.get("supabase_user_id")
        or customer_metadata.get("user_id")
    )


def check_and_increment(user_id: str, tool_name: str, plan: str) -> dict:
    """Check limit and increment counter. Returns usage info."""
    # tools not in TOOL_LIMITS are free/unlimited for free plans
    # but PRO plans have soft limits for all tools
    if tool_name not in TOOL_LIMITS and plan != "pro":
        return {"allowed": True, "used": 0, "limit": PRO_LIMIT, "remaining": PRO_LIMIT}

    limit = get_limit(tool_name, plan)
    mk = month_key()

    result = (
        supabase.table("tool_usage")
        .select("count")
        .eq("user_id", user_id)
        .eq("tool_name", tool_name)
        .eq("month_key", mk)
        .execute()
    )

    current = result.data[0]["count"] if result.data else 0

    if current >= limit:
        cost = TOOL_CREDIT_COSTS.get(tool_name, 10)
        if consume_credit(user_id, tool_name, amount=cost, bucket="tools"):
            credits = get_credit_balance(user_id, "tools")
            return {
                "allowed": True,
                "tool_name": tool_name,
                "used": current,
                "limit": limit,
                "remaining": 0,
                "credits": credits,
                "credits_spent": cost,
                "month": mk,
                "plan": plan,
                "source": "credits",
            }
        raise HTTPException(
            status_code=402,
            detail={
                "error": "credits_required",
                "tool": tool_name,
                "used": current,
                "limit": limit,
                "credits": get_credit_balance(user_id, "tools"),
                "cost": cost,
                "month": mk,
                "message": (
                    f"{tool_name} allows {limit} calls/month on {plan.upper()} plan. You've used {current}. "
                    "Run `claw topup` to buy extra pay-as-you-go credits."
                ),
            },
        )

    # increment
    if result.data:
        supabase.table("tool_usage").update({"count": current + 1}).eq(
            "user_id", user_id).eq("tool_name", tool_name).eq("month_key", mk).execute()
    else:
        supabase.table("tool_usage").insert(
            {"user_id": user_id, "tool_name": tool_name, "month_key": mk, "count": 1}
        ).execute()

    used = current + 1
    return {
        "allowed": True,
        "tool_name": tool_name,
        "used": used,
        "limit": limit,
        "remaining": limit - used,
        "credits": get_credit_balance(user_id, "tools"),
        "month": mk,
        "plan": plan,
        "source": "monthly",
    }


def verify_dodo_signature(raw_body: bytes, webhook_id: str, timestamp: str, signature: str) -> None:
    if not DODO_PAYMENTS_WEBHOOK_KEY:
        raise HTTPException(status_code=500, detail="DODO_PAYMENTS_WEBHOOK_KEY is not configured")
    if not webhook_id or not timestamp or not signature:
        raise HTTPException(status_code=400, detail="Missing Dodo webhook signature headers")

    secret = DODO_PAYMENTS_WEBHOOK_KEY
    if secret.startswith("whsec_"):
        secret_bytes = base64.b64decode(secret.split("_", 1)[1])
    else:
        secret_bytes = secret.encode()

    signed_payload = f"{webhook_id}.{timestamp}.".encode() + raw_body
    digest = hmac.new(secret_bytes, signed_payload, hashlib.sha256).digest()
    expected_signature = base64.b64encode(digest).decode()

    # header format: "v1,<sig>" possibly multiple, space-separated
    provided_signatures = [
        part.split(",", 1)[1] for part in signature.split(" ") if "," in part
    ]
    if not any(hmac.compare_digest(expected_signature, sig) for sig in provided_signatures):
        raise HTTPException(status_code=400, detail="Invalid Dodo webhook signature")


def dodo_request(path: str, payload: dict) -> dict:
    if not DODO_PAYMENTS_API_KEY:
        raise HTTPException(status_code=500, detail="DODO_PAYMENTS_API_KEY is not configured")
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{DODO_PAYMENTS_BASE_URL.rstrip('/')}{path}",
        data=data,
        headers={
            "Authorization": f"Bearer {DODO_PAYMENTS_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "claw-coder-server/1.0 (+https://claw-coder-3.onrender.com)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Dodo API error {exc.code}: {body}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Dodo API unreachable: {exc}") from exc


# ── Models ────────────────────────────────────────────────────────────────────

class CheckRequest(BaseModel):
    tool_name: str

class SearchRequest(BaseModel):
    query: str

class CheckoutRequest(BaseModel):
    credits: int | None = None
    mode: str = "subscription"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/check")
def check(body: CheckRequest, authorization: str = Header(...)):
    """
    Check + increment rate limit for any tool.
    Returns 429 if over limit.
    """
    user_id = verify_token(authorization)
    plan    = get_user_plan(user_id)
    return check_and_increment(user_id, body.tool_name, plan)


@app.post("/search")
def search(body: SearchRequest, authorization: str = Header(...)):
    """
    Proxy web search through server.
    - Rate limit enforced server-side (can't be bypassed)
    - Tavily API key never sent to client
    """
    user_id = verify_token(authorization)
    plan    = get_user_plan(user_id)

    # enforce rate limit
    check_and_increment(user_id, "search_stuff", plan)

    if not TAVILY_API_KEY:
        raise HTTPException(status_code=500, detail="Search not configured on server")

    query = body.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Missing query")

    try:
        from tavily import TavilyClient
        tavily = TavilyClient(api_key=TAVILY_API_KEY)
        response = tavily.search(query=query, max_results=5, search_depth="advanced")
        return {
            "status": "ok",
            "query": query,
            "results": [
                {
                    "title": r["title"],
                    "url": r["url"],
                    "content": r["content"][:1000],
                }
                for r in response.get("results", [])
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Search failed: {exc}") from exc

@app.post("/workspace/connect")
def workspace_connect(authorization: str = Header(...)):
    user_id = verify_token(authorization)
    plan = get_user_plan(user_id)

    if plan != "pro":
        raise HTTPException(
            status_code=402,
            detail={
                "error": "workspace_paid_only",
                "message": "Workspace mode is a paid feature. Run `claw buy` to subscribe.",
            },
        )

    if not consume_credit(user_id, "workspace_connect", amount=WORKSPACE_CONNECT_COST, bucket="workspace"):
        raise HTTPException(
            status_code=402,
            detail={
                "error": "workspace_credits_required",
                "credits": get_credit_balance(user_id, "workspace"),
                "cost": WORKSPACE_CONNECT_COST,
                "message": (
                    f"Workspace connections cost {WORKSPACE_CONNECT_COST} workspace credits "
                    f"(you have {get_credit_balance(user_id, 'workspace')} workspace credits). "
                    "Run `claw buy` to subscribe for workspace credits, or wait for next month's allotment."
                ),
            },
        )
    return {"allowed": True, "credits": get_credit_balance(user_id, "workspace")}

@app.get("/usage")
def get_usage(authorization: str = Header(...)):
    """Return this month's usage for the logged-in user."""
    user_id = verify_token(authorization)
    plan    = get_user_plan(user_id)
    mk      = month_key()

    result = (
        supabase.table("tool_usage")
        .select("tool_name, count")
        .eq("user_id", user_id)
        .eq("month_key", mk)
        .execute()
    )

    usage = {}
    for row in result.data:
        t   = row["tool_name"]
        lim = get_limit(t, plan)
        usage[t] = {
            "used":      row["count"],
            "limit":     lim,
            "remaining": max(0, lim - row["count"]),
        }

    # For PRO plans, show soft limits for all tools that have been used
    # For free plans, only show tools in TOOL_LIMITS with their specific limits
    if plan == "pro":
        # For PRO plans, apply soft limits to all tools that have been used
        for tool in list(usage.keys()):
            used = usage[tool]["used"]
            usage[tool] = {
                "used":      used,
                "limit":     PRO_SOFT_LIMIT,
                "remaining": max(0, PRO_SOFT_LIMIT - used),
            }
        # Also add soft limit entries for all limited tools even if unused
        for tool in TOOL_LIMITS.keys():
            if tool not in usage:
                usage[tool] = {
                    "used":      0,
                    "limit":     PRO_SOFT_LIMIT,
                    "remaining": PRO_SOFT_LIMIT,
                }
    else:
        # show all limited tools even if unused for free plans
        for tool, lim in TOOL_LIMITS.items():
            if tool not in usage:
                usage[tool] = {
                    "used":      0,
                    "limit":     get_limit(tool, plan),
                    "remaining": get_limit(tool, plan),
                }

    # Get credit ledger information for this month
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    ledger_result = (
        supabase.table("credit_ledger")
        .select("amount")
        .eq("user_id", user_id)
        .gte("created_at", month_start.isoformat())
        .execute()
    )
    
    credits_spent_month = 0
    credits_granted_month = 0
    for row in ledger_result.data:
        amount = row.get("amount", 0)
        if amount < 0:
            credits_spent_month += abs(amount)
        else:
            credits_granted_month += amount

    # Get balances from both buckets
    tools_balance = get_credit_balance(user_id, "tools")
    workspace_balance = get_credit_balance(user_id, "workspace")
    current_balance = tools_balance + workspace_balance

    return {
        "month": mk, 
        "plan": plan, 
        "credits": current_balance,
        "tools_credits": tools_balance,
        "workspace_credits": workspace_balance,
        "credits_spent_month": credits_spent_month,
        "credits_granted_month": credits_granted_month,
        "usage": usage
    }


@app.get("/plan")
def get_plan(authorization: str = Header(...)):
    """Return the user's current plan."""
    user_id = verify_token(authorization)
    plan    = get_user_plan(user_id)
    
    # Get credit ledger information for this month
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    ledger_result = (
        supabase.table("credit_ledger")
        .select("amount")
        .eq("user_id", user_id)
        .gte("created_at", month_start.isoformat())
        .execute()
    )
    
    credits_spent_month = 0
    credits_granted_month = 0
    for row in ledger_result.data:
        amount = row.get("amount", 0)
        if amount < 0:
            credits_spent_month += abs(amount)
        else:
            credits_granted_month += amount

    # Get balances from both buckets
    tools_balance = get_credit_balance(user_id, "tools")
    workspace_balance = get_credit_balance(user_id, "workspace")
    current_balance = tools_balance + workspace_balance
    
    total_credits = credits_granted_month + current_balance
    usage_percentage = round((credits_spent_month / total_credits) * 100, 1) if total_credits > 0 else 0

    return {
        "plan": plan, 
        "user_id": user_id, 
        "credits": current_balance,
        "tools_credits": tools_balance,
        "workspace_credits": workspace_balance,
        "credits_spent_month": credits_spent_month,
        "credits_granted_month": credits_granted_month,
        "usage_percentage": usage_percentage
    }


@app.post("/checkout")
def create_checkout(body: CheckoutRequest, authorization: str = Header(...)):
    """Create a Dodo checkout session for subscription or pay-as-you-go credits."""
    user_id = verify_token(authorization)
    mode = body.mode.strip().lower()
    if mode not in {"subscription", "topup"}:
        raise HTTPException(status_code=400, detail="mode must be subscription or topup")

    if mode == "subscription":
        if not DODO_MONTHLY_PRODUCT_ID:
            raise HTTPException(status_code=500, detail="DODO_MONTHLY_PRODUCT_ID is not configured")
        product_id = DODO_MONTHLY_PRODUCT_ID
        credits = body.credits or DODO_MONTHLY_CREDITS
        metadata_product = "claw-coder-monthly"
        billing = "monthly"
    else:
        if not DODO_TOPUP_PRODUCT_ID:
            raise HTTPException(status_code=500, detail="DODO_TOPUP_PRODUCT_ID is not configured")
        product_id = DODO_TOPUP_PRODUCT_ID
        credits = body.credits or DODO_TOPUP_CREDITS
        metadata_product = "claw-coder-topup"
        billing = "topup"

    payload = {
        "product_cart": [{"product_id": product_id, "quantity": 1}],
        "metadata": {
            "supabase_user_id": user_id,
            "credits": str(credits),
            "product": metadata_product,
            "billing": billing,
        },
        "return_url": DODO_RETURN_URL,
    }
    checkout = dodo_request("/checkouts", payload)
    session_id = checkout.get("session_id")
    supabase.table("dodo_payments").insert({
        "user_id": user_id,
        "checkout_session_id": session_id,
        "status": "checkout_created",
        "credits": credits,
        "raw_event": checkout,
    }).execute()
    return {
        "checkout_url": checkout.get("checkout_url"),
        "session_id": session_id,
        "credits": credits,
        "price_usd": 14.99,
        "billing": billing,
    }


@app.post("/webhooks/dodo")
async def dodo_webhook(
    request: Request,
    webhook_id: str = Header("", alias="webhook-id"),
    webhook_timestamp: str = Header("", alias="webhook-timestamp"),
    webhook_signature: str = Header("", alias="webhook-signature"),
):
    raw_body = await request.body()
    verify_dodo_signature(raw_body, webhook_id, webhook_timestamp, webhook_signature)
    payload = json.loads(raw_body.decode("utf-8"))
    event_type = payload.get("type") or payload.get("event_type")
    event_data = payload.get("data") or {}

    existing = (
        supabase.table("webhook_events")
        .select("id, processed")
        .eq("webhook_id", webhook_id)
        .execute()
    )
    if existing.data and existing.data[0].get("processed"):
        return {"status": "ok", "duplicate": True}

    supabase.table("webhook_events").upsert({
        "webhook_id": webhook_id,
        "event_type": event_type,
        "data": payload,
        "processed": False,
    }, on_conflict="webhook_id").execute()

    if event_type in {"subscription.active", "subscription.renewed", "subscription.updated"}:
        user_id = user_id_from_metadata(event_data)
        if not user_id:
            raise HTTPException(status_code=400, detail="Missing supabase_user_id in subscription metadata")

        upsert_subscription(user_id, event_data)

        if event_type in {"subscription.active", "subscription.renewed"}:
            metadata = event_data.get("metadata") or {}
            tool_credits = int(metadata.get("credits") or DODO_MONTHLY_CREDITS)
            subscription_id = event_data.get("subscription_id") or webhook_id
            period_key = event_data.get("next_billing_date") or event_data.get("previous_billing_date") or webhook_id
            grant_credits(
                user_id,
                tool_credits,
                event_type,
                f"{subscription_id}:{event_type}:{period_key}:tools", payload, bucket="tools"
            )
            grant_credits(
                user_id, DODO_MONTHLY_WORKSPACE_CREDITS, event_type,
                f"{subscription_id}:{event_type}:{period_key}:workspace", payload, bucket="workspace"

            )

    if event_type in {"subscription.cancelled", "subscription.on_hold", "subscription.failed", "subscription.expired"}:
        subscription_id = event_data.get("subscription_id")
        updates = {
            "status": event_data.get("status") or event_type.removeprefix("subscription."),
            "raw_event": event_data,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if event_type in {"subscription.cancelled", "subscription.failed", "subscription.expired"}:
            updates["valid_until"] = datetime.now(timezone.utc).isoformat()
        if subscription_id:
            supabase.table("subscriptions").update(updates).eq(
                "dodo_subscription_id", subscription_id
            ).execute()

    if event_type == "payment.succeeded":
        metadata = event_data.get("metadata") or payload.get("metadata") or {}
        if metadata.get("billing") == "topup":
            user_id = user_id_from_metadata(event_data) or metadata.get("supabase_user_id")
            if not user_id:
                raise HTTPException(status_code=400, detail="Missing supabase_user_id in top-up metadata")

            payment_id = event_data.get("payment_id") or payload.get("payment_id") or webhook_id
            credits = int(metadata.get("credits") or DODO_TOPUP_CREDITS)
            amount = event_data.get("total_amount") or event_data.get("amount")
            currency = event_data.get("currency")

            supabase.table("dodo_payments").upsert({
                "user_id": user_id,
                "payment_id": payment_id,
                "status": "succeeded",
                "amount": amount,
                "currency": currency,
                "credits": credits,
                "raw_event": payload,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="payment_id").execute()

            grant_credits(user_id, credits, "dodo_topup", payment_id, payload, bucket="tools")

    supabase.table("webhook_events").update({
        "processed": True,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("webhook_id", webhook_id).execute()

    return {"status": "ok", "event_type": event_type}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/payment-success", response_class=HTMLResponse)
def payment_success():
    return """
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Claw Coder Payment Complete</title>
        <style>
          body { font-family: system-ui, sans-serif; margin: 0; background: #0f172a; color: #f8fafc; }
          main { max-width: 680px; margin: 12vh auto; padding: 32px; }
          .panel { border: 1px solid #334155; border-radius: 8px; padding: 28px; background: #111827; }
          h1 { margin: 0 0 12px; font-size: 28px; }
          p { color: #cbd5e1; line-height: 1.6; }
          code { background: #1e293b; padding: 3px 6px; border-radius: 4px; color: #e2e8f0; }
        </style>
      </head>
      <body>
        <main>
          <div class="panel">
            <h1>Payment received</h1>
            <p>Your credits are added after Dodo sends the webhook. This usually takes a few seconds.</p>
            <p>Back in your terminal, run <code>claw credits</code> to confirm your balance.</p>
          </div>
        </main>
      </body>
    </html>
    """

