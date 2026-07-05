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
import uuid
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

# Sprint 2 Revision (Catalogue-First): seeded global catalogue used by the
# region-analysis pipeline to return "closest catalogue matches" instead of
# just AI descriptions.
from catalogue_seed import SEEDED_CATALOGUE, CATEGORY_SETS, ALTERNATIVE_SYSTEMS

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
    _enrich_rows_with_catalogue(mock_analysis["rows"])

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
        _enrich_rows_with_catalogue(analysis.get("rows") or [])
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


class RegionAnalyzePayload(BaseModel):
    crop_b64: str = Field(min_length=32)  # base64 (no data-url prefix)
    note: Optional[str] = ""


# ---------------------------------------------------------------------------
# Sprint 2 Revision — Catalogue matcher, classifier and enrichment helpers.
# ---------------------------------------------------------------------------
_MATERIAL_FAMILIES_SET = {"Wood", "Stone", "Tile", "Fabric", "Paint", "Laminate",
                          "Veneer", "Metal", "Textile", "Ceramic"}
_PRODUCT_FAMILIES_SET = {"Lighting", "Furniture"}
_FIXTURE_KEYWORDS = {"faucet", "sink", "shower", "basin", "tap", "toilet",
                     "hinge", "handle", "profile", "trim", "downlight"}
_DECOR_KEYWORDS = {"vase", "ornament", "sculpture", "planter", "art",
                   "cushion", "throw", "candle"}


def _tokenize(v) -> set:
    """Tokenise a string or list into a lowercase word set for Jaccard."""
    if v is None:
        return set()
    if isinstance(v, (list, tuple)):
        raw = " ".join(str(x) for x in v)
    else:
        raw = str(v)
    return {w for w in re.split(r"[^a-z0-9]+", raw.lower()) if len(w) > 2}


def _hex_to_rgb(h: str) -> tuple:
    h = (h or "").lstrip("#")
    if len(h) != 6:
        return (128, 128, 128)
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return (128, 128, 128)


# Named-colour cheat-sheet used only when the detected row exposes a colour name
# but no hex.  Keeps the matcher deterministic in mock mode.
_COLOR_NAME_TO_HEX = {
    "white": "#F5F1EA", "warm white": "#F3EED9", "ivory": "#EEE1CB",
    "warm ivory": "#EFE4CB", "cream": "#F1E4C6", "warm cream": "#EDE0BF",
    "beige": "#D6C4A2", "warm beige": "#D6C4A2", "sand": "#D9C29C",
    "warm sand": "#D9C29C", "peach": "#F2D6BB", "peach cream": "#F2D9BE",
    "warm oak": "#B58453", "oak": "#BC8B54", "honey": "#B98A5A",
    "walnut": "#6E4A2E", "warm walnut": "#664021", "dark walnut": "#4B2E1A",
    "teak": "#A0703A", "brass": "#B18C4D", "warm brass": "#B58C4D",
    "gold": "#C79C5F", "rose gold": "#C6896D", "charcoal": "#3B3B3B",
    "warm grey": "#B7ADA0", "warm gray": "#B7ADA0", "sage": "#BFC9B3",
    "olive": "#8F8B65", "terracotta": "#B37050", "dusty rose": "#D4A99B",
    "black": "#111111", "rust": "#A9552F",
}


def _resolve_hex(row) -> str:
    """Pick the best hex for the row's colour, falling back to name lookup."""
    if isinstance(row, dict):
        h = row.get("color_hex")
        if h:
            return h
        name = str(row.get("color") or row.get("color_name") or "").lower().strip()
        if name in _COLOR_NAME_TO_HEX:
            return _COLOR_NAME_TO_HEX[name]
        # Try longest keyword match.
        for k in sorted(_COLOR_NAME_TO_HEX, key=len, reverse=True):
            if k in name:
                return _COLOR_NAME_TO_HEX[k]
    return "#B7ADA0"


