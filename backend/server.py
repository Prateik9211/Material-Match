from dotenv import load_dotenv
load_dotenv()

import os
import io
import re
import base64
import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Annotated

import bcrypt
import jwt as pyjwt
import fitz  # PyMuPDF
from bson import ObjectId
from fastapi import FastAPI, APIRouter, Request, Response, HTTPException, Depends, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field
from starlette.middleware.cors import CORSMiddleware

from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent

# ============================================================================
# Setup
# ============================================================================
ROOT_DIR = Path(__file__).parent

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = 60 * 24  # 24h for MVP comfort
REFRESH_TOKEN_DAYS = 7

# ----------------------------------------------------------------------------
# Region preference (controls AI prompt context, NOT visible vendor data)
# ----------------------------------------------------------------------------
SUPPORTED_REGIONS = ["India", "Global"]
DEFAULT_REGION = "India"

# Indian-market brand & terminology context used to enrich AI reasoning when
# preferred_region == "India". Kept server-side only — never surfaced in UI.
INDIAN_BRAND_CONTEXT = (
    "INDIA SOURCING CONTEXT (use to enrich reasoning, not to advertise brands):\n"
    "- Laminates / Veneers commonly specified in India: Greenlam, Merino, "
    "Century, Action Tesa, Royale Touche (typical thickness 0.8–1mm; "
    "post-laminate substrate is usually MDF or BWP-grade plywood).\n"
    "- Tiles / Stone widely available in India: Kajaria, Simpolo, Nitco, "
    "Somany (vitrified, GVT, double-charged); regional stones: Kota grey/blue, "
    "Tandur, Jaisalmer yellow, Kadappa black, Indian green marble.\n"
    "- Hardware: Hafele India, Hettich India (soft-close, channel hardware).\n"
    "- Paints / Finishes: Asian Paints Royale, Nerolac, Berger (Royale Aspira, "
    "Excel, Velvet Touch). PU and melamine polish are common wood finishes.\n"
    "Prefer Indian interior-design terminology where it reads naturally — e.g. "
    "'teak veneer', 'PU matt finish', 'MDF + laminate', 'Kota stone', "
    "'pre-laminated board', 'vitrified tile', 'bagged jute rug'. When a "
    "referenced material is uncommon in India, prefer to suggest a plausible "
    "Indian-market equivalent over a global one."
)

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")
LLM_PROVIDER = "anthropic"
LLM_MODEL = "claude-sonnet-4-5-20250929"

# Comma-separated list of emails that are auto-promoted to role="admin".
# Users can add/edit/delete curated affiliate products only via this list.
_ADMIN_EMAILS_RAW = os.environ.get("ADMIN_EMAILS", "").strip()
ADMIN_EMAILS = {e.strip().lower() for e in _ADMIN_EMAILS_RAW.split(",") if e.strip()}
# The legacy ADMIN_EMAIL var (single email) is also honoured for backward-compat.
_legacy_admin = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()
if _legacy_admin:
    ADMIN_EMAILS.add(_legacy_admin)

app = FastAPI(title="MaterialMatch AI")
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("materialmatch")


# ============================================================================
# CORS — registered BEFORE routes so the Starlette CORS middleware can answer
# OPTIONS preflight without hitting any route handler. The allowlist is built
# from env vars so the production deploy can add the custom domain without a
# code change.
# ============================================================================
def _build_cors_origins() -> list[str]:
    """Resolve the CORS allowlist from env. Order of precedence:
       1. CORS_ORIGINS — comma-separated list (production override)
       2. FRONTEND_URL — single preview URL (legacy / dev convenience)
       3. localhost:3000 — always allowed for local dev
    Wildcards ("*") are stripped because we send credentials (cookies) and the
    browser rejects allow_origins=* with credentials.
    """
    raw = (os.environ.get("CORS_ORIGINS") or "").strip()
    origins: list[str] = []
    if raw and raw != "*":
        origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    frontend = (os.environ.get("FRONTEND_URL") or "").strip().rstrip("/")
    if frontend and frontend not in origins:
        origins.append(frontend)
    if "http://localhost:3000" not in origins:
        origins.append("http://localhost:3000")
    return [o for o in origins if o and o != "*"]


_CORS_ORIGINS = _build_cors_origins()
# Best-effort regex so any *.emergent.host preview / production deploy is
# allowed without redeploying when the Emergent platform mints a new hostname.
# The pattern covers ANY number of subdomain levels (preview.emergent.host,
# app.preview.emergent.host, www.materialmatches.com etc.).
_CORS_ORIGIN_REGEX = (
    r"^https://([a-z0-9-]+\.)*(emergent\.host|emergentagent\.com|materialmatches\.com)$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_origin_regex=_CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info(
    f"CORS allowlist: explicit={_CORS_ORIGINS} regex={_CORS_ORIGIN_REGEX}"
)


@app.middleware("http")
async def _log_cors_preflight(request: Request, call_next):
    """Log every OPTIONS preflight with the requesting Origin so production
    misconfigurations are visible in container stdout instead of silent 400s.
    Adds zero overhead on non-OPTIONS requests."""
    if request.method == "OPTIONS":
        origin = request.headers.get("origin", "<none>")
        acr_method = request.headers.get("access-control-request-method", "<none>")
        response = await call_next(request)
        if response.status_code >= 400:
            logger.warning(
                f"CORS preflight REJECTED path={request.url.path} "
                f"origin={origin!r} requested_method={acr_method} "
                f"status={response.status_code} — origin not in allowlist/regex"
            )
        else:
            logger.info(
                f"CORS preflight OK path={request.url.path} origin={origin!r}"
            )
        return response
    return await call_next(request)


# ============================================================================
# MongoDB helpers
# ============================================================================
def _validate_object_id(v):
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, str):
        return v
    raise ValueError("Invalid ObjectId")


PyObjectId = Annotated[str, BeforeValidator(_validate_object_id)]


def to_str_id(doc: dict) -> dict:
    if not doc:
        return doc
    if "_id" in doc:
        doc["id"] = str(doc["_id"])
        del doc["_id"]
    return doc


# ============================================================================
# Auth utilities
# ============================================================================
def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def get_jwt_secret() -> str:
    return os.environ["JWT_SECRET"]


def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_MINUTES),
        "type": "access",
    }
    return pyjwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_DAYS),
        "type": "refresh",
    }
    return pyjwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def set_auth_cookies(response: Response, access: str, refresh: str):
    response.set_cookie("access_token", access, httponly=True, secure=True, samesite="none",
                        max_age=ACCESS_TOKEN_MINUTES * 60, path="/")
    response.set_cookie("refresh_token", refresh, httponly=True, secure=True, samesite="none",
                        max_age=REFRESH_TOKEN_DAYS * 86400, path="/")


def clear_auth_cookies(response: Response):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = pyjwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        user = to_str_id(user)
        user.pop("password_hash", None)
        # Backfill default preferred_region for older users so callers can rely on it.
        if user.get("preferred_region") not in SUPPORTED_REGIONS:
            user["preferred_region"] = DEFAULT_REGION
        # Auto-promote admin emails. Idempotent — DB write only when needed.
        email_lc = (user.get("email") or "").lower()
        if email_lc in ADMIN_EMAILS and user.get("role") != "admin":
            await db.users.update_one(
                {"_id": ObjectId(user["id"])},
                {"$set": {"role": "admin"}},
            )
            user["role"] = "admin"
        return user
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Dependency: require role='admin' (auto-promoted via ADMIN_EMAILS)."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ============================================================================
# Models
# ============================================================================
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    name: str = Field(min_length=1)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1)
    client_name: Optional[str] = ""
    notes: Optional[str] = ""


class AnalyzeRequest(BaseModel):
    project_id: str
    reference_image_b64: str  # data URL or raw base64
    reference_mime: str = "image/jpeg"
    prompt: Optional[str] = ""
    catalogue_items: List[dict] = []  # [{name, image_b64, mime}]