def _color_similarity(hex1: str, hex2: str) -> int:
    """0-100 similarity based on RGB Euclidean distance."""
    r1, g1, b1 = _hex_to_rgb(hex1)
    r2, g2, b2 = _hex_to_rgb(hex2)
    d = ((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) ** 0.5
    # Max Euclidean distance in RGB space ≈ 441.67
    sim = max(0.0, 1.0 - (d / 441.67))
    return int(round(sim * 100))


# Family-alias table so a row tagged "wood" still boosts the "Veneer" and
# "Laminate" catalogues that visually mimic wood.
_FAMILY_ALIAS = {
    "wood": {"Wood", "Laminate"},
    "veneer": {"Wood", "Laminate"},
    "laminate": {"Laminate", "Wood"},
    "paint": {"Paint"},
    "stone": {"Stone", "Tile"},
    "marble": {"Stone", "Tile"},
    "tile": {"Tile", "Stone"},
    "fabric": {"Fabric"},
    "textile": {"Fabric"},
    "upholstery": {"Fabric"},
    "metal": {"Metal"},
    "lighting": {"Lighting"},
    "furniture": {"Furniture"},
}


def _score_catalogue_item(row: dict, item: dict, weights: dict | None = None) -> dict:
    """Return breakdown {overall, visual, color, finish, texture, family}.

    Sprint 4 — `weights` selects the Brain's category-specific ranking
    profile (paint = colour-heavy, tile/stone = pattern-heavy, fabric =
    texture-heavy). Falls back to a balanced default."""
    w = weights or _RANKING_WEIGHTS["_default"]
    fam_row = str(row.get("material_family") or "").lower()
    fam_item = str(item.get("material_family") or "")
    # Sprint 8.2 — when the AI-provided family is one we recognise in the
    # alias table we score family alignment 100 / 20. When it isn't (e.g. AI
    # returned an application word like "wall"), fall back to a neutral 60 so
    # a correctly-categorised item isn't unfairly penalised on a semantic
    # miss — the Brain's category filter has already vetted category fit.
    if fam_row in _FAMILY_ALIAS:
        family_match = fam_item in _FAMILY_ALIAS.get(fam_row, {fam_item})
        family_score = 100 if family_match else 20
    else:
        # Unrecognised family label (e.g. AI returned "wall" instead of
        # "Paint"). Score neutrally-high so a correctly-categorised item
        # can still reach the "best" tier (≥ 80) on strong colour + keyword
        # evidence — but don't award full 100.
        family_match = False
        family_score = 75

    # Keyword Jaccard visual similarity.
    row_tokens = (_tokenize(row.get("material_type")) | _tokenize(row.get("color"))
                  | _tokenize(row.get("texture")) | _tokenize(row.get("finish"))
                  | _tokenize(row.get("keywords")))
    item_tokens = (_tokenize(item.get("material_name")) | _tokenize(item.get("color_name"))
                   | _tokenize(item.get("texture")) | _tokenize(item.get("finish"))
                   | _tokenize(item.get("keywords")))
    if row_tokens and item_tokens:
        j = len(row_tokens & item_tokens) / max(1, len(row_tokens | item_tokens))
    else:
        j = 0.0
    visual = int(round(j * 100))

    # Colour similarity.
    row_hex = _resolve_hex(row)
    item_hex = item.get("color_hex") or "#B7ADA0"
    color = _color_similarity(row_hex, item_hex)

    # Finish similarity — token overlap. When one side has no finish data at
    # all we award a minor 25 baseline (Sprint 8.2: down from 40 to stop empty
    # metadata inflating scores past the min_overall gate).
    row_finish = _tokenize(row.get("finish"))
    item_finish = _tokenize(item.get("finish"))
    if row_finish and item_finish:
        finish = int(round(100 * len(row_finish & item_finish) / max(1, len(row_finish | item_finish))))
    else:
        finish = 25

    # Texture / grain similarity — same rule as finish.
    row_texture = _tokenize(row.get("texture"))
    item_texture = _tokenize(item.get("texture"))
    if row_texture and item_texture:
        texture = int(round(100 * len(row_texture & item_texture) / max(1, len(row_texture | item_texture))))
    else:
        texture = 25

    # Weighted composite from Brain profile. Cap at 97 — we never claim exact certainty.
    overall = (
        family_score * w.get("family", 0.20)
        + visual * w.get("visual", 0.25)
        + color * w.get("color", 0.25)
        + finish * w.get("finish", 0.15)
        + texture * w.get("texture", 0.15)
    )
    overall = min(97, int(round(overall)))
    return {
        "overall": overall,
        "visual": visual,
        "color": color,
        "finish": finish,
        "texture": texture,
        "family_match": family_match,
    }


def _find_catalogue_matches(row: dict, top_k: int = 8, min_overall: int = 62,
                              allowed_categories: list | None = None,
                              weights: dict | None = None) -> list:
    """Return top_k catalogue matches with similarity breakdown.

    Sprint 8.2 — quality tightened: min_overall raised to 62 and the
    family_match bypass removed so low-scoring items can no longer slip
    through just because they share a broad family. When no item clears the
    bar we return `[]` and the UI shows "No high-confidence catalogue match
    found."""
    scored = []
    allow = set(allowed_categories) if allowed_categories is not None else None
    if allow is not None and not allow:
        return []
    # Studio (uploaded PDF) records are searched first — they represent
    # user-owned real catalogue data and take priority over the seeded fallback.
    all_items = list(_STUDIO_INDEXED_RECORDS) + list(SEEDED_CATALOGUE)
    for item in all_items:
        if allow is not None and item.get("category") not in allow:
            continue
        s = _score_catalogue_item(row, item, weights=weights)
        if s["overall"] < min_overall:
            continue
        scored.append((s, item))
    scored.sort(key=lambda t: t[0]["overall"], reverse=True)
    top = scored[:top_k]
    out = []
    for s, item in top:
        code = item.get("material_code")
        page = item.get("page_number")
        out.append({
            "id": item["id"],
            "brand": item["brand"],
            "catalogue": item["catalogue"],
            "material_name": item["material_name"],
            "material_code": code,
            "material_code_display": code if code else "Code unavailable in current database",
            "page_number": page,
            "page_display": f"p.{page}" if page else "Page unavailable",
            "material_family": item["material_family"],
            "category": item["category"],
            "finish": item["finish"],
            "color_name": item.get("color_name"),
            "color_hex": item.get("color_hex"),
            "texture": item.get("texture"),
            "source": item.get("source") or "MaterialMatch Library",
            "match_percent": s["overall"],
            "similarity": {
                "visual": s["visual"],
                "color": s["color"],
                "finish": s["finish"],
                "texture": s["texture"],
            },
        })
    return out


def _classify_row(row: dict) -> str:
    """Classify a detected zone as Material Surface / Product / Fixture / Decor
    / Mixed / Unclear based on material_family + keyword hints."""
    fam = str(row.get("material_family") or "").strip().title()
    zone = str(row.get("zone") or "").lower()
    mtype = str(row.get("material_type") or "").lower()
    text_bag = f"{zone} {mtype}"
    if fam in _PRODUCT_FAMILIES_SET:
        return "Product"
    if fam in _MATERIAL_FAMILIES_SET or fam.lower() in _FAMILY_ALIAS:
        # Fixtures are usually still tagged Metal — check keywords.
        if any(k in text_bag for k in _FIXTURE_KEYWORDS):
            return "Fixture"
        return "Material Surface"
    if any(k in text_bag for k in _FIXTURE_KEYWORDS):
        return "Fixture"
    if any(k in text_bag for k in _DECOR_KEYWORDS):
        return "Decor"
    if fam:
        return "Material Surface"
    return "Unclear"


def _alternative_systems_for(row: dict) -> list:
    """Return category-level alternative material systems for a family.
    Matches case-insensitively and uses aliases so a family like `flooring`
    or `wood` still resolves to the Wood systems list.

    Sprint 2 Refinement — if the zone name implies an *application*
    (headboard wall, floor, countertop, curtain…) we prepend the priors so
    the UI's "Likely systems" list reads as an architect would think about
    the surface, not just its raw family."""
    fam_raw = str(row.get("material_family") or "").strip()
    fam_l = fam_raw.lower()
    zone = str(row.get("zone") or "")
    prior_names = _application_priors_for_zone(zone)
    prior_entries = [{"name": n, "why": "Likely system for this application"} for n in prior_names]

    family_entries: list = []
    if fam_raw:
        # Direct hit (either case).
        for key in ALTERNATIVE_SYSTEMS:
            if key.lower() == fam_l:
                family_entries = ALTERNATIVE_SYSTEMS[key][:6]
                break
        if not family_entries:
            for alias in _FAMILY_ALIAS.get(fam_l, set()):
                if alias in ALTERNATIVE_SYSTEMS:
                    family_entries = ALTERNATIVE_SYSTEMS[alias][:6]
                    break

    if not family_entries:
        # Fallback: infer canonical from keywords in material_type + keywords.
        text = f"{row.get('material_type', '')} {' '.join(row.get('keywords', []) or [])}".lower()
        for canonical in ("Wood", "Stone", "Tile", "Fabric", "Paint", "Metal"):
            if canonical.lower() in text and canonical in ALTERNATIVE_SYSTEMS:
                family_entries = ALTERNATIVE_SYSTEMS[canonical][:6]
                break

    # De-duplicate on name; priors win order so architects see application-
    # specific systems first, then family-level swaps.
    seen = set()
    merged = []
    for e in prior_entries + family_entries:
        if e["name"] not in seen:
            seen.add(e["name"])
            merged.append(e)
    return merged[:8]


def _enrich_rows_with_catalogue(rows: list, top_k: int = 8) -> None:
    """Mutates each row in-place. Sprint 4 — every row is now routed through
    the MaterialMatch Brain, whose decision packet drives category-restricted
    search and per-category ranking."""
    if not rows:
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        row.setdefault("classification", _classify_row(row))
        if "visual_reference" not in row:
            row["visual_reference"] = _visual_reference_label(row)
        if "likely_family" not in row:
            row["likely_family"] = _likely_family_label(row)
        # Brain decision packet drives everything below.
        if "brain" not in row:
            row["brain"] = materialmatch_brain(row)
        brain = row["brain"]
        # Legacy / UI-shape aliases so older frontend fields keep working.
        row["classification"] = brain["classification"]
        row["searched_categories"] = brain["allowed_categories"]
        row["searched_libraries"] = brain["allowed_libraries"]
        row["excluded_libraries"] = brain["excluded_libraries"]
        if "catalogue_matches" not in row:
            row["catalogue_matches"] = _find_catalogue_matches(
                row, top_k=top_k,
                allowed_categories=brain["allowed_categories"] or [],
                weights=brain["ranking_weights"],
            )
        if "alternative_systems" not in row:
            row["alternative_systems"] = _alternative_systems_for(row)
        if "match_buckets" not in row:
            row["match_buckets"] = _bucket_matches(row.get("catalogue_matches") or [])


# ---------------------------------------------------------------------------
# Sprint 5 — In-memory index of published Studio records so uploaded catalogue
# entries become searchable alongside the seeded MaterialMatch Library.
_STUDIO_INDEXED_RECORDS: list[dict] = []


def _studio_record_to_search_item(rec: dict) -> dict:
    """Normalise a Studio record to the shape `_score_catalogue_item` expects."""
    return {
        "id": rec["id"],
        "brand": rec.get("brand") or "Uploaded catalogue",
        "catalogue": rec.get("collection") or "Uploaded PDF",
        "material_name": rec.get("material_name") or "Extracted material",
        "material_code": rec.get("material_code"),
        "material_family": rec.get("material_family") or rec.get("category") or "",
        "category": rec.get("category") or "Laminates",
        "finish": rec.get("finish") or "",
        "color_name": rec.get("color_name") or "",
        "color_hex": rec.get("color_hex") or "#B7ADA0",
        "texture": rec.get("texture") or "",
        "page_number": rec.get("page_number"),
        "keywords": rec.get("keywords", []),
        "source": rec.get("source") or "Uploaded PDF",
    }


async def _refresh_studio_index() -> None:
    global _STUDIO_INDEXED_RECORDS
    try:
        docs = await db.ke_records.find({"status": "published"}).to_list(2000)
        # User-uploaded catalogues rank ahead of demo-seeded ones so a real
        # PDF upload always outranks the seeded demo library.
        docs.sort(key=lambda d: 1 if d.get("demo_seed") else 0)
        _STUDIO_INDEXED_RECORDS = [_studio_record_to_search_item(d) for d in docs]
        logger.info("Studio index refreshed: %d records", len(_STUDIO_INDEXED_RECORDS))
    except Exception:
        logger.exception("studio index refresh failed")


# ---------------------------------------------------------------------------
# Sprint 8.2 — Real-record Studio seed.
#
# On first boot we seed the Studio's Published Library from `catalogue_seed.py`
# (SEEDED_CATALOGUE). No fabricated MM-DEMO codes — the records are exactly
# the same real brand / material_name / color_hex / keywords already indexed
# in the Knowledge Engine, presented here so the Studio isn't empty for a
# judge who hasn't uploaded a PDF yet.
#
# Ground rules:
#   • Every seeded row carries `demo_seed=True` so user-uploaded PDFs still
#     outrank them everywhere (see _refresh_studio_index / KE endpoint).
#   • `material_code` is copied verbatim from the seed (usually None) —
#     nothing is invented.
#   • On startup we purge any legacy `MM-DEMO-*` records left behind from an
#     earlier seed version, then re-seed idempotently by seed_version.
# ---------------------------------------------------------------------------
_STUDIO_SEED_VERSION = "v2-real"

# Curated real-record picks, grouped into synthetic "catalogue" uploads. Each
# tuple is (brand, catalogue_display_name, [material_name filter list]).
_STUDIO_SEED_PICKS: list[tuple[str, str, list[str]]] = [
    ("Asian Paints", "Asian Paints — Colour Spectra Royale", [
        "Cotton White", "Warm Ivory", "Almond Whisper", "Pearl Blossom",
        "Wheat Cream", "Ivory Sand",
    ]),
    ("Greenlam", "Greenlam — Signature Wood Laminates", [
        "Warm Oak HPL", "Smoked Oak Laminate", "Warm Ivory Solid",
        "Fluted Oak Panel",
    ]),
    ("Kajaria", "Kajaria — Eternity Porcelain Range", [
        "Statuario 800x1600", "Wood Oak Warm 200x1200",
        "Sand Beige Cement 600x1200", "Terrazzo Warm 600x600",
    ]),
]


def _find_seed_record(brand: str, material_name: str) -> dict | None:
    for item in SEEDED_CATALOGUE:
        if item.get("brand") == brand and item.get("material_name") == material_name:
            return item
    return None


async def _seed_demo_studio_catalogues() -> None:
    """Idempotent: seed the Studio Published Library with a curated subset of
    real records from `catalogue_seed.py`. Purges legacy fabricated MM-DEMO-*
    seeds from earlier versions."""
    if db is None:  # pragma: no cover
        return
    # 1. Migration: purge legacy fabricated records / uploads (any seed version
    #    other than the current one is stale).
    stale = await db.ke_records.count_documents(
        {"demo_seed": True, "$or": [
            {"seed_version": {"$exists": False}},
            {"seed_version": {"$ne": _STUDIO_SEED_VERSION}},
        ]}
    )
    if stale:
        await db.ke_records.delete_many({
            "demo_seed": True,
            "$or": [
                {"seed_version": {"$exists": False}},
                {"seed_version": {"$ne": _STUDIO_SEED_VERSION}},
            ],
        })
        await db.ke_uploads.delete_many({
            "demo_seed": True,
            "$or": [
                {"seed_version": {"$exists": False}},
                {"seed_version": {"$ne": _STUDIO_SEED_VERSION}},
            ],
        })
        logger.info("Purged %d legacy Studio demo records", stale)

    # 2. If current-version seed already present, nothing to do.
    have_current = await db.ke_records.find_one(
        {"demo_seed": True, "seed_version": _STUDIO_SEED_VERSION}
    )
    if have_current:
        return

    now = datetime.now(timezone.utc).isoformat()
    total = 0
    for brand, catalogue_display, names in _STUDIO_SEED_PICKS:
        picks = [_find_seed_record(brand, n) for n in names]
        picks = [p for p in picks if p]
        if not picks:
            continue
        upload_id = str(uuid.uuid4())
        await db.ke_uploads.insert_one({
            "id": upload_id,
            "filename": f"{catalogue_display}.pdf",
            "uploaded_by": "system-demo",
            "status": "published",
            "page_count": len(picks),
            "records_extracted": len(picks),
            "created_at": now,
            "demo_seed": True,
            "seed_version": _STUDIO_SEED_VERSION,
        })
        rec_docs = []
        for pi, seed in enumerate(picks, start=1):
            rec_docs.append({
                "id": str(uuid.uuid4()),
                "upload_id": upload_id,
                "brand": seed["brand"],
                "collection": seed.get("catalogue"),
                "material_name": seed["material_name"],
                "material_code": seed.get("material_code"),  # verbatim — usually None
                "category": seed["category"],
                "material_family": seed.get("material_family") or seed["category"],
                "finish": seed.get("finish"),
                "color_name": seed.get("color_name"),
                "color_hex": seed.get("color_hex"),
                "texture": seed.get("texture"),
                "pattern": None,
                "application": None,
                "page_number": pi,
                "page_preview_b64": None,
                "status": "published",
                "keywords": list(seed.get("keywords", [])),
                "created_at": now,
                "published_at": now,
                "demo_seed": True,
                "seed_version": _STUDIO_SEED_VERSION,
                "source": "Reference catalogue",
            })
            total += 1
        if rec_docs:
            await db.ke_records.insert_many(rec_docs)
    if total:
        logger.info("Seeded %d real Studio reference records", total)




# ---------------------------------------------------------------------------
# Sprint 4 — MaterialMatch Brain v1.
#
# The Brain sits between raw AI classification and the catalogue matcher.
# For every zone it decides *what a designer would realistically specify*
# (not just "what does this look like"), and hands the matcher:
#   • allowed_libraries    — hard whitelist
#   • excluded_libraries   — hard blacklist (kept for UI transparency)
#   • ranking_weights      — category-specific weight profile
#   • possible_construction_systems
#   • application_context, detected_finish, likely_material_family
#   • reasoning_notes      — surfaced under "Why MaterialMatch searched this"
# ---------------------------------------------------------------------------

# Application-context detection — inferred from zone name + material_type.
_APPLICATION_CONTEXTS: list[tuple[tuple[str, ...], str]] = [
    (("wall paint", "painted wall", "emulsion"), "wall paint"),
    (("headboard wall", "headboard panel", "behind-bed"), "headboard wall"),
    (("tv wall", "media wall", "feature wall", "accent wall"), "feature wall"),
    (("wall behind sofa", "sofa wall", "living wall"), "feature wall"),
    (("ceiling",), "ceiling"),
    (("floor", "flooring"), "flooring"),
    (("countertop", "kitchen top", "vanity top", "worktop"), "countertop"),
    (("backsplash",), "backsplash"),
    (("bathroom", "shower wall", "wet wall"), "bathroom wet wall"),
    (("kitchen wall",), "kitchen wall"),
    (("curtain", "drape"), "curtain"),
    (("rug", "carpet"), "rug"),
    (("bedding", "bedcover", "duvet", "linen"), "bedding"),
    (("bed frame", "headboard —", "upholstered headboard"), "furniture upholstery"),
    (("sconce", "pendant", "chandelier", "lamp", "downlight"), "lighting fixture"),
    (("nightstand", "side table", "console", "cabinet", "dresser", "bed"), "furniture body"),
    (("handle", "hinge", "profile", "hardware", "faucet", "trim"), "hardware"),
    (("vase", "ornament", "sculpture", "art"), "decor object"),
]


# Ranking weight profiles per Knowledge-Engine category. Weights sum to ~1.0;
# they scale the family/keyword/color/finish/texture components in
# `_score_catalogue_item` so paint is colour-heavy, tile/stone is pattern-heavy,
# fabric is texture-heavy, etc.
_RANKING_WEIGHTS = {
    # (family, visual, color, finish, texture)
    "Paints":    {"family": 0.10, "visual": 0.10, "color": 0.55, "finish": 0.15, "texture": 0.10},
    "Laminates": {"family": 0.10, "visual": 0.30, "color": 0.25, "finish": 0.20, "texture": 0.15},
    "Veneers":   {"family": 0.10, "visual": 0.30, "color": 0.25, "finish": 0.20, "texture": 0.15},
    "Stone":     {"family": 0.10, "visual": 0.30, "color": 0.25, "finish": 0.20, "texture": 0.15},
    "Tiles":     {"family": 0.10, "visual": 0.30, "color": 0.25, "finish": 0.20, "texture": 0.15},
    "Fabric":    {"family": 0.10, "visual": 0.15, "color": 0.25, "finish": 0.15, "texture": 0.35},
    "Lighting":  {"family": 0.35, "visual": 0.20, "color": 0.15, "finish": 0.20, "texture": 0.10},
    "Hardware":  {"family": 0.30, "visual": 0.15, "color": 0.20, "finish": 0.25, "texture": 0.10},
    "Furniture": {"family": 0.35, "visual": 0.20, "color": 0.15, "finish": 0.20, "texture": 0.10},
    # Fallback — balanced, no category dominance.
    "_default":  {"family": 0.20, "visual": 0.25, "color": 0.25, "finish": 0.15, "texture": 0.15},
}


def _application_context(row: dict) -> str:
    zone_l = str(row.get("zone") or "").lower()
    mtype_l = str(row.get("material_type") or "").lower()
    text = f"{zone_l} {mtype_l}"
    for keys, ctx in _APPLICATION_CONTEXTS:
        if any(k in text for k in keys):
            return ctx
    return "unclear"


def _brain_construction_systems(app_ctx: str, mtype_l: str) -> list[dict]:
    """Return architect-priored construction systems for the application.

    Reads like a designer's mental checklist — porcelain slab / laminate /
    acrylic / PVC panel / engineered stone / natural marble is NOT the default
    assumption for a glossy veined wall."""
    marble_look = any(k in mtype_l for k in ("marble", "veined", "gloss"))
    wood_look = any(k in mtype_l for k in ("oak", "walnut", "teak", "veneer",
                                             "wood", "grain", "slat", "fluted"))
    if app_ctx == "wall paint":
        return [
            {"name": "Acrylic emulsion", "note": "Standard washable wall paint"},
            {"name": "Luxury silk emulsion", "note": "Soft-sheen premium finish"},
            {"name": "Textured plaster", "note": "Adds tactile depth"},
            {"name": "Limewash / mineral paint", "note": "Muted artisanal texture"},
        ]
    if app_ctx == "feature wall" and marble_look:
        return [
            {"name": "Porcelain slab / large-format tile", "note": "Water-safe, near-zero maintenance"},
            {"name": "Marble-look laminate", "note": "Budget alternative, wipe-clean"},
            {"name": "Acrylic decorative sheet", "note": "High gloss, easy to fabricate"},
            {"name": "PVC marble panel", "note": "Budget option, humidity-safe"},
            {"name": "Engineered stone panel", "note": "Consistent pattern, non-porous"},
            {"name": "Natural marble (premium)", "note": "Lower priority — high cost + sealing"},
        ]
    if app_ctx in {"feature wall", "headboard wall"} and wood_look:
        return [
            {"name": "HPL laminate (wood-look)", "note": "Cheaper, durable"},
            {"name": "Natural veneer", "note": "Real grain feel"},
            {"name": "Fluted MDF panel", "note": "Vertical slat rhythm, ready-to-fit"},
            {"name": "Decorative PVC panel", "note": "Budget, water-safe"},
            {"name": "Engineered wood panel", "note": "Warm, stable"},
            {"name": "Wood-look porcelain tile", "note": "Water-resistant option"},
        ]
    if app_ctx == "flooring":
        if marble_look:
            return [{"name": "Porcelain slab"}, {"name": "Natural marble"}, {"name": "Engineered quartz"}]
        if wood_look:
            return [{"name": "Engineered wood"}, {"name": "Laminate flooring (AC5)"},
                    {"name": "SPC / Vinyl"}, {"name": "Wood-look porcelain tile"}]
        return [{"name": "Vitrified tile"}, {"name": "Marble"}, {"name": "Engineered wood"}]
    if app_ctx == "countertop":
        return [
            {"name": "Engineered quartz"}, {"name": "Granite"},
            {"name": "Solid surface (Corian)"}, {"name": "Porcelain slab"},
            {"name": "Natural marble (premium — sealing required)"},
        ]
    if app_ctx == "bathroom wet wall":
        return [{"name": "Ceramic tile"}, {"name": "Porcelain slab"},
                {"name": "Stone slab"}, {"name": "Waterproof decorative panel"}]
    if app_ctx == "curtain":
        return [{"name": "Sheer linen"}, {"name": "Cotton voile"},
                {"name": "Poly-linen blend"}, {"name": "Blackout drape"}]
    if app_ctx == "rug":
        return [{"name": "Hand-tufted wool"}, {"name": "Hand-knotted wool"},
                {"name": "Jute"}, {"name": "Machine-made polyblend"}]
    if app_ctx == "bedding":
        return [{"name": "Linen"}, {"name": "Cotton sateen"},
                {"name": "Linen-cotton blend"}, {"name": "Washed cotton"}]
    if app_ctx == "furniture upholstery":
        return [{"name": "Woven upholstery fabric"}, {"name": "Bouclé"},
                {"name": "Linen-cotton blend"}, {"name": "Velvet"}, {"name": "Leatherette"}]
    if app_ctx == "furniture body":
        return [{"name": "Wood + veneer"}, {"name": "Laminate over MDF"},
                {"name": "Solid wood (premium)"}, {"name": "Metal + wood combo"}]
    if app_ctx == "lighting fixture":
        return [{"name": "Brass fixture"}, {"name": "Rattan / cane fixture"},
                {"name": "Fluted glass fixture"}, {"name": "Recessed LED"}]
    if app_ctx == "hardware":
        return [{"name": "Brushed brass profile"}, {"name": "Rose-gold PVD steel"},
                {"name": "Aluminium anodised"}]
    return []


# Hard category exclusion rules per classification (Sprint 4 trust guarantee).
_HARD_EXCLUSIONS: dict[str, set[str]] = {
    # A painted wall must never return wood, veneer, laminate, fabric, etc.
    "Paint":     {"Wood", "Veneer", "Laminates", "Fabric", "Furniture",
                  "Stone", "Tiles", "Lighting", "Hardware"},
    "Curtain":   {"Paints", "Laminates", "Veneers", "Stone", "Tiles",
                  "Lighting", "Hardware", "Furniture"},
    "Rug":       {"Paints", "Laminates", "Veneers", "Stone", "Tiles",
                  "Lighting", "Hardware", "Furniture"},
    "Fabric":    {"Paints", "Laminates", "Veneers", "Stone", "Tiles",
                  "Lighting", "Hardware"},
    "Lighting":  {"Paints", "Laminates", "Veneers", "Stone", "Tiles", "Fabric"},
    "Furniture": {"Paints", "Stone", "Tiles"},  # furniture can carry fabric+veneer
    "Hardware":  {"Paints", "Fabric", "Furniture", "Lighting"},
}


def materialmatch_brain(row: dict) -> dict:
    """The MaterialMatch Brain. Returns the decision packet that drives
    catalogue matching. Idempotent and pure — no side effects."""
    classification = row.get("classification") or _classify_row(row)
    mtype_l = str(row.get("material_type") or "").lower()
    fam = str(row.get("material_family") or "").strip()

    # 1. Application context (drives everything else).
    app_ctx = _application_context(row)

    # 2. Detected-finish label (soft language).
    detected_finish = _visual_reference_label(row) or (row.get("material_type") or "Detected material")

    # 3. Likely material family (softened).
    likely_family = _likely_family_label(row)

    # 4. Allowed libraries — start from category-aware search then filter by hard exclusions.
    allowed = list(_categories_for_row(row))

    # 5. Wall-paint hard rule. Paint applications ALWAYS search Paint only —
    # but only when the zone context is ambiguous or explicitly "wall paint".
    # If the zone unambiguously names a non-paint application (backsplash,
    # feature wall, headboard, flooring, countertop, etc.) we trust the zone
    # context over the AI-supplied family (Sprint 8.2 — the mock AI often
    # returns fam="Paint" for any wall-like region, overriding correct
    # architectural intent).
    _NON_PAINT_ZONE_CONTEXTS = {
        "backsplash", "feature wall", "headboard wall", "flooring",
        "countertop", "bathroom wet wall", "kitchen wall", "ceiling",
        "curtain", "rug", "bedding", "furniture upholstery",
        "furniture body", "lighting fixture", "hardware",
    }
    zone_overrides_family = app_ctx in _NON_PAINT_ZONE_CONTEXTS
    if not zone_overrides_family and (
        app_ctx == "wall paint" or classification.lower() == "paint" or fam.lower() == "paint"
    ):
        allowed = ["Paints"]
    elif app_ctx == "curtain":
        allowed = ["Fabric"]
    elif app_ctx == "rug":
        allowed = ["Fabric"]
    elif app_ctx == "bedding":
        allowed = ["Fabric"]
    elif app_ctx == "furniture upholstery":
        allowed = ["Fabric"]
    elif app_ctx == "furniture body":
        allowed = ["Furniture", "Laminates", "Veneers"]
    elif app_ctx == "lighting fixture":
        allowed = ["Lighting"]
    elif app_ctx == "hardware":
        allowed = ["Hardware"]
    elif app_ctx == "countertop":
        allowed = ["Stone", "Tiles", "Laminates"]
    elif app_ctx == "bathroom wet wall":
        allowed = ["Tiles", "Stone"]
    elif app_ctx == "backsplash":
        # Kitchen splashbacks are almost always porcelain tile, natural /
        # engineered stone slab, or a wipeable laminate panel — never paint.
        allowed = ["Tiles", "Stone", "Laminates"]
    elif app_ctx == "kitchen wall":
        # Kitchen walls above counters follow the same specification set as
        # backsplashes; paint is only used well away from cooking surfaces.
        allowed = ["Tiles", "Stone", "Laminates", "Paints"]
    elif app_ctx == "feature wall":
        # Feature / TV walls in residential interiors are specified as
        # decorative panel, laminate, veneer, marble-look porcelain slab or
        # engineered stone — flat paint alone is rarely the intended finish.
        if any(k in mtype_l for k in ("marble", "veined", "stone", "gloss", "porcelain", "slab")):
            allowed = ["Stone", "Tiles", "Laminates"]
        else:
            allowed = ["Laminates", "Veneers", "Tiles", "Stone"]
    elif app_ctx == "headboard wall":
        allowed = ["Laminates", "Veneers", "Fabric"]

    # 6. Hard blacklist by classification.
    blacklist = _HARD_EXCLUSIONS.get(classification, set())
    # Fabric family → treat as Fabric classification exclusion for zone semantics.
    if fam == "Paint":
        blacklist = _HARD_EXCLUSIONS["Paint"]
    if fam in {"Fabric", "Textile"} and app_ctx in {"curtain", "rug", "bedding", "furniture upholstery"}:
        blacklist = _HARD_EXCLUSIONS.get("Curtain" if app_ctx == "curtain"
                                          else "Rug" if app_ctx == "rug" else "Fabric", set())
    allowed = [c for c in allowed if c not in blacklist]

    # Excluded libraries surfaced for the UI trust card.
    all_categories = set(CATEGORY_SETS.keys())
    excluded = sorted(all_categories - set(allowed))

    # 7. Ranking weights (pick the first allowed category's profile).
    weight_key = allowed[0] if allowed else "_default"
    weights = _RANKING_WEIGHTS.get(weight_key, _RANKING_WEIGHTS["_default"])

    # 8. Reasoning notes — human-readable explanation for the collapsible.
    if app_ctx == "wall paint":
        reasoning = ("Selected region reads as a continuous painted wall surface with no visible "
                     "joints, grain, tile edges or product form. Restricting search to the Paint Library.")
    elif app_ctx == "feature wall" and any(k in mtype_l for k in ("marble", "veined", "gloss")):
        reasoning = ("Glossy veined wall finishes in residential interiors are often specified as "
                     "porcelain slab, marble-look laminate, acrylic / PVC panel or engineered stone — "
                     "not exclusively natural marble. Searching Stone + Tiles + Laminates.")
    elif app_ctx in {"feature wall", "headboard wall"}:
        reasoning = ("Wood-look decorative wall panels are typically specified as HPL laminate, "
                     "natural veneer, fluted MDF or PVC panel. Searching Laminates + Veneers.")
    elif app_ctx == "curtain":
        reasoning = "Curtain zones are matched against the Fabric Library only — paint / laminate excluded."
    elif app_ctx == "rug":
        reasoning = "Rug zones are matched against the Fabric Library only — paint / stone / laminate excluded."
    elif app_ctx == "flooring":
        reasoning = ("Flooring context — searching Laminates / Veneers for wood-look or "
                     "Tiles / Stone for slab / marble-look installations.")
    elif app_ctx == "countertop":
        reasoning = ("Countertop context — engineered quartz, granite or porcelain slab are the "
                     "primary specifications; natural marble is a premium fallback.")
    elif app_ctx == "bathroom wet wall":
        reasoning = "Wet-wall context — restricted to Tiles / Stone (waterproof categories) only."
    elif app_ctx == "lighting fixture":
        reasoning = "Detected as a lighting fixture — searching the Lighting Library only."
    elif app_ctx == "furniture body":
        reasoning = "Furniture body context — Furniture Library first, then visible wood-look finish libraries."
    elif app_ctx == "furniture upholstery":
        reasoning = "Upholstered furniture surface — Fabric Library only."
    elif not allowed:
        reasoning = ("Not enough evidence to confidently classify this region. No library was searched. "
                     "Try uploading a supplier catalogue PDF to enrich matches.")
    else:
        reasoning = "Category-aware search based on the detected material family."

    return {
        "classification": classification,
        "application_context": app_ctx,
        "detected_finish": detected_finish,
        "likely_material_family": likely_family,
        "possible_construction_systems": _brain_construction_systems(app_ctx, mtype_l),
        "allowed_libraries": [_LIBRARY_LABELS.get(c, c) for c in allowed],
        "allowed_categories": allowed,
        "excluded_libraries": [_LIBRARY_LABELS.get(c, c) for c in excluded],
        "ranking_weights": weights,
        "reasoning_notes": reasoning,
        "version": "brain-v1",
    }



# of Knowledge-Engine libraries. The matcher NEVER searches outside this
# whitelist, so a paint region can never return a wood veneer.
_CATEGORY_LIBRARIES: dict[str, list[str]] = {
    # Paint / wall-emulsion — search *only* the paint shade catalogue.
    "Paint": ["Paints"],
    # Fabric / upholstery / bedding — fabric only.
    "Fabric": ["Fabric"],
    "Textile": ["Fabric"],
    "Curtain": ["Fabric"],
    "Rug": ["Fabric"],
    # Wood-look decorative surfaces span 3 libraries (as an architect would search).
    "Wood": ["Laminates", "Veneers"],
    "Veneer": ["Veneers", "Laminates"],
    "Laminate": ["Laminates", "Veneers"],
    # Marble/stone-look wall/floor spans stone + tile + laminate (for marble-look laminate/acrylic).
    "Stone": ["Stone", "Tiles", "Laminates"],
    "Marble": ["Stone", "Tiles", "Laminates"],
    "Tile": ["Tiles", "Stone"],
    # Metal / fixtures search hardware.
    "Metal": ["Hardware"],
    # Products.
    "Lighting": ["Lighting"],
    "Furniture": ["Furniture", "Fabric"],  # furniture may have visible fabric
}

# Human-readable "Library" labels for each category.
_LIBRARY_LABELS = {
    "Paints": "Paint Library",
    "Laminates": "Laminate Library",
    "Veneers": "Veneer Library",
    "Stone": "Stone Library",
    "Tiles": "Tile Library",
    "Fabric": "Fabric Library",
    "Lighting": "Lighting Library",
    "Hardware": "Hardware Library",
    "Furniture": "Furniture Library",
}


def _categories_for_row(row: dict) -> list[str]:
    """Return the whitelist of Knowledge-Engine categories to search for this
    row. Classification wins over material_family; zone keywords add flavour."""
    classification = str(row.get("classification") or "").strip()
    fam = str(row.get("material_family") or "").strip()
    zone_l = str(row.get("zone") or "").lower()
    mtype_l = str(row.get("material_type") or "").lower()

    # 1. Zone-driven overrides (paint / curtain / rug / bedding wins early).
    if "paint" in zone_l or fam.lower() == "paint" or "emulsion" in mtype_l:
        return ["Paints"]
    if "curtain" in zone_l or "drape" in zone_l:
        return ["Fabric"]
    if "rug" in zone_l or "carpet" in zone_l:
        return ["Fabric"]
    if "bedding" in zone_l or "bedcover" in zone_l or "linen" in mtype_l:
        return ["Fabric"]
    if "headboard" in zone_l and "upholster" in mtype_l:
        return ["Fabric"]

    # 2. Marble/stone-look wall — never assume natural marble by default.
    if any(k in mtype_l for k in ("marble-look", "marble look", "stone-look", "stone look")):
        return ["Stone", "Tiles", "Laminates"]

    # 3. Wood-look — laminate + veneer (matches designer priors).
    if any(k in mtype_l for k in ("wood-look", "wood look", "veneer", "laminate", "fluted", "slat")):
        return ["Laminates", "Veneers"]

    # 4. Family → library map.
    fam_key = fam.title() if fam else classification
    if fam_key in _CATEGORY_LIBRARIES:
        return _CATEGORY_LIBRARIES[fam_key]

    # 5. Classification fallback.
    if classification in _CATEGORY_LIBRARIES:
        return _CATEGORY_LIBRARIES[classification]

    # 6. Unclear → search *nothing* rather than everything. UI shows
    #    "No library confidently matches this region — try uploading a supplier PDF".
    return []


def _libraries_display(categories: list[str]) -> list[str]:
    return [_LIBRARY_LABELS.get(c, c) for c in categories]



# Zone-keyword → likely material systems (application priors). Used both to
# reword the detected material and to boost catalogue matches that make sense
# for the location. Keys are lowercase substrings found in row["zone"].
_APPLICATION_PRIORS: list[tuple[tuple[str, ...], list[str]]] = [
    (("headboard wall", "feature wall", "accent wall", "tv wall", "media wall"),
     ["Decorative Laminate Panel", "Fluted MDF Panel", "Natural Veneer",
      "PVC Decorative Panel", "Acrylic Sheet", "Wood-look Porcelain Tile"]),
    (("wall behind sofa", "sofa wall", "living wall"),
     ["Decorative Laminate Panel", "Acrylic Sheet", "PVC Decorative Panel",
      "Natural Veneer", "Porcelain Slab", "Engineered Stone Panel"]),
    (("wall paint", "paint",),
     ["Matte Emulsion", "Silk / Satin Emulsion", "Limewash", "Textured Plaster",
      "Wallpaper"]),
    (("flooring", "floor",),
     ["Vitrified Tile", "Engineered Wood", "Laminate Flooring",
      "SPC / Vinyl", "Marble", "Wood-look Porcelain Tile"]),
    (("countertop", "kitchen top", "vanity top"),
     ["Engineered Quartz", "Granite", "Porcelain Slab", "Solid Surface",
      "Marble (premium option)"]),
    (("bathroom", "wet wall", "shower wall"),
     ["Ceramic Tile", "Porcelain Slab", "Stone Slab", "Waterproof Panel"]),
    (("curtain", "drape",),
     ["Cotton Voile", "Sheer Linen", "Poly-Linen Blend", "Blackout Fabric"]),
    (("bedding", "bedcover", "duvet"),
     ["Washed Cotton", "Linen", "Linen-Cotton Blend", "Sateen"]),
    (("rug", "carpet",),
     ["Hand-tufted Wool", "Hand-knotted Wool", "Jute", "Poly Blend"]),
    (("nightstand", "side table", "console", "cabinet"),
     ["Wood + Metal Combo", "Warm Veneer", "Laminate", "Solid Wood"]),
    (("sconce", "lamp", "pendant", "chandelier"),
     ["Brass Fixture", "Rattan Dome", "Fluted Glass Sconce",
      "Recessed LED Downlight"]),
]

# Map raw AI family → softened public label. When the row has strong evidence
# of a natural material this stays canonical; otherwise we prefer *-look.
_SOFT_FAMILY_MAP = {
    "Wood": ("Wood-look decorative finish", "Decorative wood panel / veneer-look"),
    "Stone": ("Stone-look wall panel", "Stone-look slab or panel"),
    "Marble": ("Marble-look glossy panel", "Marble-look slab / laminate / acrylic"),
    "Tile": ("Tile-look finish", "Vitrified / porcelain tile"),
    "Paint": ("Wall paint finish", "Emulsion / silk / textured paint"),
    "Fabric": ("Fabric / textile finish", "Woven / knit / bouclé textile"),
    "Textile": ("Fabric / textile finish", "Woven / knit / bouclé textile"),
    "Laminate": ("Decorative laminate finish", "HPL laminate"),
    "Veneer": ("Veneer wall finish", "Natural or reconstituted veneer"),
    "Metal": ("Metal accent finish", "Brushed / PVD steel / brass"),
    "Lighting": ("Lighting fixture", "Pendant / sconce / floor lamp"),
    "Furniture": ("Furniture piece", "Wood / upholstered / metal furniture"),
}


def _visual_reference_label(row: dict) -> str:
    """Return a professional 'Visual Reference Analysis' one-liner that
    intentionally softens overconfident labels (marble → marble-look glossy
    wall finish, wood → wood-look decorative finish) unless the row itself
    already reads as a natural-material spec."""
    mtype = str(row.get("material_type") or "").strip()
    fam = str(row.get("material_family") or "").strip()
    if not mtype and not fam:
        return ""
    text_l = f"{mtype} {row.get('color', '')} {row.get('texture', '')}".lower()
    # If the AI already used soft language, keep it.
    if any(w in text_l for w in ("look ", "-look", "decorative", "veneer", "laminate",
                                  "engineered", "reconstituted", "porcelain",
                                  "acrylic", "spc", "vinyl")):
        return mtype or _SOFT_FAMILY_MAP.get(fam, (fam or "Detected material",))[0]
    # Otherwise apply softening.
    if fam in {"Stone"} and "marble" in text_l:
        # e.g. "Warm Beige Marble" → "Marble-look glossy wall finish (warm beige)"
        return f"Marble-look glossy finish — {row.get('color') or 'beige'}"
    if fam in {"Wood"} and any(k in text_l for k in ("oak", "walnut", "teak", "wenge", "maple")):
        return f"Wood-look decorative finish — {row.get('color') or fam.lower()}"
    if fam in {"Tile", "Stone"}:
        return f"{fam}-look surface — {row.get('color') or ''}".rstrip(" —")
    return mtype or _SOFT_FAMILY_MAP.get(fam, ("Detected material",))[0]


def _likely_family_label(row: dict) -> str:
    """Return the 'Likely material family' hint under Visual Reference."""
    fam = str(row.get("material_family") or "").strip()
    if fam in _SOFT_FAMILY_MAP:
        return _SOFT_FAMILY_MAP[fam][1]
    return fam or "Uncertain — designer to verify"


def _application_priors_for_zone(zone: str) -> list:
    zl = (zone or "").lower()
    for keys, systems in _APPLICATION_PRIORS:
        if any(k in zl for k in keys):
            return systems
    return []


def _bucket_matches(matches: list) -> dict:
    """Group catalogue matches by confidence tier. UI shows Best by default
    and reveals Possible / Low on demand."""
    best, possible, low = [], [], []
    for m in matches:
        p = m.get("match_percent", 0)
        if p >= 80:
            best.append(m)
        elif p >= 65:
            possible.append(m)
        else:
            low.append(m)
    return {
        "best": best[:4],
        "possible": possible,
        "low": low,
        "counts": {"best": len(best), "possible": len(possible), "low": len(low)},
    }


@api_router.post("/projects/{project_id}/analyze-region")
async def analyze_region(project_id: str, payload: RegionAnalyzePayload,
                         user: dict = Depends(get_current_user)):
    """Sprint 7: interactive region analysis. Runs the same real-AI material
    pipeline against a user-selected crop of the reference image. Does NOT
    persist to the project — the crop analysis is ephemeral, so the designer
    can freely explore different areas without polluting the main spec."""
    doc = await db.projects.find_one({"_id": ObjectId(project_id), "user_id": user["id"]})
    if not doc:
        raise HTTPException(status_code=404, detail="Project not found")
    crop = payload.crop_b64
    if crop.startswith("data:"):
        crop = crop.split(",", 1)[-1]
    crop_bytes_len = (len(crop) * 3) // 4
    if crop_bytes_len > LLM_ANALYSIS_REF_IMAGE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Cropped area too large")
    if not (ENABLE_REAL_ANALYSIS and EMERGENT_LLM_KEY):
        # Deterministic fallback: return a small demo-style set so the flow
        # is demoable without live AI.
        fallback_rows = [{
            "zone": "Selected Area",
            "material_family": "Wood",
            "material_type": "Warm oak veneer (sample)",
            "color": "Warm honey",
            "texture": "Straight grain",
            "finish": "Matte oiled",
            "confidence": 78,
            "brands_to_check": ["Century Ply", "Greenlam"],
            "vendor_type": "Panel supplier",
            "sourcing_keywords": ["warm oak veneer india"],
            "indian_alternative": "Century Ply Sainik warm-oak veneer",
            "alternatives": [
                {"name": "HPL Laminate — warm oak", "why": "Cheaper, high durability", "cost_tier": "budget",
                 "durability": "Very High", "maintenance": "Low", "brands_to_check": ["Merino"]},
                {"name": "Fluted MDF slat panel", "why": "Ready-to-fit slatted look", "cost_tier": "mid",
                 "durability": "Medium", "maintenance": "Dust", "brands_to_check": ["Action Tesa"]},
            ],
        }]
        _enrich_rows_with_catalogue(fallback_rows)
        return {
            "rows": fallback_rows,
            "summary": {"overall_style": "Selected area — sample analysis", "palette": ["Warm oak"]},
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "version": "region-mock-v1",
            "ephemeral": True,
        }
    try:
        await _check_and_increment_quota(user["id"])
        result = await run_real_analysis(project_id, user["id"], crop,
                                         region=user.get("preferred_region", DEFAULT_REGION))
        _enrich_rows_with_catalogue(result.get("rows") or [])
        result["ephemeral"] = True
        result["region_note"] = (payload.note or "").strip()[:200]
        return result
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"analyze-region failed project={project_id}")
        raise HTTPException(status_code=502, detail="Region analysis failed. Please try again.")


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
        # Sprint 3 rooms
        await db.rooms.create_index([("project_id", 1), ("order", 1)])
        await db.rooms.create_index([("user_id", 1)])
        await db.rooms.create_index("share_slug", unique=True, sparse=True)
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

    # Sprint 4: seed the global read-only DEMO project. Every user (and even
    # signed-out visitors) can view this via GET /api/demo/project. Idempotent.
    try:
        await _seed_demo_project()
    except Exception:
        logger.exception("Demo project seed failed")
    try:
        await _seed_demo_studio_catalogues()
    except Exception:
        logger.exception("Studio demo seed failed")
    try:
        await _refresh_studio_index()
    except Exception:
        logger.exception("Studio index warm-up failed")


async def _seed_demo_project() -> None:
    """Create/refresh the global read-only demo project + one demo room. This
    is stored under a synthetic admin user with role='system-demo' so it never
    surfaces in a real user's dashboard. Uses a fixed slug/id so
    GET /api/demo/project can find it without an id in the URL.

    Sprint 2 Revision — the demo is a premium warm-modern BEDROOM. Every
    detected material / product / catalogue match corresponds to something
    that is actually visible in the reference image."""
    marker = {"is_demo": True, "demo_slug": "materialmatch-demo-warm-living"}
    existing = await db.projects.find_one(marker)
    now = datetime.now(timezone.utc).isoformat()
    # Warm-modern bedroom — bouclé headboard, oak flooring, sheer curtains,
    # bedside sconce, wool rug, ceramic + brass accents.
    demo_ref_url = ("https://images.unsplash.com/photo-1595526114035-0d45ed16cfbf"
                    "?w=1600&q=80&auto=format&fit=crop")
    demo_ref_b64 = ""
    try:
        import urllib.request
        req = urllib.request.Request(demo_ref_url, headers={"User-Agent": "materialmatch-demo"})
        with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310
            demo_ref_b64 = base64.b64encode(r.read()).decode("utf-8")
    except Exception:
        logger.warning("Demo reference image fetch failed — demo will have no image")

    def _alt(name, why, cost, durability, maintenance, brands, use_case=""):
        return {"name": name, "why": why, "cost_tier": cost, "durability": durability,
                "maintenance": maintenance, "brands_to_check": brands, "use_case": use_case}
    analysis_rows = [
        {"zone": "Headboard Wall — Fluted Oak Slat Panel", "material_family": "Wood",
         "material_type": "Warm oak fluted slat panelling",
         "color": "Warm oak", "texture": "Vertical fluted slat", "finish": "Matte oiled",
         "design_style": "Warm modern bedroom", "keywords": ["oak slat", "fluted", "vertical panel", "warm wood"],
         "confidence": 92, "procurement_difficulty": "Easy in India",
         "pin": {"x": 50, "y": 32},
         "indian_alternative": "Greenlam Fluted Oak Panel or Action Tesa fluted MDF with warm oak skin",
         "brands_to_check": ["Greenlam", "Merino", "Action Tesa"], "vendor_type": "Panel supplier",
         "sourcing_keywords": ["fluted oak slat panel india"]},
        {"zone": "Bed Frame — Upholstered Bouclé Headboard", "material_family": "Fabric",
         "material_type": "Cream bouclé upholstered headboard",
         "color": "Cream bouclé", "texture": "Loop pile bouclé", "finish": "Textured soft",
         "design_style": "Warm modern bedroom", "keywords": ["boucle", "cream", "upholstered", "headboard"],
         "confidence": 90, "procurement_difficulty": "Easy",
         "pin": {"x": 50, "y": 55},
         "indian_alternative": "D'Decor cream bouclé over MDF headboard fabricator",
         "brands_to_check": ["D'Decor", "Urban Ladder", "Home Centre"], "vendor_type": "Upholstery",
         "sourcing_keywords": ["boucle headboard cream india"]},
        {"zone": "Bedding — Ivory Linen Bedcover", "material_family": "Fabric",
         "material_type": "Warm ivory linen bed set",
         "color": "Warm ivory", "texture": "Woven linen", "finish": "Soft matte",
         "design_style": "Calm modern", "keywords": ["linen bedding", "ivory", "warm", "cozy"],
         "confidence": 88, "procurement_difficulty": "Easy",
         "pin": {"x": 55, "y": 75},
         "indian_alternative": "Fabindia ivory linen set or Sarita Handa linen bedcover",
         "brands_to_check": ["Fabindia", "Sarita Handa", "House of MG"], "vendor_type": "Home textile",
         "sourcing_keywords": ["ivory linen bedding india"]},
        {"zone": "Flooring — Engineered Warm Oak Plank", "material_family": "Wood",
         "material_type": "Engineered warm oak plank flooring",
         "color": "Honey oak", "texture": "Long plank grain", "finish": "Satin oiled",
         "design_style": "Contemporary warm", "keywords": ["engineered oak", "warm plank floor", "honey"],
         "confidence": 90, "procurement_difficulty": "Moderate",
         "pin": {"x": 30, "y": 92},
         "indian_alternative": "Pergo XP engineered oak or Kajaria Wood Oak Warm 200x1200 wood-look tile",
         "brands_to_check": ["Pergo", "Action Tesa", "Kajaria"], "vendor_type": "Flooring showroom",
         "sourcing_keywords": ["engineered oak flooring india"]},
        {"zone": "Rug — Hand-Tufted Sand Wool Rug", "material_family": "Fabric",
         "material_type": "Hand-tufted sand wool area rug",
         "color": "Sand beige", "texture": "Low-loop pile", "finish": "Hand-tufted matte",
         "design_style": "Japandi neutral", "keywords": ["wool rug", "sand", "neutral", "hand tufted"],
         "confidence": 84, "procurement_difficulty": "Easy",
         "pin": {"x": 50, "y": 90},
         "indian_alternative": "Jaipur Rugs Manchaha or Obeetee hand-tufted wool",
         "brands_to_check": ["Jaipur Rugs", "Obeetee", "Cocoon"], "vendor_type": "Rug atelier",
         "sourcing_keywords": ["hand tufted wool rug india"]},
        {"zone": "Wall Paint — Warm White Matte Emulsion", "material_family": "Paint",
         "material_type": "Warm white matte emulsion",
         "color": "Warm white", "texture": "Smooth", "finish": "Matte",
         "design_style": "Calm modern", "keywords": ["warm white", "matte paint", "royale"],
         "confidence": 95, "procurement_difficulty": "Very easy",
         "pin": {"x": 15, "y": 20},
         "indian_alternative": "Asian Paints Royale 'Cotton White' or Berger Silk Breathe Warm White",
         "brands_to_check": ["Asian Paints", "Berger", "Nerolac", "Dulux"], "vendor_type": "Paint retailer",
         "sourcing_keywords": ["asian paints cotton white"]},
        {"zone": "Nightstand — Warm Walnut Bedside", "material_family": "Furniture",
         "material_type": "Warm walnut veneered bedside table",
         "color": "Warm walnut", "texture": "Wood grain", "finish": "Matte oiled",
         "design_style": "Warm modern", "keywords": ["walnut", "nightstand", "warm"],
         "confidence": 82, "procurement_difficulty": "Easy",
         "pin": {"x": 82, "y": 75},
         "indian_alternative": "Pepperfry Studio walnut nightstand or Gulmohar Lane brass-legged bedside",
         "brands_to_check": ["Pepperfry", "Gulmohar Lane", "West Elm India"], "vendor_type": "Furniture retailer",
         "sourcing_keywords": ["walnut nightstand india"]},
        {"zone": "Bedside Lamp — Brushed Brass Reading Sconce", "material_family": "Lighting",
         "material_type": "Brushed brass reading sconce",
         "color": "Warm brass", "texture": "Brushed metal", "finish": "Brushed satin brass",
         "design_style": "Warm modern", "keywords": ["brass", "sconce", "bedside", "reading"],
         "confidence": 80, "procurement_difficulty": "Moderate",
         "pin": {"x": 82, "y": 45},
         "indian_alternative": "Whitteny warm-brass reading sconce or Havells brass pendant",
         "brands_to_check": ["Whitteny", "Havells", "The White Teak Co."], "vendor_type": "Lighting studio",
         "sourcing_keywords": ["brass reading sconce india"]},
        {"zone": "Curtains — Sheer Ivory Linen Panel", "material_family": "Fabric",
         "material_type": "Sheer ivory linen curtain panel",
         "color": "Ivory", "texture": "Loose sheer weave", "finish": "Soft drape",
         "design_style": "Calm modern", "keywords": ["sheer linen", "ivory curtain", "drape"],
         "confidence": 82, "procurement_difficulty": "Easy",
         "pin": {"x": 12, "y": 45},
         "indian_alternative": "The White Window sheer linen or Deco Window ivory linen panel",
         "brands_to_check": ["Deco Window", "The White Window", "D'Decor"], "vendor_type": "Window furnishing",
         "sourcing_keywords": ["sheer linen curtain india"]},
        # Sprint 2 Refinement — softly-labelled marble-look feature wall so
        # the demo showcases "*-look" architectural language explicitly.
        {"zone": "Behind-Bed Feature Wall — Marble-look Panel", "material_family": "Stone",
         "material_type": "Marble-look glossy wall panel (warm beige)",
         "color": "Warm beige with subtle veining", "texture": "Fine vein", "finish": "Polished glazed",
         "design_style": "Warm modern", "keywords": ["marble look", "beige", "veined", "gloss"],
         "confidence": 76, "procurement_difficulty": "Easy in India",
         "pin": {"x": 65, "y": 25},
         "indian_alternative": "Kalinga Cream Delight Quartz or Kajaria Statuario 800x1600 porcelain slab",
         "brands_to_check": ["Kalinga Stone", "Kajaria", "Somany"], "vendor_type": "Slab / panel supplier",
         "sourcing_keywords": ["marble look wall panel india", "porcelain slab statuario"]},
    ]
    # Sprint 2 Revision: attach classification, top catalogue matches
    # (5–8 per row across the seeded global catalogue) and alternative
    # material systems.  This is what makes the demo internally consistent.
    _enrich_rows_with_catalogue(analysis_rows, top_k=8)
    products_list = [
        {"id": "product_1", "product_name": "Brushed Brass Bedside Reading Sconce",
         "category": "lighting",
         "description": "Slim brushed brass wall sconce with directional head — perfect over a bed.",
         "style_keywords": ["modern", "minimalist", "warm"], "color_keywords": ["brass", "gold"],
         "material_keywords": ["brass", "steel"], "finish_keywords": ["brushed"],
         "estimated_price_inr": "₹6,499", "search_keywords": ["brass reading sconce bedside india"],
         "confidence": 88},
        {"id": "product_2", "product_name": "Bouclé Upholstered Accent Chair",
         "category": "furniture",
         "description": "Sculptural bouclé lounge chair on stained walnut base — reading corner ready.",
         "style_keywords": ["contemporary", "curved", "cozy"], "color_keywords": ["cream"],
         "material_keywords": ["boucle", "wood"], "finish_keywords": ["soft"],
         "estimated_price_inr": "₹27,999", "search_keywords": ["boucle lounge chair bedroom india"],
         "confidence": 85},
        {"id": "product_3", "product_name": "Sand-tone Hand-Tufted Wool Rug",
         "category": "textile-decor",
         "description": "Neutral wool rug with loop-pile texture — anchors the bed area.",
         "style_keywords": ["japandi", "neutral"], "color_keywords": ["sand", "ivory"],
         "material_keywords": ["wool"], "finish_keywords": ["hand-tufted"],
         "estimated_price_inr": "₹18,900", "search_keywords": ["wool bedroom rug india"],
         "confidence": 82},
        {"id": "product_4", "product_name": "Warm Walnut Bedside Nightstand",
         "category": "furniture",
         "description": "Compact walnut-veneered nightstand with a single drawer and brass pull.",
         "style_keywords": ["warm modern", "minimalist"], "color_keywords": ["walnut", "brass"],
         "material_keywords": ["walnut", "brass"], "finish_keywords": ["matte"],
         "estimated_price_inr": "₹14,999", "search_keywords": ["walnut nightstand india"],
         "confidence": 84},
        {"id": "product_5", "product_name": "Ceramic Table Vase Warm Beige",
         "category": "decor",
         "description": "Matte ceramic vase in warm beige — organic modern accent piece.",
         "style_keywords": ["organic", "minimalist"], "color_keywords": ["beige", "terracotta"],
         "material_keywords": ["ceramic"], "finish_keywords": ["matte"],
         "estimated_price_inr": "₹2,199", "search_keywords": ["ceramic vase warm beige india"],
         "confidence": 78},
    ]
    # Enrich products with affiliate matches + fallback search URLs.
    enriched_products = []
    for p in products_list:
        matched = await _match_product_to_affiliates(p)
        pp = dict(p)
        pp["matched_affiliate"] = matched
        pp["search_urls"] = _build_search_urls(p)
        enriched_products.append(pp)
    match_results = {
        "top_matches": [
            {"filename": "warm-bedroom-catalogue.pdf", "page_number": 16,
             "match_percent": 94, "material_name": "Greenlam Fluted Oak Panel",
             "explanation": "Same vertical fluted rhythm and warm honey-oak tone on the headboard wall."},
            {"filename": "warm-bedroom-catalogue.pdf", "page_number": 12,
             "match_percent": 89, "material_name": "D'Decor Bouclé Cream Upholstery",
             "explanation": "Matching loop-pile bouclé texture and cream palette on the headboard."},
            {"filename": "warm-bedroom-catalogue.pdf", "page_number": 21,
             "match_percent": 87, "material_name": "Kajaria Wood Oak Warm 200x1200",
             "explanation": "Warm honey oak plank grain — near-identical tone to the flooring."},
        ],
        "generated_at": now,
    }
    demo_doc = {
        **marker,
        "name": "Warm Modern Bedroom — Demo",
        "client_name": "Bengaluru Residence · Sample",
        "user_id": "system-demo",
        "reference_image_b64": demo_ref_b64,
        "status": "completed",
        "preferred_region": "IN",
        "mock_analysis": {
            "summary": {
                "overall_style": "Warm modern bedroom — layered oak, bouclé and linen",
                "design_style": "Warm modern bedroom",
                "material_palette": "Honey oak, cream bouclé, warm ivory linen, sand wool, warm white paint",
                "key_finishes": "Fluted oak slat, matte oiled wood, hand-tufted wool, sheer linen, brushed brass",
                "sourcing_note": "Every material is sourceable in India — Greenlam / Merino for oak, Fabindia / Sarita Handa for linen, Jaipur Rugs for wool, Asian Paints Cotton White on walls, Whitteny / Havells for brass lighting.",
                "palette": ["Honey oak", "Cream bouclé", "Warm ivory", "Sand", "Warm brass"],
                "dominant_materials": ["Fluted oak slat wall", "Bouclé headboard", "Linen bedding", "Warm oak flooring"],
                "confidence": 90,
            },
            "rows": analysis_rows,
            "generated_at": now,
            "version": "demo-v2",
        },
        "products_detected": {
            "products": enriched_products,
            "generated_at": now,
            "region": "IN",
            "version": "demo-v1",
        },
        "match_results": match_results,
        "created_at": now,
        "updated_at": now,
    }
    if existing:
        await db.projects.update_one({"_id": existing["_id"]}, {"$set": demo_doc})
        demo_project_id = str(existing["_id"])
    else:
        res = await db.projects.insert_one(demo_doc)
        demo_project_id = str(res.inserted_id)
        logger.info(f"Seeded demo project id={demo_project_id}")

    # Also seed one demo room so /demo can showcase the concept presentation.
    room_marker = {"project_id": demo_project_id, "is_demo_room": True}
    existing_room = await db.rooms.find_one(room_marker)
    room_doc = {
        **room_marker,
        "user_id": "system-demo",
        "name": "Primary Bedroom",
        "room_type": "bedroom",
        "order": 0,
        "current_site_photos": [],
        "moodboards": [],
        "reference_images": [],
        "final_render_images": [],
        "concept_overview": (
            "This bedroom is designed to feel calm, warm and considered. A restrained "
            "palette of honey oak, cream bouclé and ivory linen creates a quiet contrast "
            "of textures. Layered warm-white lighting and a hand-tufted wool rug soften the "
            "composition, while curated brushed-brass accents introduce a subtle premium note. "
            "Every specification supports a client-ready look that is timeless, inviting and "
            "unmistakably Indian in its sourcing story."
        ),
        "concept_overview_ai_draft": "",
        "designer_notes": (
            "Delivery in phases: shell + panelling first (4 weeks), then upholstery + rug "
            "(2 weeks), then lighting + decor (1 week). Final styling on-site over a weekend."
        ),
        "pinned_material_row_ids": [
            "Headboard Wall — Fluted Oak Slat Panel",
            "Bed Frame — Upholstered Bouclé Headboard",
            "Flooring — Engineered Warm Oak Plank",
            "Wall Paint — Warm White Matte Emulsion",
        ],
        "pinned_product_ids": ["product_1", "product_2", "product_3", "product_4"],
        "share_slug": "materialmatch-demo",
        "share_enabled": True,
        "created_at": now,
        "updated_at": now,
    }
    if existing_room:
        await db.rooms.update_one({"_id": existing_room["_id"]}, {"$set": room_doc})
    else:
        await db.rooms.insert_one(room_doc)


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
# ============================================================================
# Sprint 3: Concept Presentation Workspace (rooms nested in project)
# ============================================================================
# A "room" is an editable presentation section under a project. It carries
# three image galleries (current site, moodboard, reference), an editable
# concept overview (AI-drafted, designer-edited), pinned material rows +
# products from the parent project, and freeform designer notes. Rooms can
# be shared publicly via a slug for client presentation and printing.
ROOM_TYPES = [
    "living", "bedroom", "kitchen", "bath", "dining",
    "office", "kids", "outdoor", "hallway", "custom",
]