# ============================================================================
# Auth endpoints
# ============================================================================
@api_router.post("/auth/register")
async def register(payload: RegisterRequest, response: Response):
    email = payload.email.lower().strip()
    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    doc = {
        "email": email,
        "password_hash": hash_password(payload.password),
        "name": payload.name,
        "role": "user",
        "preferred_region": DEFAULT_REGION,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    res = await db.users.insert_one(doc)
    uid = str(res.inserted_id)
    access = create_access_token(uid, email)
    refresh = create_refresh_token(uid)
    set_auth_cookies(response, access, refresh)
    return {"id": uid, "email": email, "name": payload.name, "role": "user",
            "preferred_region": DEFAULT_REGION,
            # Returned in the response body too so the frontend can send it as
            # `Authorization: Bearer …` on subsequent calls. This protects auth
            # from any cross-site cookie quirk (third-party-cookie blocking,
            # proxy stripping, intermittent SameSite enforcement, etc.).
            "access_token": access}


@api_router.post("/auth/login")
async def login(payload: LoginRequest, response: Response):
    email = payload.email.lower().strip()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    uid = str(user["_id"])
    access = create_access_token(uid, email)
    refresh = create_refresh_token(uid)
    set_auth_cookies(response, access, refresh)
    return {
        "id": uid,
        "email": email,
        "name": user.get("name", ""),
        "role": user.get("role", "user"),
        "preferred_region": user.get("preferred_region") if user.get("preferred_region") in SUPPORTED_REGIONS else DEFAULT_REGION,
        # See register() comment — bearer-token fallback for cross-site cookie issues.
        "access_token": access,
    }


@api_router.post("/auth/logout")
async def logout(response: Response, user: dict = Depends(get_current_user)):
    clear_auth_cookies(response)
    return {"ok": True}


@api_router.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return user


# ============================================================================
# User preferences (region only for MVP — gates Indian-market AI context)
# ============================================================================
class PreferencesUpdate(BaseModel):
    preferred_region: str

    @classmethod
    def model_validate_strict(cls, data):
        return cls.model_validate(data)


@api_router.get("/users/me/preferences")
async def get_my_preferences(user: dict = Depends(get_current_user)):
    return {"preferred_region": user.get("preferred_region", DEFAULT_REGION)}


@api_router.put("/users/me/preferences")
async def update_my_preferences(payload: PreferencesUpdate,
                                user: dict = Depends(get_current_user)):
    region = (payload.preferred_region or "").strip()
    if region not in SUPPORTED_REGIONS:
        raise HTTPException(
            status_code=400,
            detail=f"preferred_region must be one of: {', '.join(SUPPORTED_REGIONS)}",
        )
    await db.users.update_one(
        {"_id": ObjectId(user["id"])},
        {"$set": {"preferred_region": region}},
    )
    return {"preferred_region": region}


@api_router.post("/auth/refresh")
async def refresh(request: Request, response: Response):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")
    try:
        payload = pyjwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        uid = payload["sub"]
        user = await db.users.find_one({"_id": ObjectId(uid)})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        access = create_access_token(uid, user["email"])
        response.set_cookie("access_token", access, httponly=True, secure=True, samesite="none",
                            max_age=ACCESS_TOKEN_MINUTES * 60, path="/")
        return {"ok": True, "access_token": access}
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ============================================================================
# Projects
# ============================================================================
@api_router.post("/projects")
async def create_project(payload: ProjectCreate, user: dict = Depends(get_current_user)):
    doc = {
        "user_id": user["id"],
        "name": payload.name,
        "client_name": payload.client_name or "",
        "notes": payload.notes or "",
        "status": "draft",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    res = await db.projects.insert_one(doc)
    doc["id"] = str(res.inserted_id)
    doc.pop("_id", None)
    return doc


@api_router.get("/projects")
async def list_projects(user: dict = Depends(get_current_user)):
    cursor = db.projects.find({"user_id": user["id"]}).sort("created_at", -1)
    items = []
    async for d in cursor:
        d = to_str_id(d)
        # Strip heavy fields from list view
        d.pop("reference_image_b64", None)
        d.pop("catalogue_items", None)
        d.pop("analysis", None)
        items.append(d)
    return items


@api_router.get("/projects/{project_id}")
async def get_project(project_id: str, user: dict = Depends(get_current_user)):
    doc = None
    try:
        doc = await db.projects.find_one({"_id": ObjectId(project_id), "user_id": user["id"]})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project id")
    if not doc:
        raise HTTPException(status_code=404, detail="Project not found")
    return to_str_id(doc)


@api_router.delete("/projects/{project_id}")
async def delete_project(project_id: str, user: dict = Depends(get_current_user)):
    res = await db.projects.delete_one({"_id": ObjectId(project_id), "user_id": user["id"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"ok": True}


# ============================================================================
# Upload endpoints
# ============================================================================
def _normalize_b64(data: str) -> tuple[str, str]:
    """Returns (mime, base64_without_prefix)"""
    if data.startswith("data:"):
        try:
            header, b64 = data.split(",", 1)
            mime = header.split(";")[0].replace("data:", "")
            return mime, b64
        except Exception:
            return "image/jpeg", data
    return "image/jpeg", data


@api_router.post("/projects/{project_id}/reference")
async def upload_reference(project_id: str, file: UploadFile = File(...),
                           user: dict = Depends(get_current_user)):
    content = await file.read()
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image too large (max 8MB)")
    mime = file.content_type or "image/jpeg"
    if mime not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(status_code=400, detail="Unsupported image format")
    b64 = base64.b64encode(content).decode("utf-8")
    await db.projects.update_one(
        {"_id": ObjectId(project_id), "user_id": user["id"]},
        {"$set": {"reference_image_b64": b64, "reference_mime": mime,
                  "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    return {"ok": True, "mime": mime, "size": len(content)}


@api_router.post("/projects/{project_id}/catalogue")
async def upload_catalogue(project_id: str,
                           files: List[UploadFile] = File(...),
                           user: dict = Depends(get_current_user)):
    items = []
    for f in files:
        content = await f.read()
        mime = f.content_type or ""
        if mime == "application/pdf" or f.filename.lower().endswith(".pdf"):
            # Convert PDF pages to images (max 6 pages)
            try:
                pdf = fitz.open(stream=content, filetype="pdf")
                for i, page in enumerate(pdf):
                    if i >= 6:
                        break
                    pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                    img_bytes = pix.tobytes("png")
                    items.append({
                        "name": f"{f.filename} - p{i+1}",
                        "image_b64": base64.b64encode(img_bytes).decode("utf-8"),
                        "mime": "image/png",
                    })
                pdf.close()
            except Exception:
                logger.exception("PDF parse failed")
                raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {f.filename}")
        elif mime in ("image/jpeg", "image/png", "image/webp"):
            if len(content) > 5 * 1024 * 1024:
                continue
            items.append({
                "name": f.filename,
                "image_b64": base64.b64encode(content).decode("utf-8"),
                "mime": mime,
            })
    await db.projects.update_one(
        {"_id": ObjectId(project_id), "user_id": user["id"]},
        {"$set": {"catalogue_items": items,
                  "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    return {"ok": True, "count": len(items),
            "items": [{"name": it["name"], "mime": it["mime"]} for it in items]}


@api_router.get("/projects/{project_id}/catalogue/{idx}")
async def get_catalogue_item(project_id: str, idx: int, user: dict = Depends(get_current_user)):
    doc = await db.projects.find_one({"_id": ObjectId(project_id), "user_id": user["id"]})
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    items = doc.get("catalogue_items", [])
    if idx < 0 or idx >= len(items):
        raise HTTPException(status_code=404, detail="Item not found")
    it = items[idx]
    return {"name": it["name"], "mime": it["mime"],
            "data_url": f"data:{it['mime']};base64,{it['image_b64']}"}


@api_router.get("/projects/{project_id}/reference-image")
async def get_reference_image(project_id: str, user: dict = Depends(get_current_user)):
    doc = await db.projects.find_one({"_id": ObjectId(project_id), "user_id": user["id"]})
    if not doc or not doc.get("reference_image_b64"):
        raise HTTPException(status_code=404, detail="Not found")
    mime = doc.get("reference_mime", "image/jpeg")
    return {"data_url": f"data:{mime};base64,{doc['reference_image_b64']}"}


# ============================================================================
# JSON helper (used by real-AI analysis below)
# ============================================================================
def _parse_json(text: str) -> dict:
    """Strip code fences and parse JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if "```" in text else text
        if text.startswith("json"):
            text = text[4:]
        text = text.strip("` \n")
        if text.endswith("```"):
            text = text[:-3].strip()
    try:
        return json.loads(text)
    except Exception:
        # try to find first { ... }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start:end+1])
        raise

# ============================================================================
# Mock Material Analysis (MVP — no real LLM, no catalogue matching)
# ============================================================================
MOCK_MATERIAL_LIBRARY = [
    {
        "zone": "Floor",
        "material_family": "wood",
        "material_type": "Engineered Oak Plank",
        "color": "Warm Walnut Brown",
        "texture": "Visible natural grain",
        "finish": "Matte oiled",
        "design_style": "Scandinavian",
        "keywords": ["wood", "warm", "natural", "matte", "plank"],
        "confidence": 92,
    },
    {
        "zone": "Walls",
        "material_family": "wall",
        "material_type": "Lime Plaster",
        "color": "Bone White",
        "texture": "Slightly mottled",
        "finish": "Matte chalky",
        "design_style": "Wabi-sabi",
        "keywords": ["plaster", "minimal", "soft", "chalky"],
        "confidence": 87,
    },
    {
        "zone": "Ceiling",
        "material_family": "ceiling",
        "material_type": "Painted Drywall",
        "color": "Off-white",
        "texture": "Smooth",
        "finish": "Eggshell",
        "design_style": "Modern Minimalist",
        "keywords": ["ceiling", "smooth", "neutral", "paint"],
        "confidence": 81,
    },
    {
        "zone": "Sofa",
        "material_family": "upholstery",
        "material_type": "Bouclé Upholstery",
        "color": "Cream Beige",
        "texture": "Looped, fluffy",
        "finish": "Soft matte",
        "design_style": "Contemporary Mid-century",
        "keywords": ["fabric", "bouclé", "cozy", "neutral", "textured"],
        "confidence": 89,
    },
    {
        "zone": "Coffee Table",
        "material_family": "stone",
        "material_type": "Travertine Stone",
        "color": "Sandy Cream",
        "texture": "Open-pore, banded",
        "finish": "Honed",
        "design_style": "Organic Modern",
        "keywords": ["stone", "travertine", "honed", "earthy"],
        "confidence": 84,
    },
    {
        "zone": "Lighting",
        "material_family": "metal",
        "material_type": "Brushed Brass",
        "color": "Warm Gold",
        "texture": "Linear brush marks",
        "finish": "Brushed satin",
        "design_style": "Modern Luxe",
        "keywords": ["metal", "brass", "warm", "accent"],
        "confidence": 78,
    },
    {
        "zone": "Rug",
        "material_family": "textile",
        "material_type": "Hand-tufted Wool",
        "color": "Sand & Ivory",
        "texture": "Loop-pile",
        "finish": "Natural fibre",
        "design_style": "Japandi",
        "keywords": ["rug", "wool", "neutral", "layered"],
        "confidence": 86,
    },
    {
        "zone": "Accent Wall",
        "material_family": "wood",
        "material_type": "Vertical Slatted Oak",
        "color": "Mid-tone Honey",
        "texture": "Linear ribbed",
        "finish": "Lacquered satin",
        "design_style": "Japandi",
        "keywords": ["wood", "slatted", "linear", "warm"],
        "confidence": 83,
    },
]


# Static India-market hint per material family — used by mock analysis only.
# Real-AI analysis builds these dynamically via the LLM.
INDIA_ALTERNATIVES_BY_FAMILY = {
    "wood": "Indian teak veneer with PU matt finish (Greenlam/Century range) — widely stocked.",
    "wall": "Lime-finish washable distemper or Asian Paints Royale Aspira — common in Indian homes.",
    "ceiling": "Gypsum board ceiling with Berger / Asian Paints emulsion — standard Indian spec.",
    "upholstery": "Cotton-bouclé blend from D'Decor or Sarom — widely available in Indian fabric stores.",
    "stone": "Kota stone honed or Indian Statuario marble — common via regional stone suppliers.",
    "metal": "Brushed brass / antique brass profiles from Hafele India or Hettich India.",
    "textile": "Hand-tufted wool rug from Jaipur Rugs or Obeetee — Indian alternative.",
    "flooring": "Indian engineered teak plank or Kajaria wood-look vitrified tile — common spec.",
    "furniture": "Indian sheesham or teak furniture from regional carpenters / urban brands.",
    "lighting": "Brushed brass pendant from local Indian foundries; Hafele India fittings.",
    "decor": "Locally crafted brass or terracotta accents — easy to source pan-India.",
    "door": "Flush door with teak veneer + PU finish — standard Indian residential spec.",
    "window": "Aluminium frame with toughened glass — common urban-Indian spec.",
    "other": None,
}


@api_router.post("/projects/{project_id}/mock-analyze")
async def mock_analyze(project_id: str, user: dict = Depends(get_current_user)):
    doc = await db.projects.find_one({"_id": ObjectId(project_id), "user_id": user["id"]})
    if not doc:
        raise HTTPException(status_code=404, detail="Project not found")
    if not doc.get("reference_image_b64"):
        raise HTTPException(status_code=400, detail="Upload a reference image first")

    # Stable mock based on project_id so revisits show the same result
    seed = int(ObjectId(project_id).binary[-4:].hex(), 16)
    count = 5 + (seed % 4)  # 5-8 rows
    start = seed % len(MOCK_MATERIAL_LIBRARY)
    region = user.get("preferred_region", DEFAULT_REGION)
    rows = []
    for i in range(count):
        base = dict(MOCK_MATERIAL_LIBRARY[(start + i) % len(MOCK_MATERIAL_LIBRARY)])
        if region == "India":
            base["indian_alternative"] = INDIA_ALTERNATIVES_BY_FAMILY.get(
                base.get("material_family", "other")
            )
        rows.append(base)

    mock_analysis = {
        "rows": rows,
        "summary": {
            "design_style": "Warm modern (mock)",
            "material_palette": "Wood, stone, textile, plaster",
            "key_finishes": "Matt PU, honed stone, woven fabric",
            "sourcing_note": (
                "Similar finishes are widely available across Indian dealers "
                "(Greenlam, Century, Kajaria, Asian Paints)."
                if region == "India"
                else "Common materials sourceable from most global suppliers."
            ),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": "mock-v3",
        "region": region,
    }

    await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$set": {
            "mock_analysis": mock_analysis,
            "status": "completed",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }}
    )
    return mock_analysis


# ============================================================================
# Real AI Material Analysis (OpenAI gpt-4o-mini vision)
# ============================================================================
ENABLE_REAL_ANALYSIS = os.environ.get("ENABLE_REAL_ANALYSIS", "false").lower() == "true"
LLM_PROVIDER_ANALYSIS = os.environ.get("LLM_PROVIDER_ANALYSIS", "openai")
LLM_MODEL_ANALYSIS = os.environ.get("LLM_MODEL_ANALYSIS", "gpt-4o-mini")
LLM_ANALYSIS_TIMEOUT_S = int(os.environ.get("LLM_ANALYSIS_TIMEOUT_S", "45"))
LLM_ANALYSIS_MAX_RETRIES = int(os.environ.get("LLM_ANALYSIS_MAX_RETRIES", "2"))
LLM_ANALYSIS_DAILY_USER_BUDGET = int(os.environ.get("LLM_ANALYSIS_DAILY_USER_BUDGET", "20"))
LLM_ANALYSIS_REF_IMAGE_MAX_BYTES = int(os.environ.get("LLM_ANALYSIS_REF_IMAGE_MAX_BYTES", "5242880"))
LLM_ANALYSIS_DEDUP_WINDOW_S = int(os.environ.get("LLM_ANALYSIS_DEDUP_WINDOW_S", "600"))

MATERIAL_FAMILIES = [
    "flooring", "wall", "ceiling", "window", "door",
    "furniture", "upholstery", "textile", "stone", "metal",
    "wood", "decor", "lighting", "other",
]

ANALYSIS_SYSTEM_PROMPT_V2 = (
    "You are an EXPERT INTERIOR SPECIFICATION CONSULTANT — not a generic object "
    "detector. Your job is to help an architect / interior designer specify, "
    "source and communicate materials to their client. Think in terms of "
    "SURFACES, FINISHES and SPECIFICATION ZONES, not objects.\n\n"
    "PRIORITISE detecting sourcing-relevant surfaces:\n"
    " • Headboard finish · feature wall · wall paneling · fluted panels\n"
    " • Laminate / veneer / joinery / wardrobe / cabinet finishes\n"
    " • Upholstery fabric · rugs / carpets · flooring\n"
    " • Wall paint · ceiling finish · marble / stone / tile surfaces\n"
    " • Side-table / lighting-fixture finishes\n"
    " • Artwork or frame material · handles / hardware finish\n"
    " • Decorative metal finish · glass / mirror finish\n\n"
    "AVOID generic object rows like 'Bed → Wood' or 'Sofa → Fabric' UNLESS "
    "the object itself is the sourcing target and no more specific surface can "
    "be named. For a bed, prefer 'Headboard Feature Panel'. For a sofa, prefer "
    "'Sofa Upholstery Fabric'. For a table, prefer 'Table-top Finish'.\n\n"
    "Be conservative — only report zones you can clearly identify. Reply with "
    "ONLY a valid JSON object. No markdown fences, no prose."
)

PROCUREMENT_DIFFICULTY = ["Easy", "Medium", "Difficult"]

ANALYSIS_USER_PROMPT = (
    "Analyse this interior reference image like a specification consultant. "
    "Return SURFACE-level rows, not object rows. Aim for 4–8 rows.\n\n"
    "Return ONLY this JSON shape:\n"
    "{\n"
    '  "summary": {\n'
    '    "design_style": "one-line style label, e.g. Warm modern / contemporary",\n'
    '    "material_palette": "one line naming the main materials (e.g. Walnut veneer, textured paint, brushed brass, natural woven fibre)",\n'
    '    "key_finishes": "one line naming key finish types (e.g. Matt PU, satin veneer, honed stone)",\n'
    '    "sourcing_note": "one line India sourcing guidance if region is India, else global tone"\n'
    "  },\n"
    '  "rows": [\n'
    "    {\n"
    '      "zone": "SPECIFICATION zone — e.g. Headboard Feature Panel, Wall Paint, Bedroom Flooring, Ceiling Finish, Sofa Upholstery Fabric",\n'
    '      "material_family": "one of: ' + ", ".join(MATERIAL_FAMILIES) + '",\n'
    '      "material_type": "concise finish/product description, e.g. Fluted walnut laminate or veneer panel",\n'
    '      "color": "short descriptive colour, e.g. Warm walnut brown",\n'
    '      "texture": "short texture descriptor, e.g. Vertical ribbed/fluted",\n'
    '      "finish": "short finish descriptor, e.g. Matt PU / satin",\n'
    '      "design_style": "short style label",\n'
    '      "keywords": ["3-6 short lowercase tags"],\n'
    '      "confidence": 0,\n'
    '      "procurement_difficulty": "one of: ' + ", ".join(PROCUREMENT_DIFFICULTY) + '"\n'
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Rules:\n"
    "- confidence is an INTEGER 0–100 (NOT a float 0–1).\n"
    "- procurement_difficulty reflects how easy the material is to source in "
    "an urban Indian design context (Easy = mainstream dealers / e-commerce; "
    "Medium = need specific brand or fabricator; Difficult = imported / bespoke).\n"
    "- material_family MUST be one of the listed enum values.\n"
    "- Prefer SURFACE zones ('Headboard Panel', 'Feature Wall', 'Sofa Upholstery') "
    "over object zones ('Bed', 'Sofa'). Only fall back to object zones if no "
    "specific surface is visible.\n"
    "- Return 4 to 8 rows. Skip ambiguous / occluded surfaces.\n"
    "- Reply with ONLY the JSON object."
)

INDIA_ANALYSIS_BLOCK = (
    "\n\n" + INDIAN_BRAND_CONTEXT + "\n\n"
    "BECAUSE the user prefers India sourcing, ADD these 4 optional fields per "
    "row (populate for EVERY row where useful):\n"
    '  "indian_alternative": "≤ 120 chars naming an Indian-market equivalent, '
    'e.g. \\"Walnut laminate on routed MDF (Greenlam / Century range)\\"",\n'
    '  "brands_to_check": ["array of 2–5 Indian brand names to check, from '
    'the context list above — e.g. [\\"Greenlam\\", \\"Merino\\", \\"Century\\"]"],\n'
    '  "vendor_type": "≤ 60 chars naming the vendor category, e.g. '
    '\\"Laminate dealer + carpenter/CNC panel fabricator\\"",\n'
    '  "sourcing_keywords": ["array of 2–5 Indian-market search phrases, '
    'e.g. [\\"walnut fluted panel India\\", \\"ribbed MDF panel\\", '
    '\\"walnut laminate sheet\\"]"]\n'
    "Also update the top-level summary.sourcing_note to reflect India sourcing "
    "clearly (mention typical Indian brands / vendor categories where natural). "
    "Never invent brand-SKU pairs; only reference the brand names from the "
    "context list above."
)


def _build_analysis_prompt(region: str) -> str:
    """Compose the analysis user prompt, optionally adding the India sourcing block."""
    if region == "India":
        return ANALYSIS_USER_PROMPT + INDIA_ANALYSIS_BLOCK
    return ANALYSIS_USER_PROMPT

ANALYSIS_RETRY_NUDGE = (
    "Your previous response was not valid JSON for the requested schema. "
    "Reply with ONLY the JSON object — no markdown, no commentary. "
    "Ensure confidence is an integer 0-100 and material_family is one of the enum values."
)


def _clean_optional_list(value, item_max: int = 60, max_items: int = 5) -> list:
    """Coerce an optional LLM list-of-strings field into a clean list (may be empty)."""
    if not isinstance(value, list):
        return []
    out: list = []
    for item in value[:max_items]:
        if not isinstance(item, str):
            continue
        s = item.strip()[:item_max]
        if s:
            out.append(s)
    return out


def _clean_summary(value) -> dict:
    """Coerce the optional top-level 'summary' object into a stable shape.
    Always returns a dict with the same 4 string keys (empty when absent)."""
    src = value if isinstance(value, dict) else {}
    return {
        "design_style": _clean_optional_text(src.get("design_style"), max_len=120) or "",
        "material_palette": _clean_optional_text(src.get("material_palette"), max_len=240) or "",
        "key_finishes": _clean_optional_text(src.get("key_finishes"), max_len=240) or "",
        "sourcing_note": _clean_optional_text(src.get("sourcing_note"), max_len=400) or "",
    }


def _validate_analysis_payload(data) -> dict:
    """Strictly validate the LLM payload. Raises ValueError on any deviation.
    Returns {'rows': [...], 'summary': {...}} — summary is optional (may be empty).
    """
    if not isinstance(data, dict) or "rows" not in data or not isinstance(data["rows"], list):
        raise ValueError("payload missing 'rows' array")
    raw_rows = data["rows"]
    if not (1 <= len(raw_rows) <= 12):
        raise ValueError(f"row count {len(raw_rows)} outside 1-12")

    required_str = ["zone", "material_type", "color", "texture", "finish", "design_style"]
    cleaned = []
    for i, r in enumerate(raw_rows):
        if not isinstance(r, dict):
            raise ValueError(f"row {i} not an object")
        for k in required_str:
            v = r.get(k)
            if not isinstance(v, str) or not v.strip():
                raise ValueError(f"row {i} field '{k}' missing or not non-empty string")
        family = r.get("material_family")
        if family not in MATERIAL_FAMILIES:
            raise ValueError(f"row {i} material_family '{family}' not in enum")
        kws = r.get("keywords") or []
        if not isinstance(kws, list) or not all(isinstance(k, str) for k in kws):
            raise ValueError(f"row {i} keywords not list[str]")
        conf = r.get("confidence")
        if isinstance(conf, bool):
            raise ValueError(f"row {i} confidence is bool")
        if isinstance(conf, float) and conf <= 1.0:
            raise ValueError(f"row {i} confidence {conf} looks like 0-1 scale, expected 0-100")
        try:
            conf_int = int(round(float(conf)))
        except (TypeError, ValueError):
            raise ValueError(f"row {i} confidence not numeric")
        if not (0 <= conf_int <= 100):
            raise ValueError(f"row {i} confidence {conf_int} outside 0-100")

        # Optional: procurement_difficulty (soft-coerced, never blocks)
        proc = r.get("procurement_difficulty")
        proc_val = proc if proc in PROCUREMENT_DIFFICULTY else None

        cleaned.append({
            "zone": r["zone"].strip(),
            "material_family": family,
            "material_type": r["material_type"].strip(),
            "color": r["color"].strip(),
            "texture": r["texture"].strip(),
            "finish": r["finish"].strip(),
            "design_style": r["design_style"].strip(),
            "keywords": [k.strip().lower() for k in kws[:6] if k.strip()],
            "confidence": conf_int,
            "procurement_difficulty": proc_val,
            # V2 India sourcing fields — optional, tolerant to omission.
            "indian_alternative": _clean_optional_text(r.get("indian_alternative"), max_len=120),
            "brands_to_check": _clean_optional_list(r.get("brands_to_check"), item_max=48, max_items=5),
            "vendor_type": _clean_optional_text(r.get("vendor_type"), max_len=60),
            "sourcing_keywords": _clean_optional_list(r.get("sourcing_keywords"), item_max=80, max_items=5),
        })
    return {"rows": cleaned, "summary": _clean_summary(data.get("summary"))}


def _clean_optional_text(value, max_len: int = 120):
    """Coerce a possibly-missing/empty/non-string LLM field into a trimmed str or None."""
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped[:max_len]


async def _check_and_increment_quota(user_id: str) -> int:
    """Returns current count after increment. Raises HTTPException 429 if over budget."""
    day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    doc = await db.usage_counters.find_one_and_update(
        {"user_id": user_id, "day": day_key},
        {"$inc": {"analyze_count": 1},
         "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
        upsert=True,
        return_document=True,
    )
    count = (doc or {}).get("analyze_count", 1)
    if count > LLM_ANALYSIS_DAILY_USER_BUDGET:
        await db.usage_counters.update_one(
            {"user_id": user_id, "day": day_key},
            {"$inc": {"analyze_count": -1}},
        )
        raise HTTPException(
            status_code=429,
            detail=f"Daily AI analysis quota exceeded ({LLM_ANALYSIS_DAILY_USER_BUDGET} per day). Try again tomorrow.",
        )
    return count


async def run_real_analysis(project_id: str, user_id: str, ref_b64: str, region: str = DEFAULT_REGION) -> dict:
    """Call OpenAI vision and return validated payload. Raises HTTPException on failure."""
    import asyncio

    base_user_prompt = _build_analysis_prompt(region)

    last_error = ""
    last_raw = ""
    for attempt in range(LLM_ANALYSIS_MAX_RETRIES + 1):
        try:
            chat = LlmChat(
                api_key=EMERGENT_LLM_KEY,
                session_id=f"analyze-{project_id}-{secrets.token_hex(4)}",
                system_message=ANALYSIS_SYSTEM_PROMPT_V2,
            ).with_model(LLM_PROVIDER_ANALYSIS, LLM_MODEL_ANALYSIS)

            ref_img = ImageContent(image_base64=ref_b64)
            user_text = base_user_prompt if attempt == 0 else base_user_prompt + "\n\n" + ANALYSIS_RETRY_NUDGE
            msg = UserMessage(text=user_text, file_contents=[ref_img])

            raw = await asyncio.wait_for(
                chat.send_message(msg),
                timeout=LLM_ANALYSIS_TIMEOUT_S,
            )
            last_raw = raw
            parsed = _parse_json(raw)
            cleaned = _validate_analysis_payload(parsed)
            return {
                "rows": cleaned["rows"],
                "summary": cleaned["summary"],
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "version": f"real-openai-{LLM_MODEL_ANALYSIS}-v2",
                "region": region,
            }
        except asyncio.TimeoutError:
            last_error = f"timeout after {LLM_ANALYSIS_TIMEOUT_S}s"
            logger.warning(f"analyze timeout attempt={attempt} project={project_id}")
            if attempt < LLM_ANALYSIS_MAX_RETRIES:
                await asyncio.sleep(0.5 * (3 ** attempt))
                continue
            raise HTTPException(status_code=504, detail="AI analysis timed out. Please try again.")
        except ValueError as ve:
            last_error = f"schema: {ve}"
            logger.warning(
                f"analyze schema-fail attempt={attempt} project={project_id} err={ve} raw_excerpt={last_raw[:200]!r}"
            )
            if attempt < LLM_ANALYSIS_MAX_RETRIES:
                continue
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "AI returned a malformed analysis. Please retry.",
                    "error": str(ve),
                    "raw_excerpt": last_raw[:200],
                },
            )
        except HTTPException:
            raise
        except Exception as e:
            last_error = f"upstream: {type(e).__name__}: {e}"
            logger.exception(f"analyze upstream-fail attempt={attempt} project={project_id}")
            if attempt < LLM_ANALYSIS_MAX_RETRIES:
                await asyncio.sleep(0.5 * (3 ** attempt))
                continue
            raise HTTPException(status_code=502, detail=f"AI service error: {last_error}")
    raise HTTPException(status_code=500, detail="Unexpected analysis state")


@api_router.post("/projects/{project_id}/analyze")
async def real_analyze(project_id: str, user: dict = Depends(get_current_user)):
    """Real-AI material analysis endpoint. Falls back to mock when ENABLE_REAL_ANALYSIS is off."""
    if not ENABLE_REAL_ANALYSIS or not EMERGENT_LLM_KEY:
        return await mock_analyze(project_id, user)

    doc = await db.projects.find_one({"_id": ObjectId(project_id), "user_id": user["id"]})
    if not doc:
        raise HTTPException(status_code=404, detail="Project not found")

    ref_b64 = doc.get("reference_image_b64")
    if not ref_b64:
        raise HTTPException(status_code=400, detail="Upload a reference image first")

    ref_bytes_len = (len(ref_b64) * 3) // 4
    if ref_bytes_len > LLM_ANALYSIS_REF_IMAGE_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Reference image exceeds {LLM_ANALYSIS_REF_IMAGE_MAX_BYTES // (1024 * 1024)} MiB AI-analysis limit.",
        )

    existing = doc.get("mock_analysis") or {}
    if existing.get("version", "").startswith("real-") and existing.get("generated_at"):
        try:
            gen_at = datetime.fromisoformat(existing["generated_at"].replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - gen_at).total_seconds()
            if age < LLM_ANALYSIS_DEDUP_WINDOW_S:
                logger.info(f"analyze dedup-hit project={project_id} age={age:.1f}s")
                return existing
        except Exception:
            pass

    if doc.get("status") == "analyzing":
        updated_at = doc.get("updated_at")
        try:
            up = datetime.fromisoformat((updated_at or "").replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - up).total_seconds() < 60:
                raise HTTPException(status_code=409, detail="Analysis already in progress")
        except (TypeError, ValueError):
            pass

    await _check_and_increment_quota(user["id"])

    await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$set": {"status": "analyzing", "updated_at": datetime.now(timezone.utc).isoformat()}}
    )

    started = datetime.now(timezone.utc)
    analysis = None
    try:
        analysis = await run_real_analysis(
            project_id, user["id"], ref_b64,
            region=user.get("preferred_region", DEFAULT_REGION),
        )
    except HTTPException:
        day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await db.usage_counters.update_one(
            {"user_id": user["id"], "day": day_key},
            {"$inc": {"analyze_count": -1}},
        )
        await db.projects.update_one(
            {"_id": ObjectId(project_id)},
            {"$set": {"status": "completed" if existing else "draft",
                      "updated_at": datetime.now(timezone.utc).isoformat()}}
        )
        raise

    elapsed_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
    logger.info(
        f"ai_call provider={LLM_PROVIDER_ANALYSIS} model={LLM_MODEL_ANALYSIS} "
        f"project={project_id} user={user['id']} ms={elapsed_ms:.0f} rows={len(analysis['rows'])}"
    )

    await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$set": {
            "mock_analysis": analysis,
            "status": "completed",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }}
    )

    # Sprint 2: run products & fixtures detection alongside materials.
    # Best-effort — failures are logged but don't break material analysis.
    try:
        products_result = await _run_products_pipeline(
            project_id, user["id"], ref_b64,
            region=user.get("preferred_region", DEFAULT_REGION),
        )
        if products_result:
            analysis["products"] = products_result.get("products", [])
            analysis["products_generated_at"] = products_result.get("generated_at")
            await db.projects.update_one(
                {"_id": ObjectId(project_id)},
                {"$set": {
                    "products_detected": products_result,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }}
            )
    except Exception:
        logger.exception(f"products-pipeline failed project={project_id}")

    return analysis


# ============================================================================
# Sprint 2: Products & Fixtures Detection (separate AI pass from materials)
# ============================================================================
ENABLE_REAL_PRODUCTS = os.environ.get("ENABLE_REAL_PRODUCTS", "true").lower() == "true"
LLM_MODEL_PRODUCTS = os.environ.get("LLM_MODEL_PRODUCTS", "gpt-4o-mini")
LLM_PRODUCTS_TIMEOUT_S = int(os.environ.get("LLM_PRODUCTS_TIMEOUT_S", "45"))

PRODUCT_CATEGORIES = [
    "lighting", "furniture", "decor", "art", "textile-decor",
    "fixture", "plant-planter", "electronics", "other",
]

PRODUCTS_SYSTEM_PROMPT = (
    "You are an INTERIOR PRODUCT & FIXTURE identifier. Your job is to spot "
    "SHOPPABLE PRODUCTS visible in an interior reference image — NOT surface "
    "materials or finishes. Focus on named product categories a designer would "
    "source from a supplier catalogue or Indian e-commerce site (Pepperfry, "
    "Urban Ladder, IKEA India, WoodenStreet, Hafele India, Amazon.in). "
    "Reply with ONLY a valid JSON object. No markdown fences, no prose."
)

PRODUCTS_USER_PROMPT = (
    "Identify 3–8 distinct SHOPPABLE PRODUCTS or FIXTURES visible in this "
    "interior. SKIP surface materials (walls, floors, paint, plaster, tiles). "
    "Focus on standalone items a designer would BUY from a store or catalogue: "
    "lighting fixtures, furniture pieces, decor objects, art frames, cushions, "
    "rugs, curtains, plants + planters, mirrors, hardware, faucets, sanitary "
    "fittings, etc.\n\n"
    "Return ONLY this JSON shape:\n"
    "{\n"
    '  "products": [\n'
    "    {\n"
    '      "product_name": "concise product name, e.g. Brass Pendant Light",\n'
    '      "category": "one of: ' + ", ".join(PRODUCT_CATEGORIES) + '",\n'
    '      "description": "one sentence describing the product ≤ 140 chars",\n'
    '      "style_keywords": ["3-5 style tags, e.g. modern, minimalist, mid-century"],\n'
    '      "color_keywords": ["1-3 dominant colour tags, e.g. brass, warm white"],\n'
    '      "material_keywords": ["1-3 material tags, e.g. brass, glass, wood"],\n'
    '      "finish_keywords": ["0-3 finish tags, e.g. brushed, matte, polished"],\n'
    '      "estimated_price_inr": "INR price band string, e.g. ₹4,000–₹12,000",\n'
    '      "search_keywords": ["2-4 India-market search phrases, e.g. brass pendant light india"],\n'
    '      "confidence": 0\n'
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Rules:\n"
    "- confidence is an INTEGER 0-100.\n"
    "- category MUST be one of the listed enum values.\n"
    "- estimated_price_inr should reflect typical Indian market prices for the "
    "product type (use ₹ symbol, e.g. '₹2,000–₹6,000').\n"
    "- search_keywords should be phrases a designer would type into Amazon.in "
    "or Pepperfry to find similar products.\n"
    "- Return 3–8 products. Skip ambiguous or heavily-occluded items.\n"
    "- Reply with ONLY the JSON object."
)

# Mock products library — deterministic fallback when real AI is off.
MOCK_PRODUCTS_LIBRARY = [
    {
        "product_name": "Brushed Brass Pendant Light",
        "category": "lighting",
        "description": "Modern brass pendant with fluted glass shade.",
        "style_keywords": ["modern", "minimalist", "warm"],
        "color_keywords": ["brass", "gold"],
        "material_keywords": ["brass", "glass"],
        "finish_keywords": ["brushed", "matte"],
        "estimated_price_inr": "₹4,000–₹14,000",
        "search_keywords": ["brass pendant light india", "modern pendant lamp"],
        "confidence": 88,
    },
    {
        "product_name": "Bouclé Accent Chair",
        "category": "furniture",
        "description": "Curved lounge chair upholstered in cream bouclé fabric.",
        "style_keywords": ["contemporary", "cozy", "sculptural"],
        "color_keywords": ["cream", "beige"],
        "material_keywords": ["bouclé", "wood"],
        "finish_keywords": ["soft", "matte"],
        "estimated_price_inr": "₹18,000–₹45,000",
        "search_keywords": ["boucle accent chair india", "curved lounge chair"],
        "confidence": 85,
    },
    {
        "product_name": "Hand-tufted Wool Rug",
        "category": "textile-decor",
        "description": "Neutral sand-tone wool rug with subtle loop-pile texture.",
        "style_keywords": ["japandi", "neutral", "layered"],
        "color_keywords": ["sand", "ivory"],
        "material_keywords": ["wool"],
        "finish_keywords": ["hand-tufted"],
        "estimated_price_inr": "₹8,000–₹30,000",
        "search_keywords": ["wool rug india", "hand tufted rug"],
        "confidence": 82,
    },
    {
        "product_name": "Ceramic Vase Set",
        "category": "decor",
        "description": "Set of matte ceramic vases in warm neutral tones.",
        "style_keywords": ["organic", "minimalist"],
        "color_keywords": ["beige", "off-white"],
        "material_keywords": ["ceramic"],
        "finish_keywords": ["matte"],
        "estimated_price_inr": "₹1,500–₹4,500",
        "search_keywords": ["ceramic vase set india", "decorative vase"],
        "confidence": 78,
    },
    {
        "product_name": "Solid Wood Coffee Table",
        "category": "furniture",
        "description": "Low-profile coffee table in solid sheesham or teak wood.",
        "style_keywords": ["modern", "warm", "natural"],
        "color_keywords": ["walnut", "brown"],
        "material_keywords": ["sheesham", "teak"],
        "finish_keywords": ["oiled", "satin"],
        "estimated_price_inr": "₹9,000–₹28,000",
        "search_keywords": ["sheesham coffee table india", "wooden coffee table"],
        "confidence": 84,
    },
    {
        "product_name": "Framed Botanical Print",
        "category": "art",
        "description": "Neutral botanical wall art in a slim wood frame.",
        "style_keywords": ["minimalist", "calm"],
        "color_keywords": ["green", "sage"],
        "material_keywords": ["paper", "wood-frame"],
        "finish_keywords": ["matte"],
        "estimated_price_inr": "₹800–₹3,500",
        "search_keywords": ["botanical wall art india", "framed print"],
        "confidence": 74,
    },
]


def _validate_products_payload(data) -> dict:
    """Strict validation for products payload from the LLM."""
    if not isinstance(data, dict) or "products" not in data or not isinstance(data["products"], list):
        raise ValueError("payload missing 'products' array")
    raw = data["products"]
    if not (1 <= len(raw) <= 12):
        raise ValueError(f"products count {len(raw)} outside 1-12")
    cleaned = []
    for i, p in enumerate(raw):
        if not isinstance(p, dict):
            raise ValueError(f"product {i} not an object")
        name = p.get("product_name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"product {i} missing product_name")
        category = p.get("category")
        if category not in PRODUCT_CATEGORIES:
            category = "other"
        conf = p.get("confidence")
        try:
            conf_int = max(0, min(100, int(round(float(conf)))))
        except (TypeError, ValueError):
            conf_int = 60
        cleaned.append({
            "product_name": name.strip()[:120],
            "category": category,
            "description": _clean_optional_text(p.get("description"), max_len=180) or "",
            "style_keywords": _clean_optional_list(p.get("style_keywords"), item_max=40, max_items=6),
            "color_keywords": _clean_optional_list(p.get("color_keywords"), item_max=40, max_items=4),
            "material_keywords": _clean_optional_list(p.get("material_keywords"), item_max=40, max_items=4),
            "finish_keywords": _clean_optional_list(p.get("finish_keywords"), item_max=40, max_items=4),
            "estimated_price_inr": _clean_optional_text(p.get("estimated_price_inr"), max_len=60) or "",
            "search_keywords": _clean_optional_list(p.get("search_keywords"), item_max=100, max_items=5),
            "confidence": conf_int,
        })
    return {"products": cleaned}


async def _run_products_pipeline(project_id: str, user_id: str, ref_b64: str,
                                 region: str = DEFAULT_REGION) -> dict:
    """Detect products/fixtures. Uses real AI when enabled, else mock. Also
    enriches each product with an affiliate DB match (curated) and fallback
    search URLs.

    Returns {'products': [...], 'generated_at': ...}. Never raises — a failure
    inside is logged and returned as an empty products list."""
    products: list = []
    if ENABLE_REAL_PRODUCTS and EMERGENT_LLM_KEY:
        try:
            products = await _run_real_products(project_id, ref_b64)
        except Exception:
            logger.exception(f"real-products failed project={project_id}, falling back to mock")
            products = _mock_products(project_id)
    else:
        products = _mock_products(project_id)

    # Enrich each detected product with affiliate match + fallback search URLs.
    enriched = []
    for idx, p in enumerate(products):
        matched = await _match_product_to_affiliates(p)
        p_out = dict(p)
        p_out["id"] = f"product_{idx + 1}"
        p_out["matched_affiliate"] = matched
        p_out["search_urls"] = _build_search_urls(p)
        enriched.append(p_out)

    return {
        "products": enriched,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "region": region,
        "version": ("real-openai-" + LLM_MODEL_PRODUCTS + "-v1") if (ENABLE_REAL_PRODUCTS and EMERGENT_LLM_KEY) else "mock-products-v1",
    }


async def _run_real_products(project_id: str, ref_b64: str) -> list:
    """Single-call real-AI product detection. Returns validated products list."""
    import asyncio
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"products-{project_id}-{secrets.token_hex(4)}",
        system_message=PRODUCTS_SYSTEM_PROMPT,
    ).with_model("openai", LLM_MODEL_PRODUCTS)
    msg = UserMessage(text=PRODUCTS_USER_PROMPT,
                      file_contents=[ImageContent(image_base64=ref_b64)])
    raw = await asyncio.wait_for(chat.send_message(msg), timeout=LLM_PRODUCTS_TIMEOUT_S)
    parsed = _parse_json(raw)
    cleaned = _validate_products_payload(parsed)
    return cleaned["products"]


def _mock_products(project_id: str) -> list:
    """Deterministic 4-item mock product set per project_id."""
    seed = int(ObjectId(project_id).binary[-4:].hex(), 16)
    start = seed % len(MOCK_PRODUCTS_LIBRARY)
    return [dict(MOCK_PRODUCTS_LIBRARY[(start + i) % len(MOCK_PRODUCTS_LIBRARY)])
            for i in range(4)]


def _build_search_urls(product: dict) -> dict:
    """Return {'amazon_in': url, 'google': url} using the first search keyword."""
    from urllib.parse import quote_plus
    kws = product.get("search_keywords") or []
    q = kws[0] if kws else product.get("product_name", "")
    q = (q or "").strip()
    if not q:
        return {}
    # Ensure "india" appears once in the google query without duplicating.
    google_q = q if "india" in q.lower() else f"{q} india"
    return {
        "amazon_in": f"https://www.amazon.in/s?k={quote_plus(q)}",
        "google": f"https://www.google.com/search?tbm=shop&q={quote_plus(google_q)}",
    }


# ============================================================================
# Sprint 2: Affiliate Products database (admin-managed curated DB)
# ============================================================================
AFFILIATE_MATCH_MIN_SCORE = float(os.environ.get("AFFILIATE_MATCH_MIN_SCORE", "0.20"))

AFFILIATE_PLATFORMS = [
    "Pepperfry", "Urban Ladder", "IKEA India", "WoodenStreet",
    "Hafele India", "Amazon India", "Jaipur Rugs", "Fabindia", "Other",
]


class AffiliateProductCreate(BaseModel):
    product_name: str = Field(min_length=1, max_length=200)
    product_category: str  # one of PRODUCT_CATEGORIES
    style_keywords: List[str] = []
    color_keywords: List[str] = []
    material_keywords: List[str] = []
    finish_keywords: List[str] = []
    affiliate_url: str = Field(min_length=1)
    platform: str = "Other"
    product_image_url: Optional[str] = ""
    price_inr: Optional[str] = ""
    notes: Optional[str] = ""


class AffiliateProductUpdate(BaseModel):
    product_name: Optional[str] = None
    product_category: Optional[str] = None
    style_keywords: Optional[List[str]] = None
    color_keywords: Optional[List[str]] = None
    material_keywords: Optional[List[str]] = None
    finish_keywords: Optional[List[str]] = None
    affiliate_url: Optional[str] = None
    platform: Optional[str] = None
    product_image_url: Optional[str] = None
    price_inr: Optional[str] = None
    notes: Optional[str] = None


def _affiliate_to_dict(doc: dict) -> dict:
    """Normalise a stored affiliate document for API response."""
    if not doc:
        return doc
    out = dict(doc)
    out["id"] = str(out.pop("_id", ""))
    return out


def _sanitize_kw_list(v) -> list:
    if not isinstance(v, list):
        return []
    return [s.strip().lower() for s in v if isinstance(s, str) and s.strip()][:12]


@api_router.get("/admin/affiliates")
async def list_affiliates(admin: dict = Depends(require_admin)):
    cursor = db.affiliate_products.find({}).sort("created_at", -1)
    return [_affiliate_to_dict(d) async for d in cursor]


@api_router.post("/admin/affiliates")
async def create_affiliate(payload: AffiliateProductCreate, admin: dict = Depends(require_admin)):
    if payload.product_category not in PRODUCT_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"product_category must be one of {PRODUCT_CATEGORIES}")
    doc = {
        "product_name": payload.product_name.strip(),
        "product_category": payload.product_category,
        "style_keywords": _sanitize_kw_list(payload.style_keywords),
        "color_keywords": _sanitize_kw_list(payload.color_keywords),
        "material_keywords": _sanitize_kw_list(payload.material_keywords),
        "finish_keywords": _sanitize_kw_list(payload.finish_keywords),
        "affiliate_url": payload.affiliate_url.strip(),
        "platform": (payload.platform or "Other").strip(),
        "product_image_url": (payload.product_image_url or "").strip(),
        "price_inr": (payload.price_inr or "").strip(),
        "notes": (payload.notes or "").strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "created_by": admin["id"],
    }
    res = await db.affiliate_products.insert_one(doc)
    doc["id"] = str(res.inserted_id)
    doc.pop("_id", None)
    return doc


@api_router.get("/admin/affiliates/{aff_id}")
async def get_affiliate(aff_id: str, admin: dict = Depends(require_admin)):
    try:
        doc = await db.affiliate_products.find_one({"_id": ObjectId(aff_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    return _affiliate_to_dict(doc)


@api_router.put("/admin/affiliates/{aff_id}")
async def update_affiliate(aff_id: str, payload: AffiliateProductUpdate,
                           admin: dict = Depends(require_admin)):
    updates: dict = {}
    if payload.product_name is not None:
        updates["product_name"] = payload.product_name.strip()
    if payload.product_category is not None:
        if payload.product_category not in PRODUCT_CATEGORIES:
            raise HTTPException(status_code=400, detail="Invalid product_category")
        updates["product_category"] = payload.product_category
    for k in ("style_keywords", "color_keywords", "material_keywords", "finish_keywords"):
        val = getattr(payload, k)
        if val is not None:
            updates[k] = _sanitize_kw_list(val)
    for k in ("affiliate_url", "platform", "product_image_url", "price_inr", "notes"):
        val = getattr(payload, k)
        if val is not None:
            updates[k] = str(val).strip()
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        res = await db.affiliate_products.find_one_and_update(
            {"_id": ObjectId(aff_id)},
            {"$set": updates},
            return_document=True,
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")
    if not res:
        raise HTTPException(status_code=404, detail="Not found")
    return _affiliate_to_dict(res)


@api_router.delete("/admin/affiliates/{aff_id}")
async def delete_affiliate(aff_id: str, admin: dict = Depends(require_admin)):
    try:
        res = await db.affiliate_products.delete_one({"_id": ObjectId(aff_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}


# --- Keyword-similarity matching -------------------------------------------
def _tokenize_text(s: str) -> set:
    if not isinstance(s, str):
        return set()
    return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if len(t) > 1}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _kw_set(items) -> set:
    if not isinstance(items, list):
        return set()
    return {s.strip().lower() for s in items if isinstance(s, str) and s.strip()}


def _score_affiliate_match(product: dict, aff: dict) -> float:
    """Weighted Jaccard similarity between a detected product and an affiliate."""
    p_name = _tokenize_text(product.get("product_name", ""))
    a_name = _tokenize_text(aff.get("product_name", ""))
    p_style = _kw_set(product.get("style_keywords"))
    a_style = _kw_set(aff.get("style_keywords"))
    p_mat = _kw_set(product.get("material_keywords"))
    a_mat = _kw_set(aff.get("material_keywords"))
    p_color = _kw_set(product.get("color_keywords"))
    a_color = _kw_set(aff.get("color_keywords"))
    p_finish = _kw_set(product.get("finish_keywords"))
    a_finish = _kw_set(aff.get("finish_keywords"))
    return (
        0.30 * _jaccard(p_name, a_name)
        + 0.25 * _jaccard(p_style, a_style)
        + 0.20 * _jaccard(p_mat, a_mat)
        + 0.15 * _jaccard(p_color, a_color)
        + 0.10 * _jaccard(p_finish, a_finish)
    )


async def _match_product_to_affiliates(product: dict) -> Optional[dict]:
    """Find best affiliate DB match for a detected product. Returns match dict or None."""
    category = product.get("category")
    # Prefer same category, but also consider items with no/other category as fallback.
    query = {"product_category": category} if category else {}
    best = None
    best_score = 0.0
    async for aff in db.affiliate_products.find(query):
        score = _score_affiliate_match(product, aff)
        if score > best_score:
            best_score = score
            best = aff
    # Fallback: if no same-category match, try across ALL categories with a
    # stricter score bar so we don't cross-match wildly.
    if best is None or best_score < AFFILIATE_MATCH_MIN_SCORE:
        async for aff in db.affiliate_products.find({}):
            if aff.get("product_category") == category:
                continue
            score = _score_affiliate_match(product, aff) * 0.75  # penalise cross-category
            if score > best_score:
                best_score = score
                best = aff
    if best is None or best_score < AFFILIATE_MATCH_MIN_SCORE:
        return None
    return {
        "id": str(best["_id"]),
        "product_name": best.get("product_name"),
        "product_category": best.get("product_category"),
        "platform": best.get("platform"),
        "affiliate_url": best.get("affiliate_url"),
        "product_image_url": best.get("product_image_url") or "",
        "price_inr": best.get("price_inr") or "",
        "match_score": round(best_score, 3),
    }


@api_router.get("/projects/{project_id}/products")
async def get_project_products(project_id: str, user: dict = Depends(get_current_user)):
    """Return the last-run detected products (with affiliate matches) for a project.
    Returns 200 with empty list if none yet."""
    try:
        doc = await db.projects.find_one({"_id": ObjectId(project_id), "user_id": user["id"]})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project id")
    if not doc:
        raise HTTPException(status_code=404, detail="Project not found")
    pd = doc.get("products_detected") or {}
    return {"products": pd.get("products", []),
            "generated_at": pd.get("generated_at"),
            "version": pd.get("version")}




# ============================================================================
# Mock Catalogue Match (MVP — no real AI, no PDF parsing)
# ============================================================================
MOCK_PRODUCT_LIBRARY = [
    # wood
    {"name": "White Oak Plank — Natural Matte", "ref": "Havwoods HW-291", "category": "wood", "color": "#a07856"},
    {"name": "Engineered Walnut 190mm Wide", "ref": "Forbo Catalogue 2024 · p.42", "category": "wood", "color": "#7a5a40"},
    {"name": "European Oak — Smoked Brushed", "ref": "Bjelin BJ-118", "category": "wood", "color": "#8e6d4f"},
    {"name": "Reclaimed Pine — Heritage Plank", "ref": "Element7 EL-RP-220", "category": "wood", "color": "#b08864"},
    {"name": "Maple Stripe Parquet 22mm", "ref": "Junckers JU-MAPLE-22", "category": "wood", "color": "#c8a878"},
    {"name": "Vertical Slatted Oak Panel", "ref": "Naturewall NW-SO-12", "category": "wood", "color": "#9b7a55"},
    # stone
    {"name": "Honed Travertine 600x600", "ref": "Mandarin Stone TR-CR-60", "category": "stone", "color": "#d6c4a3"},
    {"name": "Crema Limestone Tile", "ref": "Lapicida LP-CRM-300", "category": "stone", "color": "#cabd9d"},
    {"name": "Calacatta Viola Marble Slab", "ref": "Salvatori SVT-CV-01", "category": "stone", "color": "#e6dccd"},
    {"name": "Sandblasted Sandstone Pavers", "ref": "Stone Federation SF-SBS-450", "category": "stone", "color": "#c8b58e"},
    {"name": "Pietra Serena — Honed", "ref": "Cotto d'Este CT-PS-HON", "category": "stone", "color": "#9a9388"},
    # fabric
    {"name": "Bouclé Cream Upholstery", "ref": "Romo Bouclé Z2003", "category": "fabric", "color": "#e8dcc6"},
    {"name": "Looped Linen Weave", "ref": "Kvadrat KV-LL-04", "category": "fabric", "color": "#d6c8ad"},
    {"name": "Wool Curl Performance Fabric", "ref": "Maharam MH-WC-117", "category": "fabric", "color": "#cebda0"},
    {"name": "Soft Velvet — Sand", "ref": "Dedar DD-VLV-S-08", "category": "fabric", "color": "#c4a780"},
    {"name": "Nubby Tweed Upholstery", "ref": "Designtex DTX-NB-422", "category": "fabric", "color": "#bba888"},
    # metal
    {"name": "Brushed Brass Wall Sconce", "ref": "Astro AST-1112", "category": "metal", "color": "#c8a464"},
    {"name": "Antique Bronze Hardware Set", "ref": "Joseph Giles JG-AB-122", "category": "metal", "color": "#9c7c4a"},
    {"name": "Satin Champagne Trim Profile", "ref": "Buster + Punch BP-SC-22", "category": "metal", "color": "#d6b87a"},
    {"name": "Patinated Copper Cladding", "ref": "TECU CU-PAT-1.5", "category": "metal", "color": "#b06d4a"},
    {"name": "Blackened Steel Frame", "ref": "Crittall CR-BK-60", "category": "metal", "color": "#3a3a3a"},
    # plaster
    {"name": "Bone White Lime Plaster", "ref": "Bauwerk BW-04", "category": "plaster", "color": "#ece4d5"},
    {"name": "Tadelakt Marrakech Cream", "ref": "Clayworks CW-TDL-12", "category": "plaster", "color": "#e0d3bd"},
    {"name": "Polished Plaster — Calce", "ref": "Marmorino MM-PC-44", "category": "plaster", "color": "#e6dccc"},
    {"name": "Microcement Wall Coating", "ref": "Topciment TC-MCM-08", "category": "plaster", "color": "#d8c9b0"},
    {"name": "Chalk Paint — Old White", "ref": "Annie Sloan AS-CP-OW", "category": "plaster", "color": "#ece5d3"},
    # rug
    {"name": "Hand-tufted Wool Rug — Sand", "ref": "Armadillo AM-09", "category": "rug", "color": "#cdbb95"},
    {"name": "Loop Pile Wool Carpet", "ref": "Brintons BR-LP-203", "category": "rug", "color": "#b8a679"},
    {"name": "Jute & Wool Blend Runner", "ref": "Floor Story FS-JW-90", "category": "rug", "color": "#b89870"},
    {"name": "Berber Wool Rug — Beni Ourain", "ref": "Beni Ourain BO-BR-200", "category": "rug", "color": "#e2d4ba"},
    {"name": "Vintage Persian — Muted", "ref": "Nazmiyal NAZ-VP-12", "category": "rug", "color": "#9c7b5c"},
]

MATCH_REASONS_LIBRARY = {
    "wood": [
        "Natural grain pattern aligns with the reference",
        "Warm tone matches the detected colour palette",
        "Matte-oiled finish replicates the reference sheen",
        "Plank width sits within typical Scandinavian/Japandi spec",
        "Sustainability rating is compatible with the brief",
        "Tonal value within ±5% of detected colour",
    ],
    "stone": [
        "Veining and pore pattern match the texture description",
        "Tone and warmth aligned with detected palette",
        "Honed finish matches the reference surface sheen",
        "Format size suits residential floor application",
        "Available in matching skirting and accent pieces",
    ],
    "fabric": [
        "Loop / pile structure matches the detected texture",
        "Colour tone within the target warm-neutral range",
        "Performance rating suits residential seating",
        "Hand feel category aligns with the design intent",
        "Available width supports custom upholstery dimensions",
    ],
    "metal": [
        "Brushed finish replicates the reference reflectivity",
        "Warm tone matches the detected accent colour",
        "Patina depth suits the design style",
        "Available in matching adjacent hardware family",
    ],
    "plaster": [
        "Soft chalky finish matches the detected surface",
        "Off-white tone within 2 LRV of the reference",
        "Texture mottling consistent with the inspiration",
        "Compatible base coats available for the substrate",
    ],
    "rug": [
        "Sand / ivory tone aligned with the palette",
        "Loop-pile texture matches the detected weave",
        "Wool blend suits the design style",
        "Available in size variants for the room scale",
    ],
}

DISQUALIFIER_LIBRARY = [
    "Minimum order quantity may exceed project scope.",
    "Lead time of 8–10 weeks; confirm against project timeline.",
    "Slight tonal variation possible between batches.",
    "Subject to availability — confirm with supplier before specifying.",
]


def _category_for(material: dict) -> str:
    parts = [
        material.get("material_type", ""),
        material.get("color", ""),
        material.get("texture", ""),
        material.get("finish", ""),
    ]
    parts += material.get("keywords", []) or []
    blob = " ".join(parts).lower()
    aliases = {
        "marble": "stone", "travertine": "stone", "limestone": "stone", "sandstone": "stone",
        "bouclé": "fabric", "boucle": "fabric", "velvet": "fabric", "linen": "fabric", "wool curl": "fabric",
        "brass": "metal", "bronze": "metal", "copper": "metal", "steel": "metal",
        "wool": "rug", "carpet": "rug", "rug": "rug",
        "plaster": "plaster", "paint": "plaster", "tadelakt": "plaster", "microcement": "plaster",
        "wood": "wood", "oak": "wood", "walnut": "wood", "pine": "wood", "maple": "wood",
        "stone": "stone",
    }
    for keyword, cat in aliases.items():
        if keyword in blob:
            return cat
    return "wood"


def _score_label(pct: int) -> str:
    if pct >= 90:
        return "Strong Match"
    if pct >= 75:
        return "Good Match"
    if pct >= 60:
        return "Partial Match"
    return "Low Match"


# ============================================================================
# Real Catalogue Match (OpenAI vision, batched scoring)
# ============================================================================
ENABLE_REAL_MATCH = os.environ.get("ENABLE_REAL_MATCH", "false").lower() == "true"
LLM_MODEL_MATCH = os.environ.get("LLM_MODEL_MATCH", "gpt-4o-mini")
MATCH_MAX_PRODUCT_IMAGES = int(os.environ.get("MATCH_MAX_PRODUCT_IMAGES", "20"))
MATCH_MAX_FILE_BYTES = int(os.environ.get("MATCH_MAX_FILE_BYTES", "5242880"))
MATCH_RESIZE_MAX_PX = int(os.environ.get("MATCH_RESIZE_MAX_PX", "1024"))
MATCH_BATCH_SIZE = int(os.environ.get("MATCH_BATCH_SIZE", "4"))
LLM_MATCH_TIMEOUT_S = int(os.environ.get("LLM_MATCH_TIMEOUT_S", "35"))
LLM_MATCH_MAX_RETRIES = int(os.environ.get("LLM_MATCH_MAX_RETRIES", "1"))
LLM_MATCH_CONCURRENCY = int(os.environ.get("LLM_MATCH_CONCURRENCY", "4"))
LLM_MATCH_DAILY_USER_BUDGET = int(os.environ.get("LLM_MATCH_DAILY_USER_BUDGET", "50"))
MATCH_DEDUP_WINDOW_S = int(os.environ.get("MATCH_DEDUP_WINDOW_S", "600"))
MATCH_MIN_THRESHOLD = int(os.environ.get("MATCH_MIN_THRESHOLD", "40"))

REASON_CATEGORIES = ["color", "texture", "finish", "material_family", "style"]
CANDIDATE_TYPES = ["product_material_candidate", "room_scene_or_lifestyle", "unclear"]

# Family compatibility for gating. Within each set, members are loosely interchangeable.
COMPATIBLE_FAMILIES = {
    "wood": {"wood", "flooring", "furniture"},
    "flooring": {"wood", "flooring", "stone"},
    "stone": {"stone", "flooring"},
    "metal": {"metal"},
    "upholstery": {"upholstery", "textile", "furniture"},
    "textile": {"upholstery", "textile"},
    "wall": {"wall", "ceiling"},
    "ceiling": {"wall", "ceiling"},
    "furniture": {"furniture", "wood", "upholstery"},
}
HARD_FAMILIES = {"flooring", "wood", "stone", "metal", "ceiling"}
SOFT_FAMILIES = {"upholstery", "textile"}

# Regex patterns that indicate the LLM judged the candidate to be a different family.
_WRONG_FAMILY_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bnot\s+(a\s+)?(wood|flooring|stone|tile|metal|fabric|upholstery|textile|leather)\b",
        r"\bdifferent\s+(material|category|family)\b",
        r"\bwrong\s+(material|category|family)\b",
        r"\bmaterial\s+is\s+(leather|fabric|stone|wood|metal)\s+,?\s*not\b",
        r"\broom\s+scene\b",
        r"\bnot\s+a\s+(product|swatch|sample|close[- ]up)\b",
        r"\bmultiple\s+(unrelated\s+)?materials\b",
    ]
]

MATCH_SYSTEM_PROMPT = (
    "You are a materials expert who compares interior-design reference specs to "
    "candidate product photos. Be calibrated and honest — do NOT inflate scores. "
    "Reply with ONLY a valid JSON object. No markdown fences, no prose outside JSON."
)

MATCH_BATCH_USER_PROMPT_TEMPLATE = (
    "Reference material spec (target):\n{spec_json}\n\n"
    "{manual_prompt_block}"
    "You will be shown {n} candidate images in order (indexed 0 to {n_minus_1}). "
    "These should be supplier-style product/material photos — swatches, samples, or "
    "close-ups of a single product. They should NOT be room scenes, moodboards, or "
    "inspiration photos.\n\n"
    "For each candidate, do TWO things:\n"
    "  1. Classify it as one of:\n"
    '     - "product_material_candidate"  (single product/material/swatch)\n'
    '     - "room_scene_or_lifestyle"     (full room, moodboard, multiple unrelated materials)\n'
    '     - "unclear"                     (ambiguous or low-quality)\n'
    "  2. Identify its material family, then score it against the reference along five "
    "dimensions: color, texture, finish, material_family, style. Then output a single "
    "integer match_percent (0–100).\n\n"
    "Scoring calibration — use these bands honestly:\n"
    "  • 82–92  Excellent match across most dimensions\n"
    "  • 68–81  Good match with minor differences\n"
    "  • 50–67  Partial match — some dimensions align, others don't\n"
    "  • below 50  Weak, wrong material family, or unsuitable substitute\n"
    "Do NOT exceed 92. Reserve 90+ only for truly excellent matches. Most products "
    "should land 60–85. If the candidate is a DIFFERENT material family from the reference "
    "(e.g. fabric vs wood, leather vs stone), score below 50 unless there is a clear "
    "design-substitute reason explained in the reasons.\n\n"
    "If candidate_type is 'room_scene_or_lifestyle', set match_percent ≤ 30 and explain in "
    "the disqualifier (e.g. 'Room scene, not a product photo').\n\n"
    "Return ONLY this JSON shape:\n"
    "{{\n"
    '  "batch_results": [\n'
    "    {{\n"
    '      "candidate_index": 0,\n'
    '      "candidate_type": "one of: ' + ", ".join(CANDIDATE_TYPES) + '",\n'
    '      "detected_family": "one of: ' + ", ".join(MATERIAL_FAMILIES) + '",\n'
    '      "match_percent": 75,\n'
    '      "reasons": [\n'
    '        {{"category": "color", "text": "short reason ≤ 80 chars"}},\n'
    '        {{"category": "texture", "text": "short reason ≤ 80 chars"}},\n'
    '        {{"category": "finish", "text": "short reason ≤ 80 chars"}}\n'
    "      ],\n"
    '      "disqualifier": null\n'
    "    }}\n"
    "  ]\n"
    "}}\n\n"
    "Rules:\n"
    "- batch_results must contain exactly {n} entries, one per candidate, indexed 0..{n_minus_1}.\n"
    "- candidate_type and detected_family MUST be from the listed enum values.\n"
    "- match_percent is an INTEGER 0–100. Never exceed 92.\n"
    "- Exactly 3 reasons per candidate — even for room scenes or unclear images "
    "(in those cases, the reasons can simply describe what you actually see, e.g. "
    "'Color: appears to be a full room scene, not a swatch'). Each category MUST be one "
    "of: " + ", ".join(REASON_CATEGORIES) + ".\n"
    "- Prefer covering different categories across the 3 reasons.\n"
    "- disqualifier: a short sentence (≤ 120 chars) when there's a real concern, else null.\n"
    "- Reply with ONLY the JSON object."
)

MATCH_RETRY_NUDGE = (
    "Your previous reply was not valid JSON for the requested schema. "
    "Reply with ONLY the JSON object. Ensure match_percent is integer 0–100 (≤92) "
    "and reasons[].category is one of the listed enum values."
)

INDIA_MATCH_BLOCK = (
    "\n\n" + INDIAN_BRAND_CONTEXT + "\n\n"
    "BECAUSE the user prefers India sourcing, add ONE extra field per candidate:\n"
    '  "indian_alternative": "short ≤ 120 char hint naming an Indian-market '
    'equivalent or category, e.g. \\"Comparable to Greenlam veneer in matt PU '
    'finish — widely stocked across Indian dealers\\""\n'
    "Populate this field for EVERY candidate that is a genuine "
    "product_material_candidate AND has match_percent ≥ 45 (most surviving "
    "candidates should have one). For room scenes, unclear images, or weak "
    "matches, set it to null. Use Indian terminology where natural — never "
    "invent brand-SKU pairs."
)


def _build_match_user_prompt(region: str,
                             spec_json: str,
                             manual_prompt_block: str,
                             n: int) -> str:
    """Compose the per-batch match user prompt, optionally adding India sourcing block."""
    base = MATCH_BATCH_USER_PROMPT_TEMPLATE.format(
        spec_json=spec_json,
        manual_prompt_block=manual_prompt_block,
        n=n,
        n_minus_1=n - 1,
    )
    if region == "India":
        return base + INDIA_MATCH_BLOCK
    return base


def _normalize_image_to_b64(content: bytes) -> str | None:
    """Resize/recompress an image to max edge MATCH_RESIZE_MAX_PX, JPEG q85. Returns base64 or None on failure."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(content))
        if im.mode != "RGB":
            im = im.convert("RGB")
        w, h = im.size
        if max(w, h) > MATCH_RESIZE_MAX_PX:
            scale = MATCH_RESIZE_MAX_PX / max(w, h)
            im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85, optimize=True)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception:
        logger.exception("normalize_image failed")
        return None


# ----------------------------------------------------------------------------
# PDF catalogue support — Phase 2 of Real Catalogue Match
# ----------------------------------------------------------------------------
MATCH_PDF_MAX_PAGES = int(os.environ.get("MATCH_PDF_MAX_PAGES", "8"))
MATCH_PDF_MAX_FILE_BYTES = int(os.environ.get("MATCH_PDF_MAX_FILE_BYTES", str(20 * 1024 * 1024)))  # 20 MiB
MATCH_PDF_RENDER_MAX_PX = int(os.environ.get("MATCH_PDF_RENDER_MAX_PX", "1024"))
MATCH_PDF_THUMB_MAX_PX = int(os.environ.get("MATCH_PDF_THUMB_MAX_PX", "260"))


def _render_pdf_pages_to_candidates(content: bytes, filename: str, warnings: list) -> list:
    """Render the first MATCH_PDF_MAX_PAGES of a PDF to JPEG-base64 candidates.

    Each candidate has {name, size, b64, page_number, thumb_b64}. Thumb is a
    smaller base64 JPEG for UI preview (kept separate so it never goes to the LLM).
    Returns [] if the PDF is unopenable; appends warnings for oversize / empty PDFs.
    """
    import fitz  # PyMuPDF
    from PIL import Image

    if len(content) > MATCH_PDF_MAX_FILE_BYTES:
        warnings.append(
            f"Skipped {filename}: PDF is larger than "
            f"{MATCH_PDF_MAX_FILE_BYTES // (1024 * 1024)} MiB limit."
        )
        return []
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        warnings.append(f"Could not open PDF {filename}: {type(e).__name__}")
        logger.warning(f"pdf-open-fail file={filename} err={e}")
        return []

    total_pages = doc.page_count
    if total_pages == 0:
        doc.close()
        warnings.append(f"Skipped {filename}: PDF has no pages.")
        return []

    pages_to_render = min(total_pages, MATCH_PDF_MAX_PAGES)
    if total_pages > MATCH_PDF_MAX_PAGES:
        warnings.append(
            f"{filename}: only the first {MATCH_PDF_MAX_PAGES} of "
            f"{total_pages} pages were analysed."
        )

    out = []
    for i in range(pages_to_render):
        try:
            page = doc.load_page(i)
            # Render @ 2x zoom, then downsize with PIL for consistent target width.
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            w, h = im.size
            if max(w, h) > MATCH_PDF_RENDER_MAX_PX:
                scale = MATCH_PDF_RENDER_MAX_PX / max(w, h)
                im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            # Full page b64 → LLM
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85, optimize=True)
            page_bytes = buf.getvalue()
            b64 = base64.b64encode(page_bytes).decode("utf-8")
            # Thumbnail (data URL) → UI only, never sent to LLM
            thumb = im.copy()
            tw, th = thumb.size
            if max(tw, th) > MATCH_PDF_THUMB_MAX_PX:
                s = MATCH_PDF_THUMB_MAX_PX / max(tw, th)
                thumb = thumb.resize((int(tw * s), int(th * s)), Image.LANCZOS)
            tbuf = io.BytesIO()
            thumb.save(tbuf, format="JPEG", quality=70, optimize=True)
            thumb_data_url = "data:image/jpeg;base64," + base64.b64encode(tbuf.getvalue()).decode("utf-8")
            out.append({
                "name": filename or "catalogue.pdf",
                "size": len(page_bytes),
                "b64": b64,
                "page_number": i + 1,
                "thumb_b64": thumb_data_url,
            })
        except Exception:  # noqa: BLE001
            logger.exception(f"pdf-render-fail file={filename} page={i + 1}")
            warnings.append(f"{filename}: could not render page {i + 1}, skipped.")
            continue
    doc.close()
    return out


# ---- Per-field validators (used by _validate_batch_result, kept small/testable) ----
def _coerce_candidate_index(raw_idx, raw_pos: int, expected_n: int, seen_idx: set) -> int:
    """Validate a candidate_index. Allow positional fallback when LLM forgets the index."""
    idx = raw_idx
    if not isinstance(idx, int) or not (0 <= idx < expected_n):
        if 0 <= raw_pos < expected_n and raw_pos not in seen_idx:
            idx = raw_pos
        else:
            raise ValueError(f"bad candidate_index {raw_idx}")
    if idx in seen_idx:
        raise ValueError(f"duplicate candidate_index {idx}")
    return idx


def _coerce_match_percent(raw) -> int:
    if isinstance(raw, bool):
        raise ValueError("match_percent is bool")
    try:
        return max(0, min(100, int(round(float(raw)))))
    except (TypeError, ValueError) as e:
        raise ValueError("match_percent not numeric") from e


def _coerce_detected_family(raw):
    if raw in MATERIAL_FAMILIES:
        return raw
    if "other" in MATERIAL_FAMILIES:
        return "other"
    raise ValueError("detected_family missing")


def _coerce_reasons(raw_reasons) -> list:
    """Pick up to 3 well-formed {category, text} dicts; silently drop the rest."""
    if not isinstance(raw_reasons, list):
        return []
    reasons: list = []
    for r in raw_reasons[:3]:
        if not isinstance(r, dict):
            continue
        cat = r.get("category")
        txt = r.get("text")
        if cat not in REASON_CATEGORIES:
            continue
        if not isinstance(txt, str) or not txt.strip():
            continue
        reasons.append({"category": cat, "text": txt.strip()[:120]})
    return reasons


def _coerce_disqualifier(raw):
    if not isinstance(raw, str) or not raw.strip():
        return None
    return raw.strip()[:160]


def _zero_fallback_entry(idx: int, msg: str = "Could not parse this candidate's score") -> dict:
    return {
        "candidate_index": idx,
        "candidate_type": "unclear",
        "detected_family": "other",
        "match_percent": 0,
        "reasons": [],
        "disqualifier": msg,
        "indian_alternative": None,
    }


def _validate_one_batch_item(it, raw_pos: int, expected_n: int, seen_idx: set) -> dict:
    """Validate a single LLM batch entry. Raises ValueError on unrecoverable issues.
    Returns a clean dict ready for aggregation."""
    if not isinstance(it, dict):
        raise ValueError("entry not object")
    idx = _coerce_candidate_index(it.get("candidate_index"), raw_pos, expected_n, seen_idx)
    cand_type = it.get("candidate_type")
    if cand_type not in CANDIDATE_TYPES:
        raise ValueError(f"candidate_type {cand_type!r} not in enum")
    det_fam = _coerce_detected_family(it.get("detected_family"))
    pct_int = _coerce_match_percent(it.get("match_percent"))
    reasons = _coerce_reasons(it.get("reasons"))
    # Genuine product candidates must come with ≥1 reason — otherwise we can't trust the score.
    if cand_type == "product_material_candidate" and not reasons:
        raise ValueError("product candidate has no usable reasons")
    return {
        "candidate_index": idx,
        "candidate_type": cand_type,
        "detected_family": det_fam,
        "match_percent": pct_int,
        "reasons": reasons,
        "disqualifier": _coerce_disqualifier(it.get("disqualifier")),
        "indian_alternative": _clean_optional_text(it.get("indian_alternative"), max_len=120),
    }


def _validate_batch_result(data, expected_n: int) -> list:
    """Validate a batched LLM response per-item. Returns a list of length expected_n.
    Each slot is a clean entry; slots the LLM mangled are filled with a zero-score
    fallback so the rest of the batch is not lost. Raises ValueError ONLY when the
    structural envelope is unusable or every single item fails."""
    if not isinstance(data, dict) or "batch_results" not in data:
        raise ValueError("missing 'batch_results'")
    items = data["batch_results"]
    if not isinstance(items, list):
        raise ValueError("batch_results not list")

    out: list = [None] * expected_n
    seen_idx: set = set()
    item_errors: list = []

    for raw_pos, it in enumerate(items):
        try:
            entry = _validate_one_batch_item(it, raw_pos, expected_n, seen_idx)
        except ValueError as ve:
            item_errors.append(f"item[{raw_pos}]={ve}")
            continue
        seen_idx.add(entry["candidate_index"])
        out[entry["candidate_index"]] = entry

    if all(x is None for x in out):
        raise ValueError(
            f"no valid entries in batch_results ({len(items)} raw items, errors: "
            f"{'; '.join(item_errors[:3])})"
        )

    # Fill mangled slots with a zero-score fallback so the caller can still emit them
    for i in range(expected_n):
        if out[i] is None:
            logger.info(f"match validator-fallback idx={i} (item error)")
            out[i] = _zero_fallback_entry(i)
    return out


def _format_reasons_for_storage(structured_reasons: list) -> list:
    """Convert [{category, text}, ...] → ['Color: text', ...] for backward-compatible frontend rendering."""
    label_map = {"color": "Color", "texture": "Texture", "finish": "Finish",
                 "material_family": "Family", "style": "Style"}
    return [f"{label_map.get(r['category'], r['category'].title())}: {r['text']}" for r in structured_reasons]


def _calibrate_percent(p: int) -> int:
    """Server-side anti-inflation clamp."""
    if p > 92:
        return 92
    if p < 0:
        return 0
    return p


def _enforce_family_gating(selected_family, detected_family, pct, reasons, disqualifier):
    """Apply trust-preserving family caps. Returns (adjusted_pct, gating_note or None)."""
    # 1. Wrong-family language in reasons or disqualifier → cap 39
    haystack_parts = [(disqualifier or "")]
    for r in reasons or []:
        if isinstance(r, dict):
            haystack_parts.append(r.get("text", ""))
        else:
            haystack_parts.append(str(r))
    haystack = " ".join(haystack_parts)
    if any(p.search(haystack) for p in _WRONG_FAMILY_PATTERNS):
        if pct > 39:
            return 39, "wrong-family language detected"

    sel = (selected_family or "").lower()
    det = (detected_family or "").lower()
    if not sel or not det or sel == det:
        return pct, None

    # 2. Hard-vs-soft mismatch → cap 35
    if sel in HARD_FAMILIES and det in SOFT_FAMILIES:
        if pct > 35:
            return 35, f"hard/soft mismatch: {sel} vs {det}"
    if sel in SOFT_FAMILIES and det in HARD_FAMILIES:
        if pct > 35:
            return 35, f"soft/hard mismatch: {sel} vs {det}"

    # 3. Compatible (e.g. wood ↔ flooring) → no cap
    compat = COMPATIBLE_FAMILIES.get(sel, {sel})
    if det in compat:
        return pct, None

    # 4. Any other different family → cap 39
    if pct > 39:
        return 39, f"different family: {sel} vs {det}"
    return pct, None


def _candidate_hash(items: list) -> str:
    """SHA-256 of sorted (name, size) tuples — used for the dedup window."""
    parts = sorted(f"{x['name']}|{x['size']}" for x in items)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


async def _score_one_batch(
    project_id: str,
    batch_idx: int,
    selected_spec_json: str,
    manual_prompt: str,
    candidate_b64s: list,
    region: str = DEFAULT_REGION,
) -> list:
    """Call the LLM once for a batch of up to MATCH_BATCH_SIZE candidates. Returns per-candidate result list (length = len(candidate_b64s))."""
    import asyncio
    n = len(candidate_b64s)
    manual_block = f"User preferences:\n{manual_prompt}\n\n" if manual_prompt else ""
    user_text = _build_match_user_prompt(region, selected_spec_json, manual_block, n)
    last_raw = ""
    last_err = ""
    for attempt in range(LLM_MATCH_MAX_RETRIES + 1):
        try:
            chat = LlmChat(
                api_key=EMERGENT_LLM_KEY,
                session_id=f"match-{project_id}-b{batch_idx}-{secrets.token_hex(3)}",
                system_message=MATCH_SYSTEM_PROMPT,
            ).with_model("openai", LLM_MODEL_MATCH)
            text = user_text if attempt == 0 else user_text + "\n\n" + MATCH_RETRY_NUDGE
            file_contents = [ImageContent(image_base64=b) for b in candidate_b64s]
            msg = UserMessage(text=text, file_contents=file_contents)
            raw = await asyncio.wait_for(chat.send_message(msg), timeout=LLM_MATCH_TIMEOUT_S)
            last_raw = raw
            parsed = _parse_json(raw)
            cleaned = _validate_batch_result(parsed, n)
            return cleaned
        except (ValueError, asyncio.TimeoutError) as e:
            last_err = str(e)
            logger.warning(
                f"match batch={batch_idx} attempt={attempt} project={project_id} "
                f"err={type(e).__name__}:{e} raw_excerpt={last_raw[:600]!r}"
            )
            continue
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            logger.exception(f"match batch={batch_idx} upstream-fail attempt={attempt}")
            await asyncio.sleep(0.5 * (3 ** attempt))
            continue
    # All retries exhausted — return zeroed entries so candidates are dropped below threshold
    logger.warning(f"match batch={batch_idx} all retries failed err={last_err}")
    return [{"candidate_index": i, "candidate_type": "unclear", "detected_family": "other", "match_percent": 0, "reasons": [
        {"category": "color", "text": "Could not score (LLM error)"},
        {"category": "texture", "text": "Could not score (LLM error)"},
        {"category": "finish", "text": "Could not score (LLM error)"},
    ], "disqualifier": "Scoring failed for this candidate.", "indian_alternative": None} for i in range(n)]


async def _prepare_match_candidates(catalogue_files: list, warnings: list) -> list:
    """Read, filter, size-cap and base64-normalize every uploaded catalogue file.

    Appends per-file warnings to `warnings`. Raises 400/422 when the final candidate
    set is empty or too large."""
    raw_candidates = []
    for f in catalogue_files:
        content = await f.read()
        if not content:
            continue
        mime = (f.content_type or "").lower()
        fname = f.filename or ""
        fname_lower = fname.lower()
        is_pdf = mime == "application/pdf" or fname_lower.endswith(".pdf")
        is_img = mime in ("image/jpeg", "image/png", "image/webp") or \
            fname_lower.endswith((".jpg", ".jpeg", ".png", ".webp"))

        if is_pdf:
            page_cands = _render_pdf_pages_to_candidates(content, fname or "catalogue.pdf", warnings)
            raw_candidates.extend(page_cands)
            continue

        if not is_img:
            warnings.append(f"Skipped unsupported file: {fname} ({mime or 'unknown MIME'})")
            continue
        if len(content) > MATCH_MAX_FILE_BYTES:
            warnings.append(f"Skipped oversized file (>5 MiB): {fname}")
            continue
        b64 = _normalize_image_to_b64(content)
        if not b64:
            warnings.append(f"Could not decode: {fname}")
            continue
        raw_candidates.append({"name": fname or "untitled",
                               "size": len(content), "b64": b64,
                               "page_number": None, "thumb_b64": None})

    if not raw_candidates:
        raise HTTPException(
            status_code=400,
            detail=(
                "No valid product images or catalogue PDFs provided. Upload JPEG/PNG/WEBP "
                "files under 5 MiB or a PDF catalogue (first "
                f"{MATCH_PDF_MAX_PAGES} pages will be analysed)."
            ),
        )
    if len(raw_candidates) > MATCH_MAX_PRODUCT_IMAGES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Too many candidates ({len(raw_candidates)}). Maximum "
                f"{MATCH_MAX_PRODUCT_IMAGES} per match. Try fewer images or a "
                "shorter catalogue PDF."
            ),
        )
    return raw_candidates


def _dedup_hit(existing_match: dict, cand_hash: str, project_id: str) -> dict | None:
    """Return the cached `existing_match` if same uploads + still-fresh, else None."""
    if not existing_match:
        return None
    if existing_match.get("candidate_hash") != cand_hash:
        return None
    if not existing_match.get("version", "").startswith("real-"):
        return None
    try:
        gen_at = datetime.fromisoformat(existing_match["generated_at"].replace("Z", "+00:00"))
    except (KeyError, ValueError, AttributeError):
        return None
    if (datetime.now(timezone.utc) - gen_at).total_seconds() >= MATCH_DEDUP_WINDOW_S:
        return None
    logger.info(f"match dedup-hit project={project_id}")
    return existing_match


def _apply_score_gating(r: dict, cand: dict, selected_family: str,
                       project_id: str) -> tuple[str, int] | None:
    """Compute the final gated match_percent for one batch result row.

    Returns (gated_pct as int, …) or None when the candidate should be dropped
    (room-scene rejection or below MATCH_MIN_THRESHOLD)."""
    ctype = r.get("candidate_type", "unclear")
    pct = _calibrate_percent(r["match_percent"])

    gated_pct, gate_note = _enforce_family_gating(
        selected_family, r.get("detected_family"), pct,
        r.get("reasons", []), r.get("disqualifier"),
    )
    if gate_note:
        logger.info(
            f"match gating project={project_id} cand={cand['name']} "
            f"pct {pct}→{gated_pct} reason={gate_note}"
        )
    pct = gated_pct

    # Unclear candidates: cap at 60 unless family clearly matches the selected
    if ctype == "unclear":
        det = (r.get("detected_family") or "").lower()
        family_ok = det and (
            det == selected_family
            or det in COMPATIBLE_FAMILIES.get(selected_family, {selected_family})
        )
        if not family_ok and pct > 60:
            logger.info(
                f"match unclear-cap project={project_id} cand={cand['name']} pct {pct}→60"
            )
            pct = 60

    if pct < MATCH_MIN_THRESHOLD:
        return None
    return ctype, pct


def _aggregate_batch_results(batches: list, batch_results: list,
                             selected_family: str, project_id: str,
                             warnings: list) -> list:
    """Walk every per-batch result, drop room-scenes, apply gating, keep survivors."""
    scored = []
    for batch, results in zip(batches, batch_results):
        for r in results:
            cand = batch[r["candidate_index"]]
            ctype = r.get("candidate_type", "unclear")

            if ctype == "room_scene_or_lifestyle":
                warnings.append(
                    f"Skipped {cand['name']}: image appears to be a room/lifestyle scene, "
                    "not a product/material candidate."
                )
                logger.info(
                    f"match skipped project={project_id} cand={cand['name']} reason=room_scene"
                )
                continue

            gating = _apply_score_gating(r, cand, selected_family, project_id)
            if gating is None:
                continue
            kept_ctype, pct = gating

            scored.append({
                "name": cand["name"],
                "size": cand["size"],
                "candidate_type": kept_ctype,
                "detected_family": r.get("detected_family"),
                "match_percent": pct,
                "reasons": r["reasons"],
                "disqualifier": r["disqualifier"],
                "indian_alternative": r.get("indian_alternative"),
                "page_number": cand.get("page_number"),
                "thumb_b64": cand.get("thumb_b64"),
            })
    return scored


def _shape_match_response(scored: list) -> list:
    """Top-5 deterministic ordering + final wire-format for the frontend."""
    scored.sort(key=lambda x: (-x["match_percent"], x["name"], (x.get("page_number") or 0)))
    top = scored[:5]
    matches = []
    for i, s in enumerate(top):
        stem = os.path.splitext(s["name"])[0].replace("_", " ").replace("-", " ").strip().title() or s["name"]
        page = s.get("page_number")
        # For PDF pages, product_name = "Catalogue Name — Page N"; for images it's just the stem.
        product_name = f"{stem} — Page {page}" if page else stem
        matches.append({
            "id": f"match_{i + 1}",
            "product_name": product_name,
            "catalogue_ref": s["name"],
            "page_number": page,
            "thumb_b64": s.get("thumb_b64"),
            "match_percent": s["match_percent"],
            "score_label": _score_label(s["match_percent"]),
            "reasons": _format_reasons_for_storage(s["reasons"]),
            "disqualifier": s["disqualifier"],
            "indian_alternative": s.get("indian_alternative"),
            "thumbnail_color": "#" + hashlib.sha256(f"{s['name']}#{page or 0}".encode()).hexdigest()[:6],
        })
    return matches


async def _run_real_match(
    project_id: str,
    user_id: str,
    selected: dict,
    manual_prompt: str,
    catalogue_files: list,
    existing_match: dict,
    region: str = DEFAULT_REGION,
) -> dict:
    """Phase-1 real catalogue match: uploaded product images only, batched scoring.

    Pipeline:
      1. `_prepare_match_candidates` — read + size-cap + normalize uploaded files
      2. `_dedup_hit` — cheap re-use of recent identical request
      3. batched LLM dispatch via `_score_one_batch`
      4. `_aggregate_batch_results` — drop room scenes, apply family gating
      5. `_shape_match_response` — Top-5 ordering + frontend wire format
    """
    import asyncio
    warnings: list = []

    raw_candidates = await _prepare_match_candidates(catalogue_files, warnings)
    cand_hash = _candidate_hash([
        {"name": c["name"], "size": c["size"], "page": c.get("page_number")}
        for c in raw_candidates
    ])

    hit = _dedup_hit(existing_match, cand_hash, project_id)
    if hit is not None:
        return hit

    selected_spec_json = json.dumps(
        {k: selected.get(k) for k in
         ("zone", "material_family", "material_type", "color",
          "texture", "finish", "design_style", "keywords")},
        ensure_ascii=False,
    )
    batches = [raw_candidates[i:i + MATCH_BATCH_SIZE]
               for i in range(0, len(raw_candidates), MATCH_BATCH_SIZE)]
    sem = asyncio.Semaphore(LLM_MATCH_CONCURRENCY)

    async def _do_batch(bi, batch):
        async with sem:
            return await _score_one_batch(
                project_id, bi, selected_spec_json, manual_prompt,
                [c["b64"] for c in batch], region=region,
            )

    batch_results = await asyncio.gather(*[_do_batch(bi, b) for bi, b in enumerate(batches)])

    selected_family = (selected.get("material_family") or "").lower()
    scored = _aggregate_batch_results(batches, batch_results, selected_family,
                                      project_id, warnings)

    if not scored:
        warnings.append(f"No products met the minimum {MATCH_MIN_THRESHOLD}% similarity bar.")
    elif len(scored) < 3:
        warnings.append(
            "Only limited relevant matches found. Upload more products from the "
            "same material category for better results."
        )

    matches = _shape_match_response(scored)

    return {
        "matches": matches,
        "warnings": warnings,
        "candidate_hash": cand_hash,
        "batch_count": len(batches),
        "candidate_count": len(raw_candidates),
        "version": f"real-openai-{LLM_MODEL_MATCH}-v1",
        "region": region,
    }


@api_router.post("/projects/{project_id}/match")
async def run_match(
    project_id: str,
    zone: str = Form(...),
    manual_prompt: str = Form(""),
    catalogue: List[UploadFile] = File(default=[]),
    user: dict = Depends(get_current_user),
):
    doc = await db.projects.find_one({"_id": ObjectId(project_id), "user_id": user["id"]})
    if not doc:
        raise HTTPException(status_code=404, detail="Project not found")

    rows = (doc.get("mock_analysis") or {}).get("rows", [])
    selected = next((r for r in rows if r.get("zone") == zone), None)
    if not selected:
        raise HTTPException(status_code=400, detail=f"Zone '{zone}' not found in analysis — analyse materials first")

    # ---------- Real catalogue match branch ----------
    if ENABLE_REAL_MATCH and EMERGENT_LLM_KEY and len(catalogue) > 0:
        # Quota check
        day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        quota_doc = await db.usage_counters.find_one_and_update(
            {"user_id": user["id"], "day": day_key},
            {"$inc": {"match_count": 1},
             "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
            upsert=True,
            return_document=True,
        )
        if (quota_doc or {}).get("match_count", 1) > LLM_MATCH_DAILY_USER_BUDGET:
            await db.usage_counters.update_one(
                {"user_id": user["id"], "day": day_key},
                {"$inc": {"match_count": -1}},
            )
            raise HTTPException(
                status_code=429,
                detail=f"Daily match quota exceeded ({LLM_MATCH_DAILY_USER_BUDGET} per day).",
            )

        existing_match = (doc.get("match_results") or {}).get(zone)
        started = datetime.now(timezone.utc)
        try:
            real_result = await _run_real_match(
                project_id, user["id"], selected, manual_prompt, catalogue, existing_match,
                region=user.get("preferred_region", DEFAULT_REGION),
            )
        except HTTPException:
            await db.usage_counters.update_one(
                {"user_id": user["id"], "day": day_key},
                {"$inc": {"match_count": -1}},
            )
            raise

        elapsed_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
        logger.info(
            f"ai_call provider=openai model={LLM_MODEL_MATCH} kind=match project={project_id} "
            f"user={user['id']} ms={elapsed_ms:.0f} matches={len(real_result['matches'])} "
            f"batches={real_result.get('batch_count')} candidates={real_result.get('candidate_count')}"
        )

        # Compose & persist (same schema as mock + extra fields)
        result = {
            "zone": zone,
            "selected_material": selected,
            "manual_prompt": manual_prompt,
            "uploaded_files": [{"name": c.filename, "type": c.content_type or "", "size": 0} for c in catalogue],
            "category": _category_for(selected),
            "matches": real_result["matches"],
            "warnings": real_result["warnings"],
            "candidate_hash": real_result["candidate_hash"],
            "candidate_count": real_result["candidate_count"],
            "batch_count": real_result["batch_count"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "version": real_result["version"],
            "region": real_result.get("region"),
        }
        await db.projects.update_one(
            {"_id": ObjectId(project_id)},
            {"$set": {
                f"match_results.{zone}": result,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }}
        )
        return result
    # ---------- Fallback: existing mock-match path ----------

    # Collect filename / type / size only — no actual parsing or byte storage (mock mode)
    uploaded_files = []
    for f in catalogue:
        content = await f.read()
        uploaded_files.append({
            "name": f.filename or "untitled",
            "type": f.content_type or "application/octet-stream",
            "size": len(content),
        })

    # Deterministic mock: same project + zone always yields same 5 matches
    # Using sha256 (truncated to 8 hex chars) as a non-cryptographic seed
    seed_int = int(hashlib.sha256(f"{project_id}-{zone}".encode()).hexdigest()[:8], 16)
    category = _category_for(selected)

    candidates = [p for p in MOCK_PRODUCT_LIBRARY if p["category"] == category]
    if len(candidates) < 5:
        candidates += [p for p in MOCK_PRODUCT_LIBRARY if p["category"] != category]

    start = seed_int % len(candidates)
    chosen = [candidates[(start + i) % len(candidates)] for i in range(5)]

    base_pcts = [92, 86, 79, 71, 63]
    reason_pool = MATCH_REASONS_LIBRARY.get(category, MATCH_REASONS_LIBRARY["wood"])
    region = user.get("preferred_region", DEFAULT_REGION)
    india_hint_by_cat = {
        "wood":    "Comparable Indian alternative: teak / sheesham veneer + PU matt (Greenlam / Century).",
        "stone":   "Comparable Indian alternative: Kota stone honed or Indian Statuario from regional suppliers.",
        "fabric":  "Comparable Indian alternative: D'Decor / Sarom upholstery in similar weave.",
        "metal":   "Comparable Indian alternative: brushed brass profile via Hafele India or Hettich India.",
        "plaster": "Comparable Indian alternative: Asian Paints Royale Aspira or Berger Silk Velvet finish.",
        "rug":     "Comparable Indian alternative: Jaipur Rugs or Obeetee hand-tufted wool.",
    }
    matches = []
    for i, product in enumerate(chosen):
        jitter = ((seed_int >> (i * 3)) & 0x7) - 3  # ±3
        pct = max(50, min(98, base_pcts[i] + jitter))
        r_start = (seed_int + i * 7) % len(reason_pool)
        reasons = [reason_pool[(r_start + j) % len(reason_pool)] for j in range(3)]
        disqualifier = DISQUALIFIER_LIBRARY[(seed_int + i) % len(DISQUALIFIER_LIBRARY)] if i >= 3 else None
        matches.append({
            "id": f"match_{i + 1}",
            "product_name": product["name"],
            "catalogue_ref": product["ref"],
            "match_percent": pct,
            "score_label": _score_label(pct),
            "reasons": reasons,
            "disqualifier": disqualifier,
            "indian_alternative": india_hint_by_cat.get(category) if region == "India" and pct >= 65 else None,
            "thumbnail_color": product["color"],
        })

    result = {
        "zone": zone,
        "selected_material": selected,
        "manual_prompt": manual_prompt,
        "uploaded_files": uploaded_files,
        "category": category,
        "matches": matches,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": "mock-match-v1",
        "region": region,
    }

    await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$set": {
            f"match_results.{zone}": result,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }}
    )
    return result







# ============================================================================
# Reports
# ============================================================================
@api_router.get("/reports")
async def list_reports(user: dict = Depends(get_current_user)):
    cursor = db.reports.find({"user_id": user["id"]}).sort("created_at", -1).limit(20)
    items = []
    async for d in cursor:
        items.append(to_str_id(d))
    return items


# ============================================================================
# Health & client config
# ============================================================================
@api_router.get("/")
async def root():
    return {"app": "MaterialMatch AI", "status": "ok"}


@api_router.get("/health")
async def health():
    """Lightweight unauthenticated probe used by uptime checks / deploy smoke."""
    return {"status": "ok", "app": "MaterialMatch AI"}


@api_router.get("/config")
async def get_client_config():
    """Public, no-auth config flags the frontend uses to switch UI copy."""
    return {
        "enable_real_analysis": ENABLE_REAL_ANALYSIS and bool(EMERGENT_LLM_KEY),
        "enable_real_match": ENABLE_REAL_MATCH and bool(EMERGENT_LLM_KEY),
        "real_analysis_model": LLM_MODEL_ANALYSIS if ENABLE_REAL_ANALYSIS else None,
        "real_match_model": LLM_MODEL_MATCH if ENABLE_REAL_MATCH else None,
        "supported_regions": SUPPORTED_REGIONS,
        "default_region": DEFAULT_REGION,
    }


# ============================================================================
# Startup
# ============================================================================
@app.on_event("startup")
async def startup_event():
    try:
        await db.users.create_index("email", unique=True)
        await db.projects.create_index([("user_id", 1), ("created_at", -1)])
        await db.reports.create_index([("user_id", 1), ("created_at", -1)])
        await db.usage_counters.create_index([("user_id", 1), ("day", 1)], unique=True)
        # auto-expire counters after 32 days
        await db.usage_counters.create_index("created_at", expireAfterSeconds=32 * 86400)
        await db.affiliate_products.create_index("product_category")
        await db.affiliate_products.create_index("product_name")
    except Exception:
        logger.exception("Index creation failed")

    # Seed admin
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@materialmatch.ai")
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
    existing = await db.users.find_one({"email": admin_email})
    if not existing:
        await db.users.insert_one({
            "email": admin_email,
            "password_hash": hash_password(admin_password),
            "name": "Admin",
            "role": "admin",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"Seeded admin: {admin_email}")
    elif not verify_password(admin_password, existing["password_hash"]):
        await db.users.update_one(
            {"email": admin_email},
            {"$set": {"password_hash": hash_password(admin_password)}}
        )

    # Sprint 2: seed a small set of curated Indian affiliate products so the
    # matching flow is demoable out of the box. Idempotent (skips if collection
    # already has any docs).
    try:
        existing_count = await db.affiliate_products.count_documents({})
        if existing_count == 0:
            await db.affiliate_products.insert_many(_seed_affiliate_products())
            logger.info(f"Seeded {len(_seed_affiliate_products())} affiliate products")
    except Exception:
        logger.exception("Affiliate seed failed")


def _seed_affiliate_products() -> list:
    """Return a list of curated Indian affiliate products for initial demo.
    Every entry uses Indian platforms only (Pepperfry, Urban Ladder, IKEA India,
    WoodenStreet, Hafele India, Amazon India, Jaipur Rugs)."""
    now = datetime.now(timezone.utc).isoformat()
    entries = [
        {
            "product_name": "Brass Pendant Light – Fluted Glass Shade",
            "product_category": "lighting",
            "style_keywords": ["modern", "minimalist", "warm", "pendant"],
            "color_keywords": ["brass", "gold", "amber"],
            "material_keywords": ["brass", "glass"],
            "finish_keywords": ["brushed", "matte"],
            "affiliate_url": "https://www.pepperfry.com/product/pendant-light-brass",
            "platform": "Pepperfry",
            "product_image_url": "https://images.unsplash.com/photo-1524634126442-357e0eac3c14?w=600",
            "price_inr": "₹5,499",
            "notes": "Brass finish pendant light with fluted glass, popular Indian modern spec.",
        },
        {
            "product_name": "Boucle Accent Lounge Chair – Cream",
            "product_category": "furniture",
            "style_keywords": ["contemporary", "cozy", "sculptural", "curved"],
            "color_keywords": ["cream", "beige", "ivory"],
            "material_keywords": ["boucle", "fabric", "wood"],
            "finish_keywords": ["soft", "matte"],
            "affiliate_url": "https://www.urbanladder.com/products/boucle-lounge-chair",
            "platform": "Urban Ladder",
            "product_image_url": "https://images.unsplash.com/photo-1592078615290-033ee584e267?w=600",
            "price_inr": "₹27,999",
            "notes": "Curved lounge chair with textured bouclé upholstery.",
        },
        {
            "product_name": "Sheesham Wood Coffee Table – Rectangular",
            "product_category": "furniture",
            "style_keywords": ["modern", "natural", "warm"],
            "color_keywords": ["walnut", "brown", "honey"],
            "material_keywords": ["sheesham", "wood"],
            "finish_keywords": ["oiled", "satin"],
            "affiliate_url": "https://www.woodenstreet.com/coffee-tables/sheesham",
            "platform": "WoodenStreet",
            "product_image_url": "https://images.unsplash.com/photo-1594026112284-02bb6f3352fe?w=600",
            "price_inr": "₹14,499",
            "notes": "Solid sheesham (Indian rosewood) coffee table.",
        },
        {
            "product_name": "Hand-Tufted Wool Area Rug – Sand",
            "product_category": "textile-decor",
            "style_keywords": ["japandi", "neutral", "layered", "natural"],
            "color_keywords": ["sand", "ivory", "beige"],
            "material_keywords": ["wool"],
            "finish_keywords": ["hand-tufted", "loop-pile"],
            "affiliate_url": "https://www.jaipurrugs.com/rugs/hand-tufted-wool-sand",
            "platform": "Jaipur Rugs",
            "product_image_url": "https://images.unsplash.com/photo-1600166898405-da9535204843?w=600",
            "price_inr": "₹18,900",
            "notes": "Neutral hand-tufted wool rug from Jaipur Rugs.",
        },
        {
            "product_name": "Terracotta Ceramic Vase Set of 3",
            "product_category": "decor",
            "style_keywords": ["organic", "minimalist", "earthy"],
            "color_keywords": ["beige", "terracotta", "off-white"],
            "material_keywords": ["ceramic"],
            "finish_keywords": ["matte"],
            "affiliate_url": "https://www.amazon.in/s?k=terracotta+vase+set",
            "platform": "Amazon India",
            "product_image_url": "https://images.unsplash.com/photo-1578500494198-246f612d3b3d?w=600",
            "price_inr": "₹2,199",
            "notes": "Matte terracotta vase set — organic modern look.",
        },
        {
            "product_name": "Framed Botanical Wall Art – Set of 2",
            "product_category": "art",
            "style_keywords": ["minimalist", "calm", "natural"],
            "color_keywords": ["green", "sage", "beige"],
            "material_keywords": ["paper", "wood-frame", "glass"],
            "finish_keywords": ["matte"],
            "affiliate_url": "https://www.pepperfry.com/product/framed-botanical-print",
            "platform": "Pepperfry",
            "product_image_url": "https://images.unsplash.com/photo-1513519245088-0e12902e5a38?w=600",
            "price_inr": "₹1,899",
            "notes": "Set of two botanical prints in slim wood frames.",
        },
        {
            "product_name": "IKEA STOCKHOLM Table Lamp – Brass & Linen",
            "product_category": "lighting",
            "style_keywords": ["mid-century", "warm", "elegant"],
            "color_keywords": ["brass", "off-white"],
            "material_keywords": ["brass", "linen"],
            "finish_keywords": ["brushed"],
            "affiliate_url": "https://www.ikea.com/in/en/p/stockholm-table-lamp",
            "platform": "IKEA India",
            "product_image_url": "https://images.unsplash.com/photo-1507473885765-e6ed057f782c?w=600",
            "price_inr": "₹6,990",
            "notes": "Table lamp with brass base and linen shade.",
        },
        {
            "product_name": "Hafele Brushed Brass Cabinet Handle",
            "product_category": "fixture",
            "style_keywords": ["modern", "warm", "premium"],
            "color_keywords": ["brass", "gold"],
            "material_keywords": ["brass"],
            "finish_keywords": ["brushed", "satin"],
            "affiliate_url": "https://www.hafeleindia.com/handles/brushed-brass",
            "platform": "Hafele India",
            "product_image_url": "https://images.unsplash.com/photo-1615529182904-14819c35db37?w=600",
            "price_inr": "₹399",
            "notes": "Brushed brass cabinet handle from Hafele India range.",
        },
        {
            "product_name": "Fiddle Leaf Fig Plant with Ceramic Planter",
            "product_category": "plant-planter",
            "style_keywords": ["organic", "modern", "biophilic"],
            "color_keywords": ["green", "off-white"],
            "material_keywords": ["ceramic", "plant"],
            "finish_keywords": ["matte"],
            "affiliate_url": "https://www.amazon.in/s?k=fiddle+leaf+fig+plant+planter",
            "platform": "Amazon India",
            "product_image_url": "https://images.unsplash.com/photo-1509423350716-97f9360b4e09?w=600",
            "price_inr": "₹1,499",
            "notes": "Live fiddle-leaf fig with matte ceramic planter.",
        },
        {
            "product_name": "Cotton Linen Cushion Cover Set of 5",
            "product_category": "textile-decor",
            "style_keywords": ["cozy", "layered", "neutral"],
            "color_keywords": ["beige", "off-white", "sand"],
            "material_keywords": ["cotton", "linen"],
            "finish_keywords": ["woven"],
            "affiliate_url": "https://www.pepperfry.com/product/cushion-cover-cotton-linen",
            "platform": "Pepperfry",
            "product_image_url": "https://images.unsplash.com/photo-1584100936595-c0654b55a2e2?w=600",
            "price_inr": "₹899",
            "notes": "Cotton-linen cushion covers in warm neutral tones.",
        },
    ]
    for e in entries:
        e["created_at"] = now
        e["updated_at"] = now
    return entries


@app.on_event("shutdown")
async def shutdown_event():
    client.close()


# Mount the router
app.include_router(api_router)
# (CORS middleware was registered earlier — before any routes — so OPTIONS
# preflights are answered without hitting a handler.)