IMAGE_KINDS = ["current_site", "moodboard", "reference", "final_render"]

MAX_IMAGES_PER_KIND = 12
MAX_ROOM_IMAGE_BYTES = 6 * 1024 * 1024  # 6MB per image

LLM_OVERVIEW_TIMEOUT_S = int(os.environ.get("LLM_OVERVIEW_TIMEOUT_S", "45"))


class RoomCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    room_type: str = "custom"


class RoomUpdate(BaseModel):
    name: Optional[str] = None
    room_type: Optional[str] = None
    concept_overview: Optional[str] = None
    designer_notes: Optional[str] = None
    order: Optional[int] = None
    pinned_material_row_ids: Optional[List[str]] = None
    pinned_product_ids: Optional[List[str]] = None


def _room_owner_query(room_id: str, user_id: str) -> dict:
    return {"_id": ObjectId(room_id), "user_id": user_id}


def _room_out(doc: dict) -> dict:
    """Serialize a room doc — drop image bytes to keep response small."""
    if not doc:
        return doc
    d = dict(doc)
    d["id"] = str(d.pop("_id", ""))
    # Convert image arrays to lightweight metadata (id + mime); bytes fetched separately.
    for kind in IMAGE_KINDS:
        key = _kind_field(kind)
        imgs = d.get(key) or []
        d[key] = [{"id": img.get("id"), "mime": img.get("mime")} for img in imgs]
    return d


def _kind_field(kind: str) -> str:
    return {
        "current_site": "current_site_photos",
        "moodboard": "moodboards",
        "reference": "reference_images",
        "final_render": "final_render_images",
    }[kind]


def _make_slug() -> str:
    return secrets.token_urlsafe(9).replace("_", "").replace("-", "")[:12].lower()


@api_router.post("/projects/{project_id}/rooms")
async def create_room(project_id: str, payload: RoomCreate,
                      user: dict = Depends(get_current_user)):
    # Verify project ownership
    proj = await db.projects.find_one({"_id": ObjectId(project_id), "user_id": user["id"]})
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    if payload.room_type not in ROOM_TYPES:
        raise HTTPException(status_code=400, detail=f"room_type must be one of {ROOM_TYPES}")
    now = datetime.now(timezone.utc).isoformat()
    # Determine order = current count
    order = await db.rooms.count_documents({"project_id": project_id})
    # Sprint 5A: auto-populate pins from the parent project so the room is
    # presentation-ready immediately after creation. Designer edits later.
    proj_rows = ((proj.get("mock_analysis") or {}).get("rows")) or []
    proj_products = ((proj.get("products_detected") or {}).get("products")) or []
    default_pinned_rows = [
        str(r.get("id") or r.get("row_id") or r.get("zone") or "")
        for r in proj_rows if (r.get("zone") or r.get("id"))
    ][:16]
    default_pinned_products = [
        str(p.get("id") or "") for p in proj_products if p.get("id")
    ][:16]
    doc = {
        "project_id": project_id,
        "user_id": user["id"],
        "name": payload.name.strip(),
        "room_type": payload.room_type,
        "order": order,
        "current_site_photos": [],
        "moodboards": [],
        "reference_images": [],
        "final_render_images": [],
        "concept_overview": "",
        "concept_overview_ai_draft": "",
        "designer_notes": "",
        "pinned_material_row_ids": default_pinned_rows,
        "pinned_product_ids": default_pinned_products,
        "share_slug": _make_slug(),
        "share_enabled": False,
        "created_at": now,
        "updated_at": now,
    }
    res = await db.rooms.insert_one(doc)
    doc["_id"] = res.inserted_id
    return _room_out(doc)


@api_router.get("/projects/{project_id}/rooms")
async def list_rooms(project_id: str, user: dict = Depends(get_current_user)):
    proj = await db.projects.find_one({"_id": ObjectId(project_id), "user_id": user["id"]})
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    cursor = db.rooms.find({"project_id": project_id, "user_id": user["id"]}).sort("order", 1)
    return [_room_out(d) async for d in cursor]


@api_router.get("/rooms/{room_id}")
async def get_room(room_id: str, user: dict = Depends(get_current_user)):
    try:
        doc = await db.rooms.find_one(_room_owner_query(room_id, user["id"]))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid room id")
    if not doc:
        raise HTTPException(status_code=404, detail="Room not found")
    return _room_out(doc)


@api_router.patch("/rooms/{room_id}")
async def update_room(room_id: str, payload: RoomUpdate,
                      user: dict = Depends(get_current_user)):
    updates: dict = {}
    if payload.name is not None:
        updates["name"] = payload.name.strip()[:80]
    if payload.room_type is not None:
        if payload.room_type not in ROOM_TYPES:
            raise HTTPException(status_code=400, detail="Invalid room_type")
        updates["room_type"] = payload.room_type
    if payload.concept_overview is not None:
        updates["concept_overview"] = payload.concept_overview[:4000]
    if payload.designer_notes is not None:
        updates["designer_notes"] = payload.designer_notes[:4000]
    if payload.order is not None:
        updates["order"] = int(payload.order)
    if payload.pinned_material_row_ids is not None:
        updates["pinned_material_row_ids"] = [str(x) for x in payload.pinned_material_row_ids][:32]
    if payload.pinned_product_ids is not None:
        updates["pinned_product_ids"] = [str(x) for x in payload.pinned_product_ids][:32]
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        res = await db.rooms.find_one_and_update(
            _room_owner_query(room_id, user["id"]),
            {"$set": updates},
            return_document=True,
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid room id")
    if not res:
        raise HTTPException(status_code=404, detail="Room not found")
    return _room_out(res)


@api_router.delete("/rooms/{room_id}")
async def delete_room(room_id: str, user: dict = Depends(get_current_user)):
    try:
        res = await db.rooms.delete_one(_room_owner_query(room_id, user["id"]))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid room id")
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Room not found")
    return {"ok": True}


@api_router.post("/rooms/{room_id}/images")
async def upload_room_image(room_id: str, kind: str,
                            file: UploadFile = File(...),
                            user: dict = Depends(get_current_user)):
    if kind not in IMAGE_KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {IMAGE_KINDS}")
    try:
        room = await db.rooms.find_one(_room_owner_query(room_id, user["id"]))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid room id")
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    field = _kind_field(kind)
    if len(room.get(field, [])) >= MAX_IMAGES_PER_KIND:
        raise HTTPException(status_code=400, detail=f"Max {MAX_IMAGES_PER_KIND} images per kind")
    content = await file.read()
    if len(content) > MAX_ROOM_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Image too large (max 6MB)")
    mime = file.content_type or "image/jpeg"
    if mime not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(status_code=400, detail="Unsupported image format")
    img_id = secrets.token_hex(8)
    img_doc = {
        "id": img_id,
        "mime": mime,
        "b64": base64.b64encode(content).decode("utf-8"),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.rooms.update_one(
        _room_owner_query(room_id, user["id"]),
        {"$push": {field: img_doc},
         "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    return {"ok": True, "id": img_id, "mime": mime, "kind": kind}


@api_router.get("/rooms/{room_id}/images/{kind}/{img_id}")
async def get_room_image(room_id: str, kind: str, img_id: str,
                         user: dict = Depends(get_current_user)):
    if kind not in IMAGE_KINDS:
        raise HTTPException(status_code=400, detail="Invalid kind")
    try:
        room = await db.rooms.find_one(_room_owner_query(room_id, user["id"]))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid room id")
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    field = _kind_field(kind)
    for img in room.get(field, []):
        if img.get("id") == img_id:
            return {"data_url": f"data:{img['mime']};base64,{img['b64']}"}
    raise HTTPException(status_code=404, detail="Image not found")


@api_router.delete("/rooms/{room_id}/images/{kind}/{img_id}")
async def delete_room_image(room_id: str, kind: str, img_id: str,
                            user: dict = Depends(get_current_user)):
    if kind not in IMAGE_KINDS:
        raise HTTPException(status_code=400, detail="Invalid kind")
    field = _kind_field(kind)
    try:
        room = await db.rooms.find_one(_room_owner_query(room_id, user["id"]))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid room id")
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    # Verify image exists before mutating — a single $pull+$set update op leaves
    # modified_count == 1 even when nothing was pulled (because updated_at
    # changes), so we can't rely on modified_count alone to detect misses.
    if not any(img.get("id") == img_id for img in room.get(field, [])):
        raise HTTPException(status_code=404, detail="Image not found")
    await db.rooms.update_one(
        _room_owner_query(room_id, user["id"]),
        {"$pull": {field: {"id": img_id}},
         "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    return {"ok": True}


@api_router.post("/rooms/{room_id}/generate-overview")
async def generate_overview(room_id: str, user: dict = Depends(get_current_user)):
    """Ask the LLM to draft a client-facing concept overview paragraph based
    on pinned materials/products and designer notes. Returns the draft — the
    designer edits and PATCHes concept_overview to persist."""
    try:
        room = await db.rooms.find_one(_room_owner_query(room_id, user["id"]))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid room id")
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    proj = await db.projects.find_one({"_id": ObjectId(room["project_id"]), "user_id": user["id"]})
    if not proj:
        raise HTTPException(status_code=404, detail="Parent project missing")

    # Assemble context from pinned items.
    all_rows = ((proj.get("mock_analysis") or {}).get("rows")) or []
    pinned_rows = [r for r in all_rows
                   if str(r.get("id") or r.get("row_id") or r.get("zone") or "") in set(room.get("pinned_material_row_ids", []))]
    all_products = ((proj.get("products_detected") or {}).get("products")) or []
    pinned_products = [p for p in all_products
                       if str(p.get("id") or "") in set(room.get("pinned_product_ids", []))]

    ctx_lines = [f"Room: {room.get('name')} ({room.get('room_type')})"]
    if pinned_rows:
        ctx_lines.append("Pinned material specifications:")
        for r in pinned_rows[:10]:
            ctx_lines.append(f"  - {r.get('surface') or r.get('zone') or 'surface'}: "
                             f"{r.get('material_name') or r.get('material') or ''} "
                             f"({r.get('finish') or ''}), {r.get('color') or ''}")
    if pinned_products:
        ctx_lines.append("Pinned products:")
        for p in pinned_products[:10]:
            ctx_lines.append(f"  - {p.get('product_name')} ({p.get('category')}) — "
                             f"{p.get('estimated_price_inr') or ''}")
    if room.get("designer_notes"):
        ctx_lines.append(f"Designer notes: {room['designer_notes'][:500]}")

    ctx = "\n".join(ctx_lines)

    system = (
        "You are an interior designer's writing assistant. You DRAFT a warm, "
        "client-facing 'concept overview' paragraph (3–5 sentences, 60–120 words) "
        "describing the design intent for one room. Speak in a confident, human "
        "voice — no bullet lists, no headings, no jargon. The DESIGNER will edit "
        "your draft, so leave room for their voice."
    )
    prompt = (
        "Draft a concept overview paragraph for this room based on the pinned "
        "materials, products and designer notes below. Focus on mood, palette, "
        "texture and how the space will feel. Do NOT list SKUs or prices. Return "
        "ONLY the paragraph text — no markdown, no preamble.\n\n" + ctx
    )

    if not (EMERGENT_LLM_KEY and ENABLE_REAL_PRODUCTS):
        # Deterministic mock fallback so the flow is demoable without the LLM.
        draft = (
            f"{room.get('name', 'This room')} is designed to feel calm, considered "
            "and warmly modern. A restrained palette anchors the space, layering "
            "natural materials with soft, tactile finishes for quiet contrast. "
            "Curated lighting and sculptural accents introduce a sense of intimacy "
            "and rhythm, while every specification supports a client-ready look "
            "that is both timeless and inviting."
        )
    else:
        try:
            import asyncio
            chat = LlmChat(
                api_key=EMERGENT_LLM_KEY,
                session_id=f"overview-{room_id}-{secrets.token_hex(4)}",
                system_message=system,
            ).with_model("openai", LLM_MODEL_PRODUCTS)
            raw = await asyncio.wait_for(chat.send_message(UserMessage(text=prompt)),
                                         timeout=LLM_OVERVIEW_TIMEOUT_S)
            draft = (raw or "").strip().strip("`").strip()
            # Strip any accidental JSON/markdown wrapping.
            if draft.startswith("{") or draft.startswith("["):
                draft = ""
            draft = draft[:1500]
        except Exception:
            logger.exception(f"overview LLM failed room={room_id}")
            raise HTTPException(status_code=502, detail="AI service failed to generate a draft. Please try again.")

    await db.rooms.update_one(
        _room_owner_query(room_id, user["id"]),
        {"$set": {"concept_overview_ai_draft": draft,
                  "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    return {"draft": draft}


class SharePayload(BaseModel):
    enabled: bool


@api_router.post("/rooms/{room_id}/share")
async def toggle_share(room_id: str, payload: SharePayload,
                       user: dict = Depends(get_current_user)):
    try:
        room = await db.rooms.find_one(_room_owner_query(room_id, user["id"]))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid room id")
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    slug = room.get("share_slug") or _make_slug()
    await db.rooms.update_one(
        _room_owner_query(room_id, user["id"]),
        {"$set": {"share_enabled": bool(payload.enabled),
                  "share_slug": slug,
                  "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    return {"ok": True, "share_enabled": bool(payload.enabled), "share_slug": slug}


@api_router.get("/public/rooms/{slug}")
async def public_room(slug: str):
    """Public read-only view of a shared room. No auth required.
    Also embeds the pinned material rows and pinned products from the parent
    project so a client can see the full presentation without any DB round-trips.
    Sprint 5A: also returns catalogue top_matches and the designer's name for
    the presentation cover page."""
    room = await db.rooms.find_one({"share_slug": slug, "share_enabled": True})
    if not room:
        raise HTTPException(status_code=404, detail="Not found")
    # Pull parent project context for pinned items (no auth, but expose only
    # the specific rows/products the designer pinned — nothing else).
    proj = await db.projects.find_one({"_id": ObjectId(room["project_id"])})
    project_name = proj.get("name") if proj else None
    client_name = proj.get("client_name") if proj else None
    pinned_row_ids = set(room.get("pinned_material_row_ids", []))
    pinned_product_ids = set(room.get("pinned_product_ids", []))
    rows_all = ((proj or {}).get("mock_analysis") or {}).get("rows") or []
    products_all = ((proj or {}).get("products_detected") or {}).get("products") or []
    pinned_rows = [r for r in rows_all if str(r.get("id") or r.get("row_id") or r.get("zone") or "") in pinned_row_ids]
    pinned_products = [p for p in products_all if str(p.get("id") or "") in pinned_product_ids]
    catalogue_matches = ((proj or {}).get("match_results") or {}).get("top_matches") or []

    # Lookup designer name (best-effort — do NOT leak user_id or email).
    designer_name = None
    try:
        if proj and proj.get("user_id") and proj["user_id"] != "system-demo":
            owner = await db.users.find_one({"_id": ObjectId(proj["user_id"])},
                                            {"name": 1})
            if owner:
                designer_name = owner.get("name")
    except Exception:
        pass

    def _img_list(field):
        return [{"id": img.get("id"), "mime": img.get("mime")}
                for img in room.get(field, [])]

    return {
        "id": str(room["_id"]),
        "name": room.get("name"),
        "room_type": room.get("room_type"),
        "concept_overview": room.get("concept_overview") or "",
        "designer_notes": room.get("designer_notes") or "",
        "current_site_photos": _img_list("current_site_photos"),
        "moodboards": _img_list("moodboards"),
        "reference_images": _img_list("reference_images"),
        "final_render_images": _img_list("final_render_images"),
        "pinned_material_rows": pinned_rows,
        "pinned_products": pinned_products,
        "catalogue_matches": catalogue_matches,
        "project_name": project_name,
        "client_name": client_name,
        "designer_name": designer_name,
        "share_slug": slug,
        "updated_at": room.get("updated_at"),
    }


@api_router.get("/public/rooms/{slug}/images/{kind}/{img_id}")
async def public_room_image(slug: str, kind: str, img_id: str):
    if kind not in IMAGE_KINDS:
        raise HTTPException(status_code=400, detail="Invalid kind")
    room = await db.rooms.find_one({"share_slug": slug, "share_enabled": True})
    if not room:
        raise HTTPException(status_code=404, detail="Not found")
    for img in room.get(_kind_field(kind), []):
        if img.get("id") == img_id:
            return {"data_url": f"data:{img['mime']};base64,{img['b64']}"}
    raise HTTPException(status_code=404, detail="Image not found")


# ============================================================================
# Sprint 4: Public read-only DEMO project (no auth required)
# ============================================================================
def _sanitize_demo_project(doc: dict) -> dict:
    """Strip internal DB fields from the demo project before returning it."""
    if not doc:
        return {}
    out = {
        "id": str(doc.get("_id", "")),
        "name": doc.get("name"),
        "client_name": doc.get("client_name"),
        "status": doc.get("status"),
        "is_demo": True,
        "preferred_region": doc.get("preferred_region", "IN"),
        "mock_analysis": doc.get("mock_analysis") or {},
        "products_detected": doc.get("products_detected") or {},
        "match_results": doc.get("match_results") or {},
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }
    return out


@api_router.get("/demo/project")
async def get_demo_project():
    """Public: fetch the global read-only demo project. Includes analysis rows,
    products (with affiliate matches), and match results. No auth required."""
    doc = await db.projects.find_one({"is_demo": True, "demo_slug": "materialmatch-demo-warm-living"})
    if not doc:
        raise HTTPException(status_code=404, detail="Demo project not seeded")
    # Sprint 5 — re-enrich rows on every read so newly-published Studio
    # records show up as searchable matches without requiring a re-seed.
    rows = (doc.get("mock_analysis") or {}).get("rows") or []
    for r in rows:
        for k in ("catalogue_matches", "match_buckets", "brain",
                  "searched_categories", "searched_libraries", "excluded_libraries"):
            r.pop(k, None)
    if rows:
        _enrich_rows_with_catalogue(rows)
    return _sanitize_demo_project(doc)


@api_router.get("/demo/reference-image")
async def get_demo_reference_image():
    """Public: fetch the demo project's reference image as a data URL."""
    doc = await db.projects.find_one({"is_demo": True, "demo_slug": "materialmatch-demo-warm-living"})
    if not doc or not doc.get("reference_image_b64"):
        raise HTTPException(status_code=404, detail="Demo reference not available")
    return {"data_url": f"data:image/jpeg;base64,{doc['reference_image_b64']}"}


# ============================================================================
# Sprint 6: Sourceable Shortlist (per-project, designer-curated list of items
# they intend to source — bridges detection and physical verification)
# ============================================================================
class ShortlistItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source_type: str  # "catalogue_match" | "product" | "spec" | "custom"
    source: Optional[str] = ""  # e.g. catalogue filename, platform name
    match_percent: Optional[int] = None
    category: Optional[str] = ""
    zone: Optional[str] = ""
    notes: Optional[str] = ""
    image_ref: Optional[str] = ""  # optional image reference (URL or catalogue page)
    external_url: Optional[str] = ""


@api_router.get("/projects/{project_id}/shortlist")
async def list_shortlist(project_id: str, user: dict = Depends(get_current_user)):
    try:
        doc = await db.projects.find_one(
            {"_id": ObjectId(project_id), "user_id": user["id"]},
            {"shortlist_items": 1},
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project id")
    if not doc:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"items": doc.get("shortlist_items", [])}


@api_router.post("/projects/{project_id}/shortlist")
async def add_shortlist_item(project_id: str, payload: ShortlistItemCreate,
                              user: dict = Depends(get_current_user)):
    if payload.source_type not in {"catalogue_match", "product", "spec", "custom"}:
        raise HTTPException(status_code=400, detail="Invalid source_type")
    try:
        doc = await db.projects.find_one({"_id": ObjectId(project_id), "user_id": user["id"]})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project id")
    if not doc:
        raise HTTPException(status_code=404, detail="Project not found")
    item = {
        "id": secrets.token_hex(8),
        "name": payload.name.strip(),
        "source_type": payload.source_type,
        "source": (payload.source or "").strip(),
        "match_percent": payload.match_percent,
        "category": (payload.category or "").strip(),
        "zone": (payload.zone or "").strip(),
        "notes": (payload.notes or "").strip(),
        "image_ref": (payload.image_ref or "").strip(),
        "external_url": (payload.external_url or "").strip(),
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.projects.update_one(
        {"_id": ObjectId(project_id), "user_id": user["id"]},
        {"$push": {"shortlist_items": item},
         "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    return item


@api_router.delete("/projects/{project_id}/shortlist/{item_id}")
async def remove_shortlist_item(project_id: str, item_id: str,
                                 user: dict = Depends(get_current_user)):
    try:
        res = await db.projects.update_one(
            {"_id": ObjectId(project_id), "user_id": user["id"]},
            {"$pull": {"shortlist_items": {"id": item_id}},
             "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}},
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project id")
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"ok": True}


# ============================================================================
# Sprint 6: Material Library — aggregate the user's catalogue history across
# all their projects into a reusable-looking library. Also expose a small
# "Global Library" placeholder that's transparently marked Coming Soon.
# ============================================================================
GLOBAL_LIBRARY_SEED = [
    {
        "id": "global-asianpaints-2026",
        "name": "Asian Paints — Colour Cottage 2026",
        "brand": "Asian Paints",
        "category": "Paint",
        "region": "India",
        "coverage_note": "Warm neutrals, textured finishes, warm-white palettes.",
        "status": "coming_soon",
    },
    {
        "id": "global-kajaria-2026",
        "name": "Kajaria — Prima Plus Tile Book",
        "brand": "Kajaria",
        "category": "Tile / Stone",
        "region": "India",
        "coverage_note": "Warm limestone, honed marble looks, matte porcelain.",
        "status": "coming_soon",
    },
    {
        "id": "global-centuryply-2026",
        "name": "Century Ply — Sainik Veneer Volume",
        "brand": "Century Ply",
        "category": "Wood & Veneer",
        "region": "India",
        "coverage_note": "Oak, walnut, teak & warm-tone veneers.",
        "status": "coming_soon",
    },
    {
        "id": "global-hafele-2026",
        "name": "Häfele India — Hardware & Fittings",
        "brand": "Häfele India",
        "category": "Fixture / Hardware",
        "region": "India",
        "coverage_note": "Brass, brushed nickel, matte black fittings.",
        "status": "coming_soon",
    },
]


@api_router.get("/library/global")
async def library_global(user: dict = Depends(get_current_user)):
    """MaterialMatch Library — the platform-managed Knowledge Engine.

    Sprint 3 — response is designed so the UI can render category tiles
    with counts, sample brands and status. Legacy `items` field preserved."""
    grouped = {}
    all_brands: set[str] = set()
    for cat, items in CATEGORY_SETS.items():
        brands = []
        rows = []
        for i, it in enumerate(items):
            all_brands.add(it["brand"])
            if it["brand"] not in brands:
                brands.append(it["brand"])
            rows.append({
                "id": f"{cat.lower()}-{i:03d}-{it['brand'].lower().replace(' ', '-')}",
                "brand": it["brand"],
                "catalogue": it["catalogue"],
                "material_name": it["material_name"],
                "material_code": it.get("material_code"),
                "material_family": it["material_family"],
                "finish": it["finish"],
                "color_name": it.get("color_name"),
                "color_hex": it.get("color_hex"),
                "texture": it.get("texture"),
                "page_number": it.get("page_number"),
                "keywords": it.get("keywords", []),
                "source": "MaterialMatch Library",
                "status": "published",
            })
        grouped[cat] = rows
    # Compact category tile summary (counts + sample brands + status).
    tiles = []
    for cat, rows in grouped.items():
        sample_brands: list[str] = []
        for r in rows:
            if r["brand"] not in sample_brands:
                sample_brands.append(r["brand"])
            if len(sample_brands) >= 5:
                break
        tiles.append({
            "category": cat,
            "library_label": _LIBRARY_LABELS.get(cat, cat),
            "count": len(rows),
            "sample_brands": sample_brands,
            "status": "Growing library" if len(rows) < 40 else "Beta",
        })
    return {
        "library_name": "MaterialMatch Library",
        "coverage_status": "Beta — Growing library",
        "total": sum(len(v) for v in grouped.values()),
        "brands_total": len(all_brands),
        "category_names": list(CATEGORY_SETS.keys()),
        "tiles": tiles,
        "categories": grouped,
        # Legacy fields (kept for backwards compatibility with older UIs).
        "items": GLOBAL_LIBRARY_SEED,
        "status": "seeded",
    }


# Sprint 3 — admin-only read-only Knowledge Engine browse.  Manual CRUD /
# CSV/JSON import / PDF ingestion are staged for the next sprint but the data
# shape below (`records` + `filter_meta`) is already designed to accept them.
@api_router.get("/admin/knowledge-engine")
async def admin_knowledge_engine(
    user: dict = Depends(get_current_user),
    category: str | None = None,
    brand: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    q_l = (q or "").strip().lower()
    filtered: list[dict] = []
    all_brands: set[str] = set()
    all_categories: set[str] = set()
    # Studio-uploaded catalogues rank first so admins can verify their
    # ingested PDFs are searchable in the live Knowledge Engine.
    studio_docs = await db.ke_records.find({"status": "published"}).to_list(2000)
    # User uploads (no demo_seed flag) surface ahead of the demo-seeded set.
    studio_docs.sort(key=lambda d: 1 if d.get("demo_seed") else 0)
    for rec in studio_docs:
        item = {
            "id": rec.get("id"),
            "brand": rec.get("brand") or "Uploaded catalogue",
            "catalogue": rec.get("collection") or "Uploaded PDF",
            "material_name": rec.get("material_name") or "Extracted material",
            "material_code": rec.get("material_code"),
            "material_family": rec.get("material_family") or rec.get("category") or "",
            "category": rec.get("category") or "Laminates",
            "finish": rec.get("finish") or "",
            "color_name": rec.get("color_name") or "",
            "color_hex": rec.get("color_hex") or "#B7ADA0",
            "texture": rec.get("texture") or "",
            "page_number": rec.get("page_number"),
            "keywords": rec.get("keywords", []),
            "source": rec.get("source") or "Uploaded PDF",
        }
        all_brands.add(item["brand"])
        all_categories.add(item["category"])
        if category and item["category"] != category:
            continue
        if brand and item["brand"] != brand:
            continue
        if q_l:
            hay = f"{item['brand']} {item['catalogue']} {item['material_name']} {item.get('color_name','')} {' '.join(item.get('keywords', []))}".lower()
            if q_l not in hay:
                continue
        filtered.append({**item, "status": "published"})
    for item in SEEDED_CATALOGUE:
        all_brands.add(item["brand"])
        all_categories.add(item["category"])
        if category and item["category"] != category:
            continue
        if brand and item["brand"] != brand:
            continue
        if q_l:
            hay = f"{item['brand']} {item['catalogue']} {item['material_name']} {item.get('color_name','')} {' '.join(item.get('keywords', []))}".lower()
            if q_l not in hay:
                continue
        filtered.append({
            **{k: v for k, v in item.items() if k != "keywords"},
            "keywords": item.get("keywords", []),
            "status": "published",
            "source": item.get("source") or "Global Library",
        })
    total = len(filtered)
    page = filtered[offset:offset + max(1, min(500, limit))]
    return {
        "library_name": "MaterialMatch Library",
        "total": total,
        "returned": len(page),
        "records": page,
        "filter_meta": {
            "categories": sorted(all_categories),
            "brands": sorted(all_brands),
            "supported_filters": ["category", "brand", "q", "limit", "offset"],
            "coming_soon": {
                "manual_crud": "Sprint 3.5",
                "csv_json_import": "Sprint 3.5",
                "pdf_ingestion": "Sprint 4",
            },
        },
    }


@api_router.get("/library/my")
async def library_my(user: dict = Depends(get_current_user)):
    """Aggregate distinct catalogue filenames the user has uploaded across their
    projects (via the match flow). Includes usage_count and last_used_at so it
    feels like a real library. No re-upload / re-match yet — that is Coming Soon."""
    cursor = db.projects.find(
        {"user_id": user["id"]},
        {"name": 1, "match_results": 1, "updated_at": 1, "created_at": 1},
    )
    library: dict = {}
    async for p in cursor:
        results = p.get("match_results") or {}
        # match_results is keyed by zone -> {uploaded_files: [...]}
        for zone_key, zone_val in results.items():
            if not isinstance(zone_val, dict):
                continue
            for uf in zone_val.get("uploaded_files", []) or []:
                name = (uf or {}).get("name")
                if not name:
                    continue
                entry = library.setdefault(name, {
                    "id": name,
                    "name": name,
                    "type": (uf or {}).get("type") or "",
                    "usage_count": 0,
                    "last_used_at": None,
                    "projects": [],
                })
                entry["usage_count"] += 1
                ts = zone_val.get("generated_at") or p.get("updated_at") or p.get("created_at")
                if ts and (entry["last_used_at"] is None or ts > entry["last_used_at"]):
                    entry["last_used_at"] = ts
                pn = p.get("name")
                if pn and pn not in entry["projects"]:
                    entry["projects"].append(pn)
    items = sorted(
        library.values(),
        key=lambda x: (x.get("last_used_at") or ""),
        reverse=True,
    )
    return {"items": items, "reuse_status": "coming_soon"}


# ============================================================================
# Sprint 5 — MaterialMatch Studio (PDF ingestion pipeline)
# ============================================================================
#
# Pipeline:  Upload PDF → AI Processing → Extract Records (draft)
#            → Review Queue → Approve → Publish → live in Knowledge Engine.
#
# Collections (Mongo):
#   ke_uploads  { id, filename, uploaded_by, status, page_count,
#                 records_extracted, created_at }
#   ke_records  { id, upload_id, brand, collection, material_name,
#                 material_code, category, material_family, finish, color_name,
#                 color_hex, texture, pattern, application, page_number,
#                 page_preview_b64, status: draft|published|rejected,
#                 keywords, created_at, published_at }
# ============================================================================


class StudioApprovePayload(BaseModel):
    record_ids: list[str] = Field(default_factory=list)


def _swatch_from_page(page) -> tuple[str, str | None]:
    """Return (dominant_hex, thumbnail_b64) for a PyMuPDF page. Cheap avg colour."""
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5))
        # Sample every 32nd pixel to keep it fast.
        samples = pix.samples
        n = pix.n  # channels
        step = max(1, len(samples) // (n * 800))
        r_sum = g_sum = b_sum = c = 0
        for i in range(0, len(samples) - n, n * step):
            r_sum += samples[i]
            g_sum += samples[i + 1]
            b_sum += samples[i + 2]
            c += 1
        if c == 0:
            return "#B7ADA0", None
        r, g, b = r_sum // c, g_sum // c, b_sum // c
        hexc = f"#{r:02X}{g:02X}{b:02X}"
        thumb = base64.b64encode(pix.tobytes("jpeg")).decode()
        return hexc, thumb
    except Exception:
        return "#B7ADA0", None


def _extract_records_from_pdf(pdf_bytes: bytes, upload_id: str) -> list[dict]:
    """Parse PDF via PyMuPDF and heuristically extract per-page material records.

    Approach: for each page, take the first heading-like line as the material
    name, extract a code-like token (e.g. `GV-8734`, `8834`, `M-1054`), and
    sample the page's dominant colour for a swatch preview. Live AI upgrade
    can replace this later — the record shape is already the same."""
    import re
    records: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    try:
        pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        logger.exception("PDF open failed")
        raise HTTPException(status_code=400, detail=f"Could not parse PDF: {e}")
    code_re = re.compile(r"\b([A-Z]{1,4}[-]?\d{2,5}(?:[-]?[A-Z0-9]{1,3})?)\b")
    for pi, page in enumerate(pdf, start=1):
        text = page.get_text("text") or ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            continue
        # Heuristics: first meaningful line = material name; scan for code + brand hints.
        material_name = next((ln for ln in lines if 3 < len(ln) < 80), lines[0])[:120]
        code_match = code_re.search(text)
        material_code = code_match.group(1) if code_match else None
        # Guess brand from filename or first line if it matches a seeded brand.
        brand_hint = None
        text_l = text.lower()
        for cat_items in CATEGORY_SETS.values():
            for it in cat_items:
                if it["brand"].lower() in text_l:
                    brand_hint = it["brand"]
                    break
            if brand_hint:
                break
        # Guess category from keywords.
        cat_guess = "Laminates"
        if any(k in text_l for k in ("paint", "emulsion", "shade")):
            cat_guess = "Paints"
        elif any(k in text_l for k in ("veneer",)):
            cat_guess = "Veneers"
        elif any(k in text_l for k in ("marble", "quartz", "granite")):
            cat_guess = "Stone"
        elif any(k in text_l for k in ("tile", "porcelain", "vitrified")):
            cat_guess = "Tiles"
        elif any(k in text_l for k in ("linen", "boucle", "cotton", "fabric", "sateen")):
            cat_guess = "Fabric"
        elif any(k in text_l for k in ("pendant", "sconce", "lamp")):
            cat_guess = "Lighting"
        color_hex, thumb_b64 = _swatch_from_page(page)
        rec_id = str(uuid.uuid4())
        records.append({
            "id": rec_id,
            "upload_id": upload_id,
            "brand": brand_hint,
            "collection": None,
            "material_name": material_name,
            "material_code": material_code,
            "category": cat_guess,
            "material_family": cat_guess.rstrip("s") if cat_guess in {"Paints", "Laminates", "Veneers", "Tiles"} else cat_guess,
            "finish": None,
            "color_name": None,
            "color_hex": color_hex,
            "texture": None,
            "pattern": None,
            "application": None,
            "page_number": pi,
            "page_preview_b64": thumb_b64,
            "status": "draft",
            "keywords": [w.lower() for w in re.findall(r"[a-zA-Z]{4,}", material_name)][:8],
            "created_at": now,
            "published_at": None,
        })
    pdf.close()
    return records


@api_router.post("/admin/studio/upload")
async def studio_upload(
    file: UploadFile = File(...),
    user: dict = Depends(require_admin),
):
    """MaterialMatch Studio — upload a supplier PDF, extract per-page records
    into `draft` state. Records only become searchable after admin approval."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")
    data = await file.read()
    if len(data) > 40 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="PDF too large (max 40 MB)")
    upload_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    upload_doc = {
        "id": upload_id,
        "filename": file.filename,
        "uploaded_by": user.get("id"),
        "status": "processing",
        "page_count": 0,
        "records_extracted": 0,
        "created_at": now,
    }
    await db.ke_uploads.insert_one(upload_doc)
    try:
        records = _extract_records_from_pdf(data, upload_id)
        if records:
            await db.ke_records.insert_many(records)
        upload_doc["page_count"] = len(records)
        upload_doc["records_extracted"] = len(records)
        upload_doc["status"] = "review"
        await db.ke_uploads.update_one({"id": upload_id},
                                        {"$set": {"status": "review",
                                                  "page_count": len(records),
                                                  "records_extracted": len(records)}})
    except HTTPException:
        await db.ke_uploads.update_one({"id": upload_id}, {"$set": {"status": "failed"}})
        raise
    except Exception as e:
        logger.exception("studio ingest failed")
        await db.ke_uploads.update_one({"id": upload_id}, {"$set": {"status": "failed"}})
        raise HTTPException(status_code=500, detail=str(e))
    return {"upload_id": upload_id, **{k: upload_doc[k] for k in ("filename", "status", "records_extracted", "page_count")}}


def _clean_upload(u: dict) -> dict:
    return {k: v for k, v in u.items() if k != "_id"}


def _clean_record(r: dict) -> dict:
    return {k: v for k, v in r.items() if k != "_id"}


@api_router.get("/admin/studio/uploads")
async def studio_list_uploads(user: dict = Depends(require_admin)):
    docs = await db.ke_uploads.find().sort("created_at", -1).to_list(200)
    return {"uploads": [_clean_upload(d) for d in docs]}


@api_router.get("/admin/studio/uploads/{upload_id}/records")
async def studio_upload_records(upload_id: str, user: dict = Depends(require_admin)):
    docs = await db.ke_records.find({"upload_id": upload_id}).sort("page_number", 1).to_list(500)
    return {"upload_id": upload_id, "records": [_clean_record(d) for d in docs]}


@api_router.post("/admin/studio/records/approve")
async def studio_approve(payload: StudioApprovePayload, user: dict = Depends(require_admin)):
    if not payload.record_ids:
        raise HTTPException(status_code=400, detail="record_ids required")
    now = datetime.now(timezone.utc).isoformat()
    result = await db.ke_records.update_many(
        {"id": {"$in": payload.record_ids}, "status": {"$ne": "published"}},
        {"$set": {"status": "published", "published_at": now}},
    )
    await _refresh_studio_index()
    return {"approved": result.modified_count}


@api_router.post("/admin/studio/records/reject")
async def studio_reject(payload: StudioApprovePayload, user: dict = Depends(require_admin)):
    if not payload.record_ids:
        raise HTTPException(status_code=400, detail="record_ids required")
    result = await db.ke_records.update_many(
        {"id": {"$in": payload.record_ids}},
        {"$set": {"status": "rejected"}},
    )
    return {"rejected": result.modified_count}


@api_router.post("/admin/studio/uploads/{upload_id}/publish")
async def studio_publish_all(upload_id: str, user: dict = Depends(require_admin)):
    """Convenience: approve every remaining draft record from this upload."""
    now = datetime.now(timezone.utc).isoformat()
    result = await db.ke_records.update_many(
        {"upload_id": upload_id, "status": "draft"},
        {"$set": {"status": "published", "published_at": now}},
    )
    await db.ke_uploads.update_one({"id": upload_id}, {"$set": {"status": "published"}})
    await _refresh_studio_index()
    return {"approved": result.modified_count, "upload_id": upload_id}


@api_router.get("/admin/studio/library")
async def studio_published_library(
    user: dict = Depends(require_admin),
    category: str | None = None,
    limit: int = 200,
):
    q = {"status": "published"}
    if category:
        q["category"] = category
    docs = await db.ke_records.find(q).sort("published_at", -1).to_list(min(500, limit))
    return {"records": [_clean_record(d) for d in docs], "total": len(docs)}


app.include_router(api_router)

# (CORS middleware was registered earlier — before any routes — so OPTIONS
# preflights are answered without hitting a handler.)
