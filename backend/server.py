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
import asyncio
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
    # 2026-02-01 — When the reference image is replaced, purge every
    # cached analysis result derived from the OLD image. Otherwise the
    # Analysis UI shows stale material rows / product pins / bboxes
    # that were computed against a photo the user no longer has.
    # `status` is reset to draft so the UI knows the project needs
    # re-analysis.
    await db.projects.update_one(
        {"_id": ObjectId(project_id), "user_id": user["id"]},
        {
            "$set": {
                "reference_image_b64": b64,
                "reference_mime": mime,
                "status": "draft",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            "$unset": {
                "mock_analysis": "",
                "analysis": "",
                "products_detected": "",
            },
        },
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
    "Return SURFACE-level rows, not object rows. Aim for 3–6 meaningful rows "
    "(fewer is better than filler).\n\n"
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
    '      "group": "MUST be one of: Wall, Floor, Ceiling, Furniture. Never invent other groups.",\n'
    '      "pin": {"x": 42, "y": 61},'
    "  // OPTIONAL — approximate centre of the material area as percentages of the image (x in 0..100, y in 0..100). Omit the field entirely if you cannot pin the region confidently. Never fabricate coordinates.\n"
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
    "- group is REQUIRED — pick the closest of Wall, Floor, Ceiling or Furniture. "
    "  Wall covers wall paint, feature walls, wall paneling and wall cladding. "
    "  Floor covers all flooring materials. "
    "  Ceiling covers ceiling paint and ceiling panels. "
    "  Furniture covers sofa upholstery, cabinetry, tables, wardrobes, chairs, beds, feature panels.\n"
    "- pin is OPTIONAL; only include it when you can point to the material region. "
    "  Do not invent coordinates — an omitted pin is better than a wrong pin.\n"
    "- procurement_difficulty reflects how easy the material is to source in "
    "an urban Indian design context (Easy = mainstream dealers / e-commerce; "
    "Medium = need specific brand or fabricator; Difficult = imported / bespoke).\n"
    "- material_family MUST be one of the listed enum values.\n"
    "- Prefer SURFACE zones ('Headboard Panel', 'Feature Wall', 'Sofa Upholstery') "
    "over object zones ('Bed', 'Sofa'). Only fall back to object zones if no "
    "specific surface is visible.\n"
    "- Return 3 to 6 rows — fewer credible zones beat many mediocre ones. "
    "Skip ambiguous / occluded surfaces. Do not report people, plants, artwork, "
    "electronics, shadows or tiny decorative objects as materials.\n"
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


# Sprint 5 — Wall / Floor / Ceiling / Furniture top-level grouping.
_ZONE_GROUPS = ("Wall", "Floor", "Ceiling", "Furniture")

# Best-effort mapping from raw zone / application text → top-level group when
# the LLM omits the group field. Uses the same _application_context vocab.
_ZONE_TO_GROUP_HINTS: dict[str, tuple[str, ...]] = {
    "Wall":     ("wall", "backsplash", "feature", "paneling", "cladding",
                 "wainscot", "headboard wall", "accent wall"),
    "Floor":    ("floor", "flooring", "rug", "carpet"),
    "Ceiling":  ("ceiling", "cove", "soffit"),
    "Furniture":("sofa", "chair", "table", "wardrobe", "cabinet", "bed",
                 "shelf", "console", "cushion", "upholstery", "joinery",
                 "kitchen island", "countertop", "counter top", "bedding",
                 "curtain", "lighting", "pendant", "chandelier", "hardware"),
}


def _infer_zone_group(zone: str, material_family: str = "") -> str | None:
    text = f"{zone} {material_family}".lower()
    for grp, keywords in _ZONE_TO_GROUP_HINTS.items():
        if any(k in text for k in keywords):
            return grp
    return None


def _coerce_pin(value) -> dict | None:
    """Coerce an LLM `pin` field into `{x, y}` in 0..100 percent, or None."""
    if not isinstance(value, dict):
        return None
    try:
        x = float(value.get("x"))
        y = float(value.get("y"))
    except (TypeError, ValueError):
        return None
    # Accept 0..1 (unit) too, upscale to 0..100.
    if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and (x > 0 or y > 0):
        x, y = x * 100, y * 100
    if not (0 <= x <= 100 and 0 <= y <= 100):
        return None
    return {"x": round(x, 1), "y": round(y, 1)}


# 2026-02-27 — deterministic fallback pin per top-level group.  Used
# when the LLM omits the optional `pin` field so every material row
# still renders a numbered anchor on the reference image (founder-
# reported bug: pins were dropping randomly on ~30% of results).
#
# Positions are canonical "where you'd expect to see this zone" points:
#   Ceiling  → top band, alternating left / centre / right
#   Wall     → mid-height, alternating left / right of centre
#   Floor    → bottom band, alternating left / centre / right
#   Furniture → lower-mid band, staggered horizontally
# The staggering by `index` keeps multiple rows in the same group from
# stacking on top of one another.
_FALLBACK_PIN_POSITIONS: dict[str, tuple[tuple[float, float], ...]] = {
    "Ceiling":   ((30.0, 12.0), (50.0, 10.0), (70.0, 14.0), (40.0, 16.0), (60.0, 12.0)),
    "Wall":      ((22.0, 45.0), (78.0, 48.0), (35.0, 40.0), (65.0, 52.0), (50.0, 44.0)),
    "Floor":     ((30.0, 88.0), (55.0, 90.0), (75.0, 86.0), (40.0, 92.0), (65.0, 88.0)),
    "Furniture": ((30.0, 68.0), (70.0, 70.0), (50.0, 72.0), (25.0, 62.0), (75.0, 66.0)),
}


def _fallback_pin_for_group(group: str | None, index: int) -> dict | None:
    """Return a deterministic pin coordinate for a group + index.

    Returns None when the group is unknown so we don't fabricate a
    coordinate for genuinely un-groupable rows.
    """
    if not group:
        return None
    slots = _FALLBACK_PIN_POSITIONS.get(str(group).strip().title())
    if not slots:
        return None
    x, y = slots[index % len(slots)]
    return {"x": round(x, 1), "y": round(y, 1)}


# 2026-02-27 (round 5) — Product → SAM3 detection matching.  Products
# are detected by an independent whole-image LLM pass and don't carry
# coordinates.  This helper attaches real bbox-derived pins by looking
# up each product's name / keywords against the SAM3 Stage-A objects
# captured during scene-mode.  Products whose category doesn't map to
# any architectural label (chandelier / lamp / etc.) stay pin-less
# rather than getting fake coordinates.
#
# Word-boundary matches against a curated synonym map: e.g. a product
# called "Upholstered Bed" → SAM3 label "bed" → pin at bbox centre.
# Multi-word matches (e.g. "throw pillow") try the compound label
# first and fall back to any of the whitespace-split parts.
_PRODUCT_SAM3_SYNONYMS: dict[str, tuple[str, ...]] = {
    # SAM3 label → product-name / keyword synonyms that should map to it.
    "bed":         ("bed", "mattress", "bedframe"),
    "headboard":   ("headboard",),
    "sofa":        ("sofa", "couch", "loveseat", "sectional", "armchair"),
    "curtain":     ("curtain", "drape", "drapery", "blind"),
    "rug":         ("rug", "carpet", "runner", "mat"),
    "mirror":      ("mirror",),
    "sink":        ("sink", "basin", "washbasin"),
    "toilet":      ("toilet", "wc"),
    "bathtub":     ("bathtub", "tub"),
    "plant":       ("plant", "planter", "pot", "vase"),
    "shelf":       ("shelf", "shelving", "bookshelf"),
    "nightstand":  ("nightstand", "bedside", "side table"),
    "cabinet":     ("cabinet", "cupboard", "wardrobe", "dresser"),
    "cushion":     ("cushion",),
    "pillow":      ("pillow", "sham"),
    "throw pillow": ("throw pillow", "throw"),
    "mattress":    ("mattress",),
}


def _attach_product_pins(products: list, scene_stage_a: dict) -> None:
    """Mutate `products` in place — attach `pin: {x, y}` (image %) and
    `pin_source: "product_sam3"` to each product that maps to a SAM3
    detection.  Products without a match get `pin=None`.

    Match rule: assemble a haystack from each product's `product_name`,
    `material_keywords`, `style_keywords`, and `search_keywords`
    (lowercased, joined).  For each SAM3 detection, check whether any
    synonym from `_PRODUCT_SAM3_SYNONYMS` for that detection's label
    appears in the haystack.  On first match, take the detection's bbox
    centre.  Ties broken by SAM3 confidence.
    """
    if not products or not isinstance(scene_stage_a, dict):
        return
    objects = scene_stage_a.get("objects") or []
    image_size = scene_stage_a.get("image_size") or {}
    W = float(image_size.get("width") or 0)
    H = float(image_size.get("height") or 0)
    if not objects or W <= 0 or H <= 0:
        return

    # Sort detections by confidence descending so ties go to the
    # highest-confidence mask.
    sorted_objects = sorted(
        objects, key=lambda o: float(o.get("confidence", 0)), reverse=True
    )

    for prod in products:
        if not isinstance(prod, dict):
            continue
        haystack = " ".join([
            str(prod.get("product_name") or ""),
            " ".join(prod.get("material_keywords") or []),
            " ".join(prod.get("style_keywords") or []),
            " ".join(prod.get("search_keywords") or []),
        ]).lower()

        best: dict | None = None
        for obj in sorted_objects:
            label = (obj.get("label") or "").strip().lower()
            if not label:
                continue
            synonyms = _PRODUCT_SAM3_SYNONYMS.get(label)
            if not synonyms:
                continue
            if any(syn in haystack for syn in synonyms):
                best = obj
                break

        if best is None:
            prod["pin"] = None
            prod["pin_source"] = None
            continue

        bbox = best.get("bbox") or []
        try:
            bx, by, bw, bh = [float(v) for v in bbox]
            cx_pct = ((bx + bw / 2.0) / W) * 100.0
            cy_pct = ((by + bh / 2.0) / H) * 100.0
            if 0 <= cx_pct <= 100 and 0 <= cy_pct <= 100:
                prod["pin"] = {"x": round(cx_pct, 1), "y": round(cy_pct, 1)}
                prod["pin_source"] = "product_sam3"
                prod["pin_matched_label"] = best.get("label")
                continue
        except (TypeError, ValueError):
            pass
        prod["pin"] = None
        prod["pin_source"] = None


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

        # Sprint 5 — top-level group (Wall / Floor / Ceiling / Furniture).
        raw_group = str(r.get("group") or "").strip().title()
        group = raw_group if raw_group in _ZONE_GROUPS else _infer_zone_group(
            r["zone"], family,
        )

        # Sprint 5 — optional pin {x, y} in image %. Absent when the LLM
        # cannot pin the region confidently. Never fabricated.
        # 2026-02-27 — deterministic group-based fallback so every row
        # renders a numbered pin on the reference image.  The founder
        # reported "some results show 0 pins" — this ensures pins are
        # consistent on EVERY analysis result even when the LLM omits
        # the (optional) coordinate field.  Fallback anchors sit at the
        # canonical part of the frame for each group so users can still
        # match number → zone at a glance:
        #   Wall     → mid-height, alternating left / right
        #   Ceiling  → top band
        #   Floor    → bottom band
        #   Furniture → lower-mid band
        # The `pin_source` marker is kept for debugging / audit.
        pin = _coerce_pin(r.get("pin"))
        pin_source = "llm" if pin else None
        if pin is None:
            pin = _fallback_pin_for_group(group, i)
            pin_source = "fallback_group" if pin else None

        cleaned.append({
            "zone": r["zone"].strip(),
            "group": group,                              # Sprint 5
            "pin": pin,                                  # Sprint 5
            "pin_source": pin_source,                    # 2026-02-27
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
async def real_analyze(project_id: str,
                       library_scope: str = "admin",
                       user: dict = Depends(get_current_user)):
    """Real-AI material analysis endpoint. Falls back to mock when ENABLE_REAL_ANALYSIS is off.

    2026-02-01 (round 4) — `library_scope` (`"admin"` | `"own"`) selects
    the catalogue corpus. Admin scope hits the seeded + admin-published
    library; own scope hits ONLY the user's own uploaded catalogue.
    Scopes are never silently merged."""
    if library_scope not in ("admin", "own"):
        raise HTTPException(status_code=400,
                            detail="library_scope must be 'admin' or 'own'")
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
    # 2026-02-01 (round 4) — dedup MUST honour the caller's chosen
    # library_scope. Otherwise a "Check Admin Library" click landing
    # within LLM_ANALYSIS_DEDUP_WINDOW_S of a "Check My Catalogue" call
    # returns the CACHED own-scope response, breaking the never-silently-
    # merged rule.
    if (existing.get("version", "").startswith("real-")
            and existing.get("generated_at")
            and (existing.get("library_scope") or "admin") == library_scope):
        try:
            gen_at = datetime.fromisoformat(existing["generated_at"].replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - gen_at).total_seconds()
            if age < LLM_ANALYSIS_DEDUP_WINDOW_S:
                logger.info(f"analyze dedup-hit project={project_id} age={age:.1f}s scope={library_scope}")
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
        # 2026-02-27 — Scene-mode hybrid pipeline is now the DEFAULT for
        # the "Generate specification" full-image path. SAM3 Stage-A
        # detects architectural objects (wall / ceiling / floor / cabinet
        # etc.), then per-object polygon-masked GPT-4o-mini classifies
        # each material.  Every returned row has a `pin` derived from
        # the bbox centre so the reference-image overlay has real
        # anchors instead of the group-based fallback.
        #
        # Belt-and-suspenders fallback → run_real_analysis (LLM-only)
        # kicks in when:
        #   * SAM3 Stage-A returns 0 detected objects (e.g. the upload
        #     is actually an isolated swatch, not a room scene)
        #   * The Roboflow SAM3 API is unavailable, key is missing, or
        #     any other Sam3Error is raised
        #   * The hybrid pipeline throws unexpectedly
        # The LLM-only path still emits deterministic group-based
        # fallback pins so pins are never absent from the UI.
        scene_ok = False
        scene_fallback_reason: str | None = None
        try:
            scene_result, _per_object_crops = await run_scene_region_analysis(
                project_id, user["id"], ref_b64,
                region=user.get("preferred_region", DEFAULT_REGION),
            )
            if scene_result.get("rows"):
                scene_result["version"] = "real-scene-hybrid-v1"
                # Preserve the existing analyze-endpoint contract: keep
                # the top-level `summary` shape and a summary_v2 stub
                # like run_real_analysis produces, so downstream UI
                # code doesn't have to branch.
                scene_result.setdefault("summary_v2", None)
                analysis = scene_result
                scene_ok = True
                logger.info(
                    "[analyze %s] scene-mode DEFAULT: %d rows, stage-a=%s",
                    project_id, len(scene_result["rows"]),
                    scene_result.get("scene_stage_a"),
                )
            else:
                scene_fallback_reason = "stage_a_zero_objects"
        except Exception as exc:
            # Sam3Error (missing key, network), asyncio issues, PIL
            # decoding errors — all funnel to the LLM-only path so the
            # user still gets a spec.
            scene_fallback_reason = f"scene_error:{type(exc).__name__}"
            logger.warning(
                "[analyze %s] scene-mode failed (%s) — falling back to "
                "LLM-only run_real_analysis. reason=%r",
                project_id, type(exc).__name__, str(exc)[:200],
            )

        if not scene_ok:
            analysis = await run_real_analysis(
                project_id, user["id"], ref_b64,
                region=user.get("preferred_region", DEFAULT_REGION),
            )
            if scene_fallback_reason:
                analysis["scene_fallback"] = scene_fallback_reason
                logger.info(
                    "[analyze %s] LLM-only fallback used (reason=%s), "
                    "rows=%d — pins will use deterministic group-based "
                    "fallback for LLM-omitted coordinates.",
                    project_id, scene_fallback_reason,
                    len(analysis.get("rows") or []),
                )

        _enrich_rows_with_catalogue(
            analysis.get("rows") or [],
            library_scope=library_scope,
            user_records=(await _load_user_catalogue_records(user["id"]))
                          if library_scope == "own" else None,
        )
        # 2026-02-01 (round 4) — stamp the scope onto the persisted
        # analysis so the UI surfaces the same choice on reload
        # (button-highlight, "Regenerate · Admin Library" label, etc.).
        if isinstance(analysis, dict):
            analysis["library_scope"] = library_scope
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
            products_list = products_result.get("products", [])
            # 2026-02-27 (round 5) — attach real bbox-derived pins to each
            # detected product by matching its name/keywords against the
            # SAM3 Stage-A objects captured during scene-mode analysis.
            # Products that don't match any object stay pin-less (no fake
            # coordinates).  See _attach_product_pins for match rules.
            _attach_product_pins(products_list, analysis.get("scene_stage_a") or {})
            analysis["products"] = products_list
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
    # Sprint 6 — object-aware region analysis: send the FULL reference
    # image + the selected bbox alongside the crop so the LLM can reason
    # about which OBJECT the selected surface belongs to (e.g. kitchen
    # cabinet vs wall paint) instead of just looking at an isolated
    # colour patch. All three fields are optional for backwards compat.
    full_image_b64: Optional[str] = None      # full reference (may be reused from server-side blob)
    bbox: Optional[list] = None               # [x, y, w, h] percent of image (0-100)
    # 2026-07 hybrid pipeline — when `mode="scene"`, `crop_b64` is
    # treated as the WHOLE room photo rather than a pre-selected region.
    # The endpoint then runs SAM3 Stage-A object detection followed by
    # polygon-masked GPT-4o-mini material classification on each detected
    # object, and returns ONE row per material.  Existing "single"
    # behaviour is unchanged and remains the default.
    mode: Optional[str] = "single"            # "single" | "scene"
    # 2026-02-01 (round 4) — user-uploadable catalogues. "admin" searches
    # the global admin/seed library; "own" searches ONLY this user's
    # uploaded catalogue records. Never silently merged.
    library_scope: Optional[str] = "admin"    # "admin" | "own"


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


# Sprint 5 — bridge Sprint 4 Studio records (singular category names like
# "Laminate", "Paint", "Veneer", "Tile") to the seeded library convention
# (plural: "Laminates", "Paints", "Veneers", "Tiles"). Without this bridge
# the user-side matcher's category hard-filter drops every real Sprint 4
# record on the floor.
_CATEGORY_ALIAS = {
    "Laminate": "Laminates", "Laminates": "Laminates",
    "Veneer": "Veneers",     "Veneers": "Veneers",
    "Tile": "Tiles",         "Tiles": "Tiles",
    "Paint": "Paints",       "Paints": "Paints",
    "Stone": "Stone",
    "Fabric": "Fabric",
    "Lighting": "Lighting",
    "Hardware": "Hardware",
    "Furniture": "Furniture",
}


def _normalize_category(cat: str | None) -> str | None:
    if not cat:
        return None
    return _CATEGORY_ALIAS.get(cat.strip().title(), cat.strip().title())


def _find_catalogue_matches(row: dict, top_k: int = 8, min_overall: int = 62,
                              allowed_categories: list | None = None,
                              weights: dict | None = None,
                              object_locked: bool = False,
                              library_scope: str = "admin",
                              user_records: list | None = None) -> list:
    """Sprint 7 — Describe-Embed-Rerank retrieval stage.

    Pipeline: Brain category gate (hard filter) → pHash exact-loopback
    shortcut (pixel identity only, Hamming ≤ 6) → hybrid embedding +
    attribute retrieval over Visual DNA. Confidence here is retrieval-only
    (capped at 88) — the GPT-4o visual re-rank upgrades / rejects these
    candidates lazily when the user selects a region.

    `weights` is accepted for call-site compatibility but unused: ranking
    is owned by the intelligence package, not per-category weight tables.

    2026-02-27 (round 7) — `object_locked=True` disables the low-DNA-
    confidence "widen alts" heuristic below.  The Brain routed this row
    via high-confidence object-aware detection (wall / ceiling / cabinet
    / countertop / etc.), so the DNA classifier's separate low-family-
    confidence signal must NOT override the Brain's category gate.
    Without this, a matte white wall (Paint object-locked) would get
    widened to include Laminates because DNA said "plain white panel
    could plausibly be a laminate" — producing the trust-breaking
    Paint-wall → 88%-laminate cross-category match reported in the
    founder's wife's session on 2026-02-27.

    2026-02-01 (round 4 — user-uploadable catalogues) — `library_scope`
    controls the search corpus:
      * `"admin"` — searches the global admin/seed catalogue only.
      * `"own"`   — searches ONLY the calling user's uploaded records
                    (pass them in via `user_records`; retrieval never
                    reaches admin data).
    Scopes are NEVER silently merged — the caller must make an explicit
    choice per search."""
    from intelligence.dna import dna_from_query_row
    from intelligence.pipeline import retrieve_matches

    allow_norm: set | None = None
    if allowed_categories is not None:
        allow_norm = set(_normalize_category(c) or "" for c in allowed_categories)
        allow_norm.discard("")
        # 2026-07 — when the DNA classifier itself reported LOW confidence
        # on the primary family (`family_confidence < 0.7`, typically a
        # flat / texture-less crop where Paint vs Laminate vs Veneer are
        # visually indistinguishable), widen the allowed-category set to
        # include the categories of the alternative families the
        # classifier flagged.  Retrieval then searches those extra
        # catalogue partitions AND the `attribute_similarity` family
        # scorer treats them as full-family-match, so the correct answer
        # can win on colour / finish / pattern signals.
        #
        # 2026-02-27 (round 7) — SKIP this widen entirely when the Brain
        # object-locked the category.  Object-aware detection has higher
        # trust than the LLM's own family_confidence self-report on
        # texture-poor crops.
        if not object_locked:
            alts = row.get("family_alternatives") or []
            fconf = float(row.get("family_confidence") or 1.0)
            # LLM-classifier path (single-swatch / analyze-region fallback)
            # attaches alts inside visual_dna rather than on the top-level
            # row.  Look there too so pool widening fires for that path
            # too — critical for isolated-crop uploads that never hit the
            # hybrid scene classifier.
            vdna = row.get("visual_dna") or {}
            if not alts:
                alts = vdna.get("family_alternatives") or []
            if fconf >= 1.0 and vdna.get("family_confidence") is not None:
                try:
                    fconf = float(vdna["family_confidence"])
                except (TypeError, ValueError):
                    pass
            if alts and fconf < 0.7:
                for alt_fam in alts:
                    alt_cat = _normalize_category(alt_fam)
                    if alt_cat:
                        allow_norm.add(alt_cat)
        if not allow_norm:
            return []
    # 2026-02-01 (round 4) — corpus is chosen strictly by scope; no
    # silent merging of admin and user catalogues. Founder rule.
    scope = (library_scope or "admin").lower()
    if scope == "own":
        # User-scope search: only that user's uploaded records.
        # SEEDED_CATALOGUE (the built-in demo library) is EXCLUDED so
        # a user searching "my catalogue" only sees materials they
        # actually uploaded themselves.
        all_items = list(user_records or [])
    else:
        # Admin scope (default): admin uploads + built-in seed library.
        # Studio (uploaded PDF) records first — real supplier data
        # outranks the demo seed.
        all_items = list(_STUDIO_INDEXED_RECORDS) + list(SEEDED_CATALOGUE)
    if allow_norm is not None:
        items = [it for it in all_items
                 if _normalize_category(it.get("category")) in allow_norm]
    else:
        items = all_items

    query_dna = row.get("visual_dna")
    if not query_dna:
        query_dna = dna_from_query_row({**row, "color_hex": _resolve_hex(row)})
    row["visual_dna"] = query_dna
    if SEEDED_CATALOGUE and not SEEDED_CATALOGUE[0].get("dna_embedding"):
        _build_seed_dna_index()  # lazy warm-up (startup normally does this)
    result = retrieve_matches(query_dna, row.get("visual_hashes"), items, top_k=max(top_k * 2, 8))
    row["retrieval_meta"] = result["meta"]

    out = []
    seen: set = set()
    for cand in result["candidates"]:
        conf = cand["confidence"]
        exact = cand["exact_visual_match"]
        if conf < min_overall and not exact:
            continue
        item = cand["item"]
        key = (
            (item.get("material_code") or "").strip().lower() or None,
            str(item.get("material_name") or "").strip().lower(),
            (item.get("color_hex") or "").lower(),
        )
        if key in seen:
            continue
        seen.add(key)

        code = item.get("material_code")
        page = item.get("page_number")
        is_studio = bool(item.get("upload_id"))
        source_library = (
            "Seeded Library" if item.get("demo_seed") else
            "Published Library" if is_studio else
            "Seeded Library"
        )
        source_page_href = None
        if is_studio and page:
            source_page_href = f"/api/admin/studio/uploads/{item['upload_id']}/page/{page}"

        attr = cand.get("attribute_similarity") or {}
        emb = cand.get("embedding_similarity")
        sim_visual = 100 if exact else (int(round(emb * 100)) if emb is not None else 50)
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
            "source": item.get("source") or source_library,
            "source_library": source_library,
            "match_percent": conf,
            "match_reason": cand["reason"],
            "swatch_crop_b64": item.get("swatch_crop_b64"),
            "upload_id": item.get("upload_id"),
            "source_page_href": source_page_href,
            "has_swatch_crop": bool(item.get("swatch_crop_b64")),
            "exact_visual_match": exact,
            "visually_verified": exact,   # rerank flips this for accepted candidates
            "visual_dna": item.get("visual_dna"),
            "similarity": {
                "visual": sim_visual,
                "color": 100 if exact else int(round((attr.get("color") or 0.5) * 100)),
                "finish": 100 if exact else int(round((attr.get("finish") or 0.5) * 100)),
                "texture": 100 if exact else int(round((attr.get("texture") or 0.5) * 100)),
            },
            "debug": {
                "record_id": item["id"],
                "source_library": source_library,
                "source_catalogue": item.get("catalogue"),
                "source_page": page,
                "pipeline_stage": cand["stage"],
                "embedding_similarity": round(emb, 4) if emb is not None else None,
                "attribute_similarity": {k: round(v, 3) for k, v in attr.items()} if attr else None,
                "retrieval_score": round(cand["retrieval_score"], 4),
                "retrieval_confidence": conf,
                "visual_hamming": cand.get("hamming"),
                "exact_visual_match": exact,
                "rerank_score": None,
                "rerank_verdict": None,
            },
        })
        if len(out) >= top_k:
            break
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


def _enrich_rows_with_catalogue(rows: list, top_k: int = 4,
                                  library_scope: str = "admin",
                                  user_records: list | None = None) -> None:
    """Mutates each row in-place. Sprint 4 — every row is now routed through
    the MaterialMatch Brain, whose decision packet drives category-restricted
    search and per-category ranking. Sprint 5 — default top_k lowered to 4:
    the user sees a small set of strong candidates, never 8 mediocre ones.

    2026-02-01 (round 4) — `library_scope` ('admin' | 'own') selects the
    corpus.  Pass `user_records` when scope='own' so retrieval can search
    the user's own uploaded catalogue records without hitting DB
    per-row (they're loaded once by the caller)."""
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
                # 2026-02-27 (round 7) — pass object-locked signal so
                # retrieval skips the widen-on-low-DNA-confidence
                # heuristic for object-aware architectural rows.  See
                # `_find_catalogue_matches` docstring for the failure
                # case this fixes.
                object_locked=bool(brain.get("object_locked")),
                library_scope=library_scope,
                user_records=user_records,
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
    """Normalise a Studio record to the shape `_score_catalogue_item` expects.
    Sprint 5 — preserves the per-swatch crop (`page_preview_b64`), bounding
    box, upload id and collection name so the user-side matcher can display
    the isolated swatch (never the full page) and link back to the source."""
    return {
        "id": rec["id"],
        "brand": rec.get("brand") or "Uploaded catalogue",
        "catalogue": rec.get("collection_name") or rec.get("collection") or "Uploaded PDF",
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
        "source": rec.get("source") or ("Seeded Library" if rec.get("demo_seed") else "Published Library"),
        # Sprint 5 — data required by the user-facing result card:
        "swatch_crop_b64": rec.get("page_preview_b64"),    # isolated swatch image
        "swatch_bbox": rec.get("swatch_bbox"),
        "upload_id": rec.get("upload_id"),
        "collection_name": rec.get("collection_name"),
        "region_class": rec.get("region_class"),
        "record_confidence": rec.get("confidence"),
        "demo_seed": bool(rec.get("demo_seed")),
        "visual_hashes": rec.get("visual_hashes"),         # Sprint 6
        "visual_dna": rec.get("visual_dna"),               # Sprint 7
        "dna_embedding": rec.get("dna_embedding"),         # Sprint 7
    }


async def _refresh_studio_index() -> None:
    """Rebuild the in-memory admin-catalogue index.

    2026-02-01 (round 4 — user-uploadable catalogues): this cache now
    ONLY holds records with `catalogue_scope in ('admin', <missing>)`.
    User-uploaded catalogue records live in the same `ke_records`
    collection but are marked `catalogue_scope='user'` and are fetched
    on-demand per search (see `_load_user_catalogue_records`). This
    keeps process memory bounded regardless of how many users upload
    catalogues.
    """
    global _STUDIO_INDEXED_RECORDS
    try:
        # Only PUBLISHED (non-archived) admin/seed records feed the
        # global matcher. Records without `catalogue_scope` are legacy
        # admin records from before the field existed.
        docs = await db.ke_records.find({
            "status": "published",
            "$or": [
                {"catalogue_scope": {"$exists": False}},
                {"catalogue_scope": "admin"},
            ],
        }).to_list(2000)
        # User-uploaded catalogues rank ahead of demo-seeded ones so a real
        # PDF upload always outranks the seeded demo library.
        docs.sort(key=lambda d: 1 if d.get("demo_seed") else 0)
        _STUDIO_INDEXED_RECORDS = [_studio_record_to_search_item(d) for d in docs]
        logger.info("Studio index refreshed: %d admin/seed records", len(_STUDIO_INDEXED_RECORDS))
    except Exception:
        logger.exception("studio index refresh failed")


async def _load_user_catalogue_records(user_id: str) -> list[dict]:
    """Fetch this user's PUBLISHED catalogue records on-demand for a
    single retrieval call.

    Deliberately not cached in a module-level dict — the user catalogue
    is bounded by `USER_LIBRARY_MAX_UPLOADS` × per-PDF record yield
    (typically ~100 records / PDF), so a per-search live query stays
    cheap AND keeps process memory constant even at thousands of
    concurrent users. Returns items already in the search-item shape
    `_find_catalogue_matches` expects.
    """
    if not user_id:
        return []
    try:
        docs = await db.ke_records.find({
            "status": "published",
            "catalogue_scope": "user",
            "uploaded_by": user_id,
        }).to_list(2000)
        return [_studio_record_to_search_item(d) for d in docs]
    except Exception:
        logger.exception("user catalogue load failed for user=%s", user_id)
        return []


# ---------------------------------------------------------------------------
# Sprint 7 — Visual DNA enrichment (Describe-Embed-Rerank intelligence layer)
VISUAL_DNA_PROVIDER = os.environ.get("VISUAL_DNA_PROVIDER", "openai")
VISUAL_DNA_MODEL = os.environ.get("VISUAL_DNA_MODEL", "gpt-4o-mini")
_DNA_BACKFILL_LOCK = None  # created lazily inside the running event loop


async def _generate_query_vision_dna(crop_b64: str, row: dict) -> dict | None:
    """Run the same vision-DNA pass we use on catalogue swatches, but on the
    user's selected crop. This closes the Sprint 7 asymmetry where the query
    side was building DNA from text attributes only while the catalogue side
    had rich vision-DNA — retrieval was comparing apples to oranges.

    Sprint 8.1 — vision-DNA is given the upstream classifier hints (colour,
    finish, object_type) as WEAK metadata but the SWATCH_DNA_PROMPT
    instructs the vision model to trust pixel evidence over metadata when
    the two conflict. Empirically, dropping metadata entirely made colour
    perception drift ("light grey" for warm oak); dropping only the
    `classifier_material_family` was too aggressive too. The vision model
    behaves best when it can see the classifier's guess AND is explicitly
    told it may correct it.

    Returns a normalized DNA dict on success, None on any failure so the
    caller can fall back to the text-derived DNA. Never raises."""
    if not crop_b64 or not EMERGENT_LLM_KEY:
        return None
    try:
        from intelligence.dna import generate_swatch_dna
        metadata = {
            "detected_color": row.get("color") or "",
            "detected_finish": row.get("finish") or "",
            "object_type_hint": row.get("object_type") or "",
        }
        return await generate_swatch_dna(
            crop_b64, metadata, EMERGENT_LLM_KEY,
            VISUAL_DNA_PROVIDER, VISUAL_DNA_MODEL,
        )
    except Exception as e:
        logger.warning("query vision-DNA generation failed (%s: %s)",
                       type(e).__name__, e)
        return None


def _reconcile_family_with_vision_dna(row: dict, vision_dna: dict) -> dict:
    """Merge the analyser row's family with the vision-DNA family per the
    approved override rules (see intelligence/family.pick_final_family).

    Mutates the row in place:
      - `visual_dna`             attached (query side, symmetric with catalogue)
      - `material_family_original` preserved for debugging / UI
      - `material_family`        overwritten only when the rules trigger
      - `family_routing`         debug packet (original, vision, final, reason)

    Returns the debug packet.
    """
    from intelligence.family import pick_final_family, to_canonical

    orig_family = row.get("material_family")
    vision_family = (vision_dna or {}).get("material_family")
    final_family, reason = pick_final_family(orig_family, vision_family)
    debug = {
        "classifier_family": orig_family,
        "vision_family": vision_family,
        "final_family": final_family,
        "override_applied": bool(final_family and final_family != orig_family),
        "reason": reason,
    }
    row["visual_dna"] = vision_dna
    row["family_routing"] = debug
    if debug["override_applied"]:
        row["material_family_original"] = orig_family
        # Family used for Brain routing / catalogue filter. Canonical DNA
        # family names line up with catalogue metadata (Laminate / Paint / …).
        row["material_family"] = final_family
        # Rebuild classification & Brain packet so the override actually
        # reaches _find_catalogue_matches downstream.
        row["classification"] = _classify_row(row)
        row.pop("brain", None)
    logger.info(
        "region DNA reconcile: classifier=%r vision=%r final=%r reason=%s "
        "override=%s object_type=%r",
        orig_family, vision_family, final_family, reason,
        debug["override_applied"], row.get("object_type"),
    )
    return debug


async def _apply_visual_rerank(row: dict, crop_b64: str, strict: bool = True) -> None:
    """Stage 5 — visual re-rank of a row's retrieved matches against the
    user's selected crop. Mutates the row in place: accepted candidates get
    the re-rank confidence, rejected ones are dropped when `strict=True`.
    Skipped entirely on an exact pHash loopback hit (already pixel-verified
    — no LLM spend). Fails open: if the re-rank call errors, retrieval
    results stand.

    Sprint 8 — candidates without any swatch image are routed AROUND the
    rerank (visual comparison is impossible so a text-only 'judge on
    description alone' verdict is unreliable). Those candidates keep
    their retrieval confidence and are surfaced as 'compatible' at a
    small confidence penalty so the designer can still shortlist them
    for physical sampling. Common case: paint chip records that store
    only colour metadata, not an isolated swatch image.

    2026-07 — `strict=False` scene-mode behaviour: when the query crop is
    a WIDE-ANGLE scene shot (polygon-masked floor / cabinet / wall in a
    room photo), it will systematically look different from a plain
    isolated catalogue swatch (perspective, lighting, surrounding
    context). Rejecting candidates on that basis drops the RIGHT match
    for the wrong reason. In `strict=False` mode, rerank still runs and
    still BOOSTS accepted candidates, but REJECTED candidates are
    demoted (−15 pts) rather than dropped — same treatment paint chips
    without swatch images already get on the strict path."""
    from intelligence.rerank import visual_rerank, RERANK_MAX_CANDIDATES, RERANK_MODEL
    from intelligence.confidence import reranked_confidence
    matches = row.get("catalogue_matches") or []
    qdna = row.get("visual_dna") or {}
    qctx = qdna.get("canonical_description") or row.get("material_type") or "selected surface"
    if not matches:
        row["match_state"] = {"no_confident_match": True,
                              "ai_description": qctx,
                              "reason": "No library candidate cleared the retrieval bar."}
        return
    if matches[0].get("exact_visual_match"):
        row["rerank"] = {"ran": False, "skipped": "exact_loopback"}
        return
    if not EMERGENT_LLM_KEY:
        row["rerank"] = {"ran": False, "skipped": "no_llm_key"}
        return

    def _has_image(m: dict) -> bool:
        return bool(m.get("swatch_crop_b64") or m.get("page_preview_b64"))

    shortlist = matches[:RERANK_MAX_CANDIDATES]
    visual_shortlist = [m for m in shortlist if _has_image(m)]
    text_only_shortlist = [m for m in shortlist if not _has_image(m)]

    accepted: list[dict] = []
    if visual_shortlist:
        results = await visual_rerank(
            crop_b64, [{"item": m} for m in visual_shortlist],
            qctx, EMERGENT_LLM_KEY,
        )
        if results is None:
            row["rerank"] = {"ran": False, "skipped": "rerank_failed"}
            # Fail open — keep original retrieval order, including text-only.
            row["catalogue_matches"] = shortlist
            row["match_buckets"] = _bucket_matches(shortlist)
            return
        by_idx = {r["candidate"]: r for r in results}
        for i, m in enumerate(visual_shortlist):
            r = by_idx.get(i)
            if not r:
                # Reranker returned no verdict for this candidate.
                # Non-strict: keep at retrieval conf.  Strict: drop.
                if not strict:
                    m["debug"]["rerank_score"] = None
                    m["debug"]["rerank_verdict"] = "rerank_no_verdict"
                    accepted.append(m)
                continue
            m["debug"]["rerank_score"] = r["score"]
            m["debug"]["rerank_verdict"] = r["verdict"]
            if r["verdict"] == "accept":
                m["match_percent"] = reranked_confidence(r["score"])
                m["match_reason"] = f"Visually verified — {r['reason']}"
                m["visually_verified"] = True
                accepted.append(m)
            elif not strict:
                # Non-strict scene mode: demote rather than drop.  The
                # wide-angle crop can't be pixel-matched to an isolated
                # swatch, but the retrieval signal is still real.
                m["match_percent"] = max(0, int(m.get("match_percent", 0)) - 15)
                m["match_reason"] = (
                    "Retrieved as a description-level match — visual "
                    f"verification inconclusive ({r.get('reason', 'no reason given')[:120]}). "
                    "Order a physical sample before finalising."
                )
                m["visually_verified"] = False
                accepted.append(m)

    # Text-only candidates (no swatch image) can't be visually verified,
    # but they made the retrieval bar — surface them as 'compatible
    # shortlist' with an honest penalty so the designer can still request
    # physical samples. This is critical for paint records where the
    # catalogue often lacks an isolated swatch image.
    for m in text_only_shortlist:
        m["debug"]["rerank_score"] = None
        m["debug"]["rerank_verdict"] = "text_only"
        # Penalise 15 pts so text-only never outranks visually-verified.
        m["match_percent"] = max(0, int(m.get("match_percent", 0)) - 15)
        m["match_reason"] = (
            "Colour/description compatible — catalogue swatch image "
            "not available for full visual verification. Order a physical "
            "sample before finalising."
        )
        m["visually_verified"] = False
        accepted.append(m)

    accepted.sort(key=lambda m: m["match_percent"], reverse=True)
    row["catalogue_matches"] = accepted
    row["match_buckets"] = _bucket_matches(accepted)
    row["rerank"] = {"ran": bool(visual_shortlist), "model": RERANK_MODEL,
                     "strict": strict,
                     "evaluated": len(visual_shortlist),
                     "accepted": sum(1 for m in accepted if m.get("visually_verified")),
                     "text_only_passthrough": len(text_only_shortlist)}
    if not accepted:
        row["match_state"] = {
            "no_confident_match": True,
            "ai_description": qctx,
            "reason": "Visual verification rejected every library candidate.",
        }


def _record_dna_metadata(rec: dict) -> dict:
    return {
        "brand": rec.get("brand"), "name": rec.get("material_name"),
        "code": rec.get("material_code"), "category": rec.get("category"),
        "collection": rec.get("collection_name") or rec.get("collection"),
        "color": rec.get("color_name") or rec.get("color_hex"),
        "finish": rec.get("finish"), "texture": rec.get("texture"),
        "keywords": rec.get("keywords"),
    }


async def _visual_dna_backfill() -> None:
    """Enrich every published record missing `visual_dna`: vision call on
    the isolated swatch crop when one exists, metadata-only DNA otherwise.
    Also fills empty display metadata (finish / texture / color_name) from
    the DNA. Idempotent — safe to kick after every publish."""
    import asyncio
    from intelligence.dna import (generate_swatch_dna, dna_from_record,
                                  embedding_text)
    from intelligence.embeddings import get_embedder
    global _DNA_BACKFILL_LOCK
    if _DNA_BACKFILL_LOCK is None:
        _DNA_BACKFILL_LOCK = asyncio.Lock()
    if _DNA_BACKFILL_LOCK.locked():
        return
    async with _DNA_BACKFILL_LOCK:
        docs = await db.ke_records.find(
            {"status": "published", "visual_dna": {"$exists": False}}
        ).to_list(3000)
        if not docs:
            return
        logger.info("visual_dna backfill: %d record(s) to enrich", len(docs))
        embedder = get_embedder()
        sem = asyncio.Semaphore(3)
        enriched = 0

        async def enrich(d: dict):
            nonlocal enriched
            async with sem:
                dna = None
                swatch = d.get("page_preview_b64")
                if swatch and EMERGENT_LLM_KEY:
                    dna = await generate_swatch_dna(
                        swatch, _record_dna_metadata(d), EMERGENT_LLM_KEY,
                        VISUAL_DNA_PROVIDER, VISUAL_DNA_MODEL,
                    )
                if not dna:
                    dna = dna_from_record(d)
                vec = await asyncio.to_thread(
                    lambda: embedder.embed([embedding_text(dna)])[0]
                )
                update = {"visual_dna": dna, "dna_embedding": vec}
                # Fill blank display metadata from the DNA (never overwrite).
                pc = dna.get("primary_color") or {}
                if not d.get("finish") and dna.get("finish"):
                    update["finish"] = dna["finish"].title()
                if not d.get("texture") and dna.get("texture"):
                    update["texture"] = dna["texture"].capitalize()
                if not d.get("color_name") and pc.get("name"):
                    update["color_name"] = pc["name"].capitalize()
                if not d.get("pattern") and dna.get("pattern"):
                    update["pattern"] = dna["pattern"].capitalize()
                await db.ke_records.update_one({"id": d["id"]}, {"$set": update})
                enriched += 1

        results = await asyncio.gather(*(enrich(d) for d in docs), return_exceptions=True)
        errors = [r for r in results if isinstance(r, Exception)]
        if errors:
            logger.warning("visual_dna backfill: %d failure(s), first: %s",
                           len(errors), errors[0])
        logger.info("visual_dna backfill: enriched %d/%d record(s)", enriched, len(docs))
        await _refresh_studio_index()


def _build_seed_dna_index() -> int:
    """Compute metadata-only Visual DNA + embeddings for the in-memory
    seeded library (~1s for a few hundred rows). Idempotent, sync."""
    from intelligence.dna import dna_from_record, embedding_text
    from intelligence.embeddings import get_embedder
    pending = [it for it in SEEDED_CATALOGUE if not it.get("dna_embedding")]
    if not pending:
        return 0
    embedder = get_embedder()
    texts = []
    for it in pending:
        it["visual_dna"] = dna_from_record(it)
        texts.append(embedding_text(it["visual_dna"]))
    vecs = embedder.embed(texts)
    for it, v in zip(pending, vecs):
        it["dna_embedding"] = v
    return len(pending)


async def _seed_catalogue_dna() -> None:
    import asyncio
    n = await asyncio.to_thread(_build_seed_dna_index)
    if n:
        logger.info("seed DNA index: embedded %d seeded record(s)", n)


async def _recompute_upload_status(upload_id: str) -> str | None:
    """Recompute the parent catalogue status from its child records.
    Called after every record status change so the Processing Queue
    always reflects the true state of the catalogue.

    Lifecycle:
      processing → review → (review_remaining | published | archived
                             | rejected | failed)

    Never overrides a catalogue that is still `processing` or `failed` at
    ingestion time — those are set by the extractor itself.
    """
    upload = await db.ke_uploads.find_one({"id": upload_id})
    if not upload:
        return None
    current = upload.get("status")
    # Ingestion-owned states stay untouched.
    if current in ("processing", "failed"):
        return current
    cursor = db.ke_records.find(
        {"upload_id": upload_id},
        {"status": 1},
    )
    counts = {"draft": 0, "published": 0, "archived": 0, "rejected": 0}
    total = 0
    async for d in cursor:
        s = d.get("status", "draft")
        counts[s] = counts.get(s, 0) + 1
        total += 1
    if total == 0:
        new_status = "failed"
    elif counts["published"] and counts["draft"] == 0 and counts["archived"] == 0:
        new_status = "published"
    elif counts["archived"] and counts["archived"] == total:
        new_status = "archived"
    elif counts["rejected"] and counts["rejected"] == total:
        new_status = "rejected"
    elif counts["published"] and counts["draft"] > 0:
        new_status = "review_remaining"
    elif counts["draft"] > 0:
        new_status = "review"
    else:
        # Mixed published + archived (no drafts): treat as published so
        # the library still lists the active records.
        new_status = "published" if counts["published"] else "archived"
    if new_status != current:
        await db.ke_uploads.update_one(
            {"id": upload_id},
            {"$set": {"status": new_status,
                       "status_updated_at": datetime.now(timezone.utc).isoformat()}},
        )
    return new_status




async def _recover_stuck_studio_uploads() -> None:
    """Any upload still in `processing` state on startup is stuck from a
    previous OCR / ingress timeout — mark it as `failed` with a diagnostic so
    the admin can delete it."""
    if db is None:  # pragma: no cover
        return
    stuck = await db.ke_uploads.count_documents({"status": "processing"})
    if not stuck:
        return
    await db.ke_uploads.update_many(
        {"status": "processing"},
        {"$set": {
            "status": "failed",
            "failure_reason": ("Extraction did not complete — the previous run "
                               "timed out or the server restarted mid-ingest. "
                               "Delete and re-upload the PDF."),
        }},
    )
    logger.info("Recovered %d stuck 'processing' uploads → failed", stuck)


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
# Stability sprint — purge internal development / testing uploads from the
# Studio so the competition admin looks clean. Keeps: the real Reference
# seed (demo_seed=True) and any admin-uploaded supplier catalogue. Removes:
# anything whose filename matches a developer artefact pattern.
# ---------------------------------------------------------------------------
_DEV_TEST_FILENAME_PATTERNS = [
    r"^pub\.pdf$", r"^rej\.pdf$",
    r"^studio_test.*\.pdf$",
    r"^demo_catalogue.*\.pdf$",
    r"^lighting_catalogue.*\.pdf$",
    r"^rc\.pdf$", r"^x\.pdf$", r"^test\.pdf$",
    r"^testbrand.*\.pdf$",
    r"^rc[\s_-]?test.*\.pdf$",
    # Stability sprint test artifacts (small synthetic PDFs used to verify
    # the OCR fallback and 150 MB limit — never real supplier data).
    r"^merino_text\.pdf$", r"^kalinga_scanned\.pdf$",
    r"^big_text\.pdf$", r"^med_scanned\.pdf$", r"^very_big_scanned\.pdf$",
]


async def _purge_dev_test_uploads() -> None:
    """Remove uploads whose filename (case-insensitive) matches a known
    developer / test artefact. Also purges their child ke_records. Never
    touches Reference seeded records (demo_seed=True) or genuine supplier
    catalogues."""
    if db is None:  # pragma: no cover
        return
    import re
    patterns = [re.compile(p, re.IGNORECASE) for p in _DEV_TEST_FILENAME_PATTERNS]
    uploads = await db.ke_uploads.find(
        {"demo_seed": {"$ne": True}}
    ).to_list(500)
    ids_to_drop: list[str] = []
    for u in uploads:
        name = (u.get("filename") or "").strip()
        if not name:
            continue
        if any(p.match(name) for p in patterns):
            ids_to_drop.append(u["id"])
    if not ids_to_drop:
        return
    rec_res = await db.ke_records.delete_many({"upload_id": {"$in": ids_to_drop}})
    up_res = await db.ke_uploads.delete_many({"id": {"$in": ids_to_drop}})
    logger.info("Purged %d dev-test uploads (%d records)",
                up_res.deleted_count, rec_res.deleted_count)





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

# Canonical material family (from intelligence.family.to_canonical) → the
# catalogue category buckets that actually stock that family. Used by the
# Brain to widen `allowed_categories` when the analyser's material_type
# free-text names a family that the picked material_family doesn't cover.
# Additive only — never used to exclude categories.
_CATEGORIES_FOR_FAMILY = {
    "Paint":     ["Paints"],
    "Laminate":  ["Laminates", "Veneers"],
    "Veneer":    ["Veneers", "Laminates"],
    "Wood":      ["Veneers", "Laminates", "Furniture"],
    "Tile":      ["Tiles", "Stone"],
    "Stone":     ["Stone", "Tiles"],
    "Fabric":    ["Fabric"],
    "Metal":     ["Hardware"],
    "Wallpaper": ["Wallpaper", "Paints"],
    "Ceramic":   ["Tiles", "Stone"],
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
    catalogue matching. Idempotent and pure — no side effects.

    Sprint 6 — object-aware category gating. When the row carries an
    `object_type` (populated by the object-aware region analysis), we
    OVERRIDE the legacy zone-driven routing so a kitchen cabinet or
    wardrobe never falls back to Paint just because its surface is a
    flat colour."""
    classification = row.get("classification") or _classify_row(row)
    mtype_l = str(row.get("material_type") or "").lower()
    fam = str(row.get("material_family") or "").strip()

    # Sprint 6 — object-aware routing runs FIRST. If we know what
    # object was selected, that dictates the compatible categories.
    object_type = str(row.get("object_type") or "").strip().lower()
    _CABINETRY_OBJECTS = {
        "kitchen cabinet", "wardrobe", "tv unit", "vanity",
        "built-in shelf", "cabinetry",
    }
    _COUNTERTOP_OBJECTS = {"countertop", "counter top", "worktop"}
    _BACKSPLASH_OBJECTS = {"backsplash", "splashback"}
    _SOFA_OBJECTS = {"sofa", "settee", "couch"}
    _UPHOLSTERED_FURNITURE = {"chair", "bed", "headboard"}
    _HARD_WALL_OBJECTS = {"feature panel", "wall panel"}
    # Sprint 8 — architectural painted surfaces. When the object-aware
    # classifier says the region is a plain wall / ceiling / false ceiling
    # trim, we route by canonical DNA family. Paint → Paints, wood-clad
    # → Laminates/Veneers, wallpaper → Wallpaper. This bypasses the
    # `_application_context` heuristic that was mis-classifying "Wall
    # beside the bed" as "furniture body" because of the word "bed".
    _ARCH_PAINTED_SURFACES = {"wall", "ceiling", "false_ceiling", "false ceiling"}

    is_pu_painted = "pu" in mtype_l or "puf" in mtype_l or "painted" in mtype_l

    if object_type in _ARCH_PAINTED_SURFACES:
        from intelligence.family import to_canonical
        canon = to_canonical(fam)
        if canon == "Paint":
            allowed = ["Paints"]
        elif canon in {"Laminate", "Veneer", "Wood"}:
            allowed = ["Laminates", "Veneers", "Wallpaper"]
        elif canon == "Wallpaper":
            allowed = ["Wallpaper", "Paints"]
        elif canon == "Tile":
            allowed = ["Tiles", "Stone"]
        elif canon == "Stone":
            allowed = ["Stone", "Tiles"]
        else:
            # Unknown / Other — wall & ceiling surfaces are
            # overwhelmingly paint in practice.  Round 7 fix for the
            # founder-reported "wall with Other/undefined family
            # confidently matched an 80% laminate" bug: default to
            # Paints ONLY so an unknown wall can't cross-match into
            # laminates via the DNA classifier's alt suggestions.
            # The self-consistency widen below can still promote
            # Laminates in when the analyser's own `material_type`
            # free-text explicitly names laminate/wood/veneer/etc.
            allowed = ["Paints"]
        # Sprint 8.2 — self-consistency widen: when the analyser's own
        # `material_type` free-text names a canonical family that the
        # picked family didn't cover (e.g. classifier said material_type=
        # "ceramic tile" but material_family collapsed to Paint because the
        # crop looked plain), we ADD that family's categories to the
        # allowed pool. Doesn't commit to it — retrieval + rerank still
        # decide the winner, but the correct family is at least in the
        # search pool. Purely additive; can never subtract Paints/etc.
        mtype_canon = to_canonical(mtype_l)
        if mtype_canon and mtype_canon != canon:
            for extra in _CATEGORIES_FOR_FAMILY.get(mtype_canon, ()):
                if extra not in allowed:
                    allowed.append(extra)
        excluded = sorted(set(CATEGORY_SETS.keys()) - set(allowed))
        return {
            "classification": classification,
            "app_context": "ceiling" if "ceiling" in object_type else "wall paint",
            "detected_finish": _visual_reference_label(row) or (row.get("material_type") or "Detected material"),
            "likely_family": _likely_family_label(row),
            "allowed_categories": allowed,
            "allowed_libraries": [_LIBRARY_LABELS.get(c, c) for c in allowed],
            "excluded_libraries": [_LIBRARY_LABELS.get(c, c) for c in excluded],
            "ranking_weights": _RANKING_WEIGHTS.get(allowed[0], _RANKING_WEIGHTS["_default"]),
            "object_aware": True,
            # 2026-02-27 (round 7) — object_locked signals to
            # `_find_catalogue_matches` that this routing came from
            # high-confidence object-aware detection, so the low-DNA-
            # confidence "widen alts" heuristic must NOT fire.  Fixes
            # matte-wall-paint being confidently matched against a
            # laminate at 88%.
            "object_locked": True,
        }

    if object_type in _CABINETRY_OBJECTS:
        # Cabinetry is almost always laminate / veneer / acrylic panel.
        # Only allow Paints when the analyser explicitly identified a
        # PU-painted finish (the material_type / finish will say so).
        # Sprint 8 — cane-look wardrobe inserts. The catalogue stocks
        # "LINEN JUTE" as BOTH Laminate (EJ-8026) and Fabric (VT-8026),
        # and cane-print wallpapers are increasingly used on cabinetry.
        # When vision-DNA classifies the panel as Fabric or Wallpaper
        # (both plausible for a woven-texture crop), we broaden the pool
        # so the semantically-correct catalogue entry can surface.
        from intelligence.family import to_canonical
        canon_fam = to_canonical(fam)
        allowed = ["Laminates", "Veneers"]
        if canon_fam == "Fabric":
            allowed.append("Fabric")
        if canon_fam == "Wallpaper":
            allowed.append("Wallpaper")
        if is_pu_painted:
            allowed.append("Paints")
        detected_finish = _visual_reference_label(row) or (row.get("material_type") or "Detected material")
        likely_family = _likely_family_label(row)
        excluded = sorted(set(CATEGORY_SETS.keys()) - set(allowed))
        weights = _RANKING_WEIGHTS.get(allowed[0], _RANKING_WEIGHTS["_default"])
        return {
            "classification": classification,
            "app_context": "cabinet",
            "detected_finish": detected_finish,
            "likely_family": likely_family,
            "allowed_categories": allowed,
            "allowed_libraries": [_LIBRARY_LABELS.get(c, c) for c in allowed],
            "excluded_libraries": [_LIBRARY_LABELS.get(c, c) for c in excluded],
            "ranking_weights": weights,
            "object_aware": True,
            "object_locked": True,
        }
    if object_type in _COUNTERTOP_OBJECTS:
        # Sprint 8.1 — respect the canonical vision-DNA family when it
        # contradicts the classifier's "countertop" object guess. A bench
        # cushion mislabelled as countertop should still route to Fabric,
        # not Stone. Genuine countertops keep the Stone/Tiles/Laminates
        # default.
        from intelligence.family import to_canonical
        canon_fam = to_canonical(fam)
        base = ["Stone", "Tiles", "Laminates"]
        if canon_fam == "Fabric":
            allowed = ["Fabric"] + base
        elif canon_fam == "Paint":
            allowed = ["Paints"] + base
        elif canon_fam in {"Wood", "Veneer"}:
            allowed = ["Laminates", "Veneers", "Stone", "Tiles"]
        else:
            allowed = base
        excluded = sorted(set(CATEGORY_SETS.keys()) - set(allowed))
        return {
            "classification": classification,
            "app_context": "countertop",
            "detected_finish": _visual_reference_label(row) or (row.get("material_type") or "Detected material"),
            "likely_family": _likely_family_label(row),
            "allowed_categories": allowed,
            "allowed_libraries": [_LIBRARY_LABELS.get(c, c) for c in allowed],
            "excluded_libraries": [_LIBRARY_LABELS.get(c, c) for c in excluded],
            "ranking_weights": _RANKING_WEIGHTS.get(allowed[0], _RANKING_WEIGHTS["_default"]),
            "object_aware": True,
            "object_locked": True,
        }
    if object_type in _BACKSPLASH_OBJECTS:
        allowed = ["Tiles", "Stone", "Laminates"]
        excluded = sorted(set(CATEGORY_SETS.keys()) - set(allowed))
        return {
            "classification": classification,
            "app_context": "backsplash",
            "detected_finish": _visual_reference_label(row) or (row.get("material_type") or "Detected material"),
            "likely_family": _likely_family_label(row),
            "allowed_categories": allowed,
            "allowed_libraries": [_LIBRARY_LABELS.get(c, c) for c in allowed],
            "excluded_libraries": [_LIBRARY_LABELS.get(c, c) for c in excluded],
            "ranking_weights": _RANKING_WEIGHTS.get(allowed[0], _RANKING_WEIGHTS["_default"]),
            "object_aware": True,
            "object_locked": True,
        }
    if object_type in _SOFA_OBJECTS or object_type in _UPHOLSTERED_FURNITURE:
        # Sprint 7.1 — headboards / beds / chairs can be upholstered fabric
        # OR wood / laminate slat panels. Use the canonical DNA family (set
        # via _reconcile_family_with_vision_dna) as the tie-breaker so a
        # wood-slat headboard doesn't get routed to Fabric-only.
        from intelligence.family import to_canonical
        canon = to_canonical(fam)
        if object_type in _SOFA_OBJECTS or canon == "Fabric":
            allowed = ["Fabric"]
        elif canon in {"Laminate", "Veneer", "Wood"}:
            allowed = ["Laminates", "Veneers", "Fabric"]
        elif canon == "Metal":
            allowed = ["Hardware", "Fabric"]
        else:
            allowed = ["Fabric"]
        excluded = sorted(set(CATEGORY_SETS.keys()) - set(allowed))
        return {
            "classification": classification,
            "app_context": "upholstery",
            "detected_finish": _visual_reference_label(row) or (row.get("material_type") or "Detected material"),
            "likely_family": _likely_family_label(row),
            "allowed_categories": allowed,
            "allowed_libraries": [_LIBRARY_LABELS.get(c, c) for c in allowed],
            "excluded_libraries": [_LIBRARY_LABELS.get(c, c) for c in excluded],
            "ranking_weights": _RANKING_WEIGHTS.get(allowed[0], _RANKING_WEIGHTS["_default"]),
            "object_aware": True,
            "object_locked": True,
        }
    if object_type in _HARD_WALL_OBJECTS:
        allowed = ["Laminates", "Veneers", "Tiles", "Stone"]
        excluded = sorted(set(CATEGORY_SETS.keys()) - set(allowed))
        return {
            "classification": classification,
            "app_context": "feature wall",
            "detected_finish": _visual_reference_label(row) or (row.get("material_type") or "Detected material"),
            "likely_family": _likely_family_label(row),
            "allowed_categories": allowed,
            "allowed_libraries": [_LIBRARY_LABELS.get(c, c) for c in allowed],
            "excluded_libraries": [_LIBRARY_LABELS.get(c, c) for c in excluded],
            "ranking_weights": _RANKING_WEIGHTS.get(allowed[0], _RANKING_WEIGHTS["_default"]),
            "object_aware": True,
            "object_locked": True,
        }

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

    # Round 8 (retest) — object_locked signals to
    # `_find_catalogue_matches` that this row's category has been
    # confidently routed by application-context (not just a generic
    # "let the LLM decide" fallback), so the low-DNA-confidence
    # "widen alts" heuristic must NOT fire.  Fixes the founder-
    # reported "Tile floor confidently matched Laminate at 79%" case
    # (bug testing agent iteration 21) — the flooring branch here
    # legitimately searches Tiles+Stone OR Laminates+Veneers, but
    # never wants a Tile-classified floor to widen into Laminates
    # via family_alternatives.
    _OBJECT_LOCKED_CONTEXTS = {
        "wall paint", "kitchen wall", "flooring", "countertop",
        "backsplash", "bathroom wet wall", "curtain", "rug", "bedding",
        "furniture upholstery", "lighting fixture", "hardware",
        "feature wall", "headboard wall",
    }
    object_locked = app_ctx in _OBJECT_LOCKED_CONTEXTS

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
        "object_locked": object_locked,
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
        "best": best[:3],
        "possible": possible,
        "low": low,
        "counts": {"best": len(best), "possible": len(possible), "low": len(low)},
    }


# ---------------------------------------------------------------------------
# Scene-mode helpers for `/analyze-region` (2026-07 hybrid pipeline).
#
# The hybrid pipeline validated at 96.5% material plausibility in
# `/api/admin/test-scene-segmentation` is now the front-end of the
# user-facing analyze flow for multi-object scene uploads.  Single-swatch
# pre-cropped queries still take the original per-crop path.
# ---------------------------------------------------------------------------

# SAM3 object label → (group, object_group) used by downstream classify /
# enrichment / palette code.  Values chosen to align with the DNA
# classifier vocabulary so the rest of the stack sees the same fields
# regardless of which entry-point (full-image spec or manual region
# select) drove the classification.
_SCENE_OBJECT_META: dict[str, tuple[str, str]] = {
    "wall":       ("Wall",      "Architectural Surface"),
    "ceiling":    ("Ceiling",   "Architectural Surface"),
    "floor":      ("Floor",     "Architectural Surface"),
    "backsplash": ("Wall",      "Architectural Surface"),
    "cabinet":    ("Furniture", "Built-in Element"),
    "countertop": ("Furniture", "Built-in Element"),
    "shelf":      ("Furniture", "Built-in Element"),
    "nightstand": ("Furniture", "Furniture"),
    "sofa":       ("Furniture", "Furniture"),
    "bed":        ("Furniture", "Furniture"),
    "headboard":  ("Furniture", "Furniture"),
    "curtain":    ("Furniture", "Furniture"),
    "rug":        ("Floor",     "Furniture"),
    "mirror":     ("Furniture", "Fixture"),
    "sink":       ("Furniture", "Fixture"),
    "faucet":     ("Furniture", "Fixture"),
    "toilet":     ("Furniture", "Fixture"),
    "bathtub":    ("Furniture", "Fixture"),
    # 2026-02-01 round 10 — dining / office vocab additions to
    # `ARCHITECTURAL_VOCAB`. Grouped so the downstream group / palette
    # code categorises them consistently with the rest of the furniture.
    "dining table": ("Furniture", "Furniture"),
    "chair":        ("Furniture", "Furniture"),
    "desk":         ("Furniture", "Furniture"),
    "office chair": ("Furniture", "Furniture"),
}


def _dna_to_row(
    dna: dict,
    object_label: str,
    object_confidence: float,
    index: int,
    bbox: list | None,
    polygon: list | None,
    source: str,
    image_size: tuple[int, int] | None = None,
) -> dict:
    """Convert a Stage-B DNA dict + Stage-A object metadata into a row
    dict compatible with the existing rerank / catalogue-match pipeline.

    Args:
        dna:               DNA dict as returned by `generate_swatch_dna`
                           (or a shortcut dict from `DETERMINISTIC_MATERIAL`).
        object_label:      SAM3 label ("wall" / "cabinet" / ...).
        object_confidence: Stage-A confidence.
        index:             enumeration index — used to build a stable zone
                           string so the UI can order rows deterministically.
        bbox:              [x, y, w, h] in ORIGINAL image pixels.
        polygon:           SAM3 polygon (list of {x, y}) in ORIGINAL image
                           pixels; carried through so the UI can render an
                           overlay.
        source:            "dna" or "shortcut" — recorded on the row for
                           debugging / audit.
        image_size:        Original scene (W, H) in pixels — used to
                           derive a percent-based `pin` from the bbox
                           centre so the UI can render numbered pins
                           consistently on every scene-mode result
                           (fixes founder-reported "some results have
                           0 pins" bug).
    """
    label = (object_label or "").strip().lower() or "unknown"
    group, object_group = _SCENE_OBJECT_META.get(
        label, ("Furniture", "Furniture")
    )
    pc = (dna.get("primary_color") or {}) if isinstance(dna, dict) else {}
    color_name = pc.get("name") or ""
    color_hex = pc.get("hex") or ""
    material_family = str(dna.get("material_family") or "").strip().title()
    surface_type = dna.get("surface_type") or ""
    canonical = dna.get("canonical_description") or ""

    zone = f"{label.title()} · region {index + 1}"
    material_type = (surface_type or canonical or material_family).strip()[:120]

    # Keywords from DNA — used by _classify_row and application priors.
    keywords: list[str] = []
    for k in (
        material_family, surface_type, dna.get("pattern"), dna.get("finish"),
        dna.get("gloss_level"), dna.get("texture"),
    ):
        if not k:
            continue
        for tok in re.split(r"[^a-z0-9]+", str(k).lower()):
            if len(tok) > 2 and tok not in keywords:
                keywords.append(tok)
    keywords = keywords[:6]

    # Confidence — derive from object confidence for now; the visual
    # reranker will overwrite `visual_rerank_confidence` afterwards.
    conf_pct = int(round(float(object_confidence) * 100))
    conf_pct = max(0, min(100, conf_pct))

    # 2026-02-27 — deterministic pin from bbox / polygon centroid.
    # Scene-mode rows ALWAYS have a bbox from SAM3, so every scene row
    # can render a pin.  When a polygon is present we prefer the
    # polygon CENTROID (arithmetic mean of vertices) over the bbox
    # centre — for large flat surfaces like rugs / floors where objects
    # commonly sit on the material, the polygon centroid tends to fall
    # on visible material rather than on an object placed on top.
    # Round 8 fix for the founder-reported "rug pin landed on a book
    # placed on the rug" issue.
    pin: dict | None = None
    pin_source: str | None = None
    if image_size and image_size[0] and image_size[1]:
        W, H = float(image_size[0]), float(image_size[1])
        cx_px = cy_px = None
        # Prefer polygon centroid when we have one (>= 3 vertices).
        if polygon and isinstance(polygon, list) and len(polygon) >= 3:
            try:
                xs = [float(pt.get("x")) for pt in polygon if isinstance(pt, dict)]
                ys = [float(pt.get("y")) for pt in polygon if isinstance(pt, dict)]
                if len(xs) >= 3 and len(ys) >= 3:
                    cx_px = sum(xs) / len(xs)
                    cy_px = sum(ys) / len(ys)
            except (TypeError, ValueError):
                cx_px = cy_px = None
        # Fall back to bbox centre.
        if (cx_px is None or cy_px is None) and bbox:
            try:
                bx, by, bw, bh = [float(v) for v in bbox]
                cx_px = bx + bw / 2.0
                cy_px = by + bh / 2.0
            except (TypeError, ValueError):
                cx_px = cy_px = None
        if cx_px is not None and cy_px is not None:
            cx_pct = (cx_px / W) * 100.0
            cy_pct = (cy_px / H) * 100.0
            if 0 <= cx_pct <= 100 and 0 <= cy_pct <= 100:
                pin = {"x": round(cx_pct, 1), "y": round(cy_pct, 1)}
                pin_source = ("scene_polygon_centroid"
                              if polygon and len(polygon) >= 3
                              else "scene_bbox")

    row: dict = {
        "zone": zone,
        "group": group,
        "object_group": object_group,
        "object_type": label,
        "surface_description": canonical or material_type,
        "material_family": material_family or "Other",
        # 2026-07 — expose family ambiguity to retrieval so a flat cabinet-
        # door crop classified as "Paint" but truly a laminate can still
        # find the correct laminate entry.  See dna.py + retrieval.py.
        "family_confidence": float(dna.get("family_confidence") or 1.0)
                             if isinstance(dna, dict) else 1.0,
        "family_alternatives": list(dna.get("family_alternatives") or [])
                               if isinstance(dna, dict) else [],
        "material_type": material_type,
        "color": color_name,
        "color_hex": color_hex,
        "texture": dna.get("texture") or "",
        "finish": dna.get("finish") or "",
        "pattern": dna.get("pattern") or "",
        "gloss_level": dna.get("gloss_level") or "",
        "design_style": "",
        "keywords": keywords,
        "confidence": conf_pct,
        "object_confidence": conf_pct,
        "material_confidence": conf_pct,
        # Numbered pin (bbox-centre) — consumed by the RegionSelector
        # overlay on the reference image.
        "pin": pin,
        "pin_source": pin_source,
        # Scene-mode extras — visible in the response so the UI can draw
        # the overlay without a second server round-trip.
        "scene_bbox": bbox,
        "scene_polygon": polygon,
        "scene_source": source,
    }
    row["classification"] = _classify_row(row)
    return row


async def run_scene_region_analysis(
    project_id: str, user_id: str,
    scene_b64: str,
    region: str = None,
) -> tuple[dict, dict[int, str]]:
    """Hybrid scene analysis: SAM3 Stage-A object detection followed by
    polygon-masked GPT-4o-mini material classification on each detected
    object.  Returns `(result_dict, per_object_crop_b64)` where
    `per_object_crop_b64[i]` is the polygon-masked crop for row i, used
    by the caller for visual reranking against the retrieved shortlist.

    This is the same pipeline validated at 96.5% material plausibility
    across the March-2026 31-image benchmark — see `/tmp/HYBRID_REPORT_V2.md`.
    """
    import asyncio as _asyncio
    from PIL import Image as _Image
    from intelligence.scene_segmentation import (
        ARCHITECTURAL_VOCAB, LABEL_MIN_CONFIDENCE, Sam3Error,
        classify_object_material, detect_objects, filter_detections,
        _apply_polygon_mask, _crop_to_base64, _crop_to_bbox,
    )
    if region is None:
        region = DEFAULT_REGION

    # 1) Load scene image (base64 payload can be huge; decode once, share
    #    the PIL handle between Stage A and Stage B).
    raw = base64.b64decode(scene_b64)
    scene_img = _Image.open(io.BytesIO(raw)).convert("RGB")
    W, H = scene_img.size

    # 2) Stage A — SAM3 architectural-object detection with the same
    #    filter settings the admin validation tool uses.
    obj_raw = detect_objects(scene_img, vocab=ARCHITECTURAL_VOCAB)
    objects = filter_detections(
        obj_raw, min_confidence=0.55, min_area_frac=0.003,
        image_w=W, image_h=H,
        label_min_confidence=LABEL_MIN_CONFIDENCE,
    )
    logger.info(
        "[scene %s] SAM3 stage-A: raw=%d kept=%d image=%dx%d",
        project_id, len(obj_raw), len(objects), W, H,
    )

    # 3) Stage B — per-object polygon-masked DNA classification.  Run in
    #    parallel; each call is network-bound so gather cuts wall-clock.
    #
    # 2026-02-05 (round 6) — for merge-clustered detections, crop from
    # the ANCHOR'S ORIGINAL bbox rather than the hull. The hull can be
    # a wide-thin strip (e.g. a whole cabinet run) that makes the DNA
    # classifier see a horizontal band and misclassify as Paint. The
    # anchor bbox is the single highest-confidence per-door detection
    # from the cluster — a clean tight crop the classifier can reason
    # about accurately. Non-merged detections have no `anchor_bbox`,
    # so they crop from their own bbox as before (unchanged).
    stage_b_tasks = [
        classify_object_material(
            scene_img,
            obj.get("anchor_bbox") or obj["bbox"],
            obj["label"], EMERGENT_LLM_KEY,
            polygon=obj.get("polygon"),
            object_confidence=float(obj.get("confidence", 0.0)),
        )
        for obj in objects
    ]
    stage_b_results = await _asyncio.gather(*stage_b_tasks, return_exceptions=True)

    # 4) Build one row per object with a usable material result; also
    #    encode the polygon-masked crop so downstream rerank has a clean
    #    per-object view.
    rows: list[dict] = []
    per_object_crops: dict[int, str] = {}
    skipped = 0
    errored = 0
    for obj, res in zip(objects, stage_b_results):
        if isinstance(res, Exception):
            logger.warning("[scene %s] stage-B exception on %s: %r",
                           project_id, obj.get("label"), res)
            errored += 1
            continue
        src = res.get("source") if isinstance(res, dict) else None
        if src in (None, "error"):
            errored += 1
            continue
        if src == "skipped":
            skipped += 1
            continue
        mat = res.get("material") or {}
        row_index = len(rows)
        row = _dna_to_row(
            dna=mat,
            object_label=obj["label"],
            object_confidence=float(obj.get("confidence", 0.0)),
            index=row_index,
            bbox=obj.get("bbox"),
            polygon=obj.get("polygon"),
            source=src,
            image_size=(W, H),
        )
        rows.append(row)

        # Build the polygon-masked crop for downstream rerank — same
        # crop the DNA classifier saw, so we're comparing apples-to-apples
        # against the catalogue swatch it was matched against.
        # 2026-02-05 (round 6) — matches the Stage-B crop above: use
        # anchor_bbox when the detection is a merge cluster so the
        # rerank crop is a tight per-door crop, not the wide hull.
        try:
            crop_bbox = obj.get("anchor_bbox") or obj["bbox"]
            crop, (x0, y0, x1, y1) = _crop_to_bbox(scene_img, list(crop_bbox))
            if obj.get("polygon") and len(obj["polygon"]) >= 3:
                crop = _apply_polygon_mask(crop, obj["polygon"], (x0, y0))
            per_object_crops[row_index] = _crop_to_base64(crop)
        except Sam3Error:
            # Bbox intersect failure — leave crop unset; rerank will just
            # fall back to text scoring for this row.
            pass

    palette = list({
        (r.get("color") or "").strip().title()
        for r in rows if r.get("color")
    })[:6]

    result = {
        "rows": rows,
        "summary": {
            "design_style": "",
            "material_palette": ", ".join(palette),
            "key_finishes": "",
            "sourcing_note": (
                f"Detected {len(objects)} object(s), {len(rows)} with usable "
                f"materials ({skipped} skipped, {errored} errored)."
            ),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": "region-scene-hybrid-v1",
        "scene_stage_a": {
            "raw": len(obj_raw), "kept": len(objects),
            "with_material": len(rows), "skipped": skipped, "errored": errored,
            "image_size": {"width": W, "height": H},
            # 2026-02-27 (round 5) — expose the SAM3 detections themselves
            # so the products-pipeline hookup can attach real bbox-derived
            # pins to detected products.  Includes skipped labels (cushion
            # / pillow / plant / mattress) — those never make it into
            # `rows` but ARE valid product anchors on the image.
            "objects": [
                {"label": o.get("label"),
                 "confidence": float(o.get("confidence", 0)),
                 "bbox": o.get("bbox")}
                for o in objects
                if isinstance(o.get("bbox"), (list, tuple)) and len(o.get("bbox")) == 4
            ],
        },
    }
    return result, per_object_crops




async def run_consolidated_region_analysis(
    project_id: str, user_id: str,
    crop_b64: str,
    full_b64: str | None,
    bbox_pct: list | None,
    region: str = None,
) -> tuple[dict, dict[int, str]]:
    """Analyse a user-selected region through the SAME classification path
    the full-image scene flow uses (SAM3 → `classify_object_material` →
    `generate_swatch_dna`).

    This is the foundational-trust consolidation (2026-02-01 P0 fix):
    before this function existed, `analyze-region` routed through a
    separate `run_object_aware_region_analysis` LLM prompt (since
    deleted) that could disagree with the scene pipeline on the same
    wall region (paint vs plaster). Now both flows call the same DNA
    classifier on the same polygon-masked crop, so cross-flow
    disagreement is structurally impossible.

    Strategy:
      1. If `full_b64` + `bbox_pct` are provided (normal case for the
         Analysis-page rectangle selector), run SAM3 Stage-A on the FULL
         reference image — the same detection call `run_scene_region_
         analysis` makes.
      2. Find the SAM3 object whose bbox has the highest IoU with the
         user's drawn rectangle. If IoU ≥ MIN_IOU_MATCH, use THAT
         object's polygon-masked crop for classification (a real
         architectural anchor). Emits ONE row for that matched object.
      3. If no SAM3 match (user drew on empty space, or on an object
         SAM3 missed), fall through to `classify_object_material` on
         the user's raw rectangular crop with label='unknown' — same
         DNA prompt, no polygon mask.
      4. If `full_b64` is absent (older callers, tests), classify the
         crop directly with label='unknown'.

    Returns `(result_dict, per_object_crops)` in the same shape as
    `run_scene_region_analysis` so all downstream enrichment (family
    reconcile / catalogue enrich / visual rerank) works untouched.
    """
    import asyncio as _asyncio
    from PIL import Image as _Image
    from intelligence.scene_segmentation import (
        ARCHITECTURAL_VOCAB, LABEL_MIN_CONFIDENCE, Sam3Error,
        classify_object_material, detect_objects, filter_detections,
        _apply_polygon_mask, _crop_to_base64, _crop_to_bbox,
    )
    if region is None:
        region = DEFAULT_REGION

    MIN_IOU_MATCH = 0.30  # user's rect must overlap at least 30% with
                          # a SAM3 object to be considered a match.

    def _bbox_iou(a: list, b: list) -> float:
        """IoU of two pixel bboxes in [x, y, w, h] form."""
        ax0, ay0, aw, ah = float(a[0]), float(a[1]), float(a[2]), float(a[3])
        bx0, by0, bw, bh = float(b[0]), float(b[1]), float(b[2]), float(b[3])
        ax1, ay1 = ax0 + aw, ay0 + ah
        bx1, by1 = bx0 + bw, by0 + bh
        ix0, iy0 = max(ax0, bx0), max(ay0, by0)
        ix1, iy1 = min(ax1, bx1), min(ay1, by1)
        iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
        inter = iw * ih
        union = aw * ah + bw * bh - inter
        return inter / union if union > 0 else 0.0

    # -----------------------------------------------------------------
    # Case A: full image + bbox present → SAM3 + object-match path.
    # -----------------------------------------------------------------
    matched_obj: dict | None = None
    scene_img: "_Image.Image | None" = None
    sam3_objects_summary: list[dict] = []

    if full_b64 and bbox_pct and len(bbox_pct) == 4:
        try:
            raw = base64.b64decode(full_b64)
            scene_img = _Image.open(io.BytesIO(raw)).convert("RGB")
            W, H = scene_img.size

            # Convert user's percent bbox → pixels.
            u_x = float(bbox_pct[0]) / 100.0 * W
            u_y = float(bbox_pct[1]) / 100.0 * H
            u_w = float(bbox_pct[2]) / 100.0 * W
            u_h = float(bbox_pct[3]) / 100.0 * H
            user_bbox_px = [u_x, u_y, u_w, u_h]

            # SAM3 Stage-A — identical call to the full-image scene flow.
            obj_raw = detect_objects(scene_img, vocab=ARCHITECTURAL_VOCAB)
            objects = filter_detections(
                obj_raw, min_confidence=0.55, min_area_frac=0.003,
                image_w=W, image_h=H,
                label_min_confidence=LABEL_MIN_CONFIDENCE,
            )
            sam3_objects_summary = [
                {"label": o.get("label"),
                 "confidence": float(o.get("confidence", 0)),
                 "bbox": o.get("bbox")}
                for o in objects
                if isinstance(o.get("bbox"), (list, tuple)) and len(o.get("bbox")) == 4
            ]

            # Find best-IoU object.
            best_iou = 0.0
            best_obj: dict | None = None
            for obj in objects:
                b = obj.get("bbox")
                if not (isinstance(b, (list, tuple)) and len(b) == 4):
                    continue
                iou = _bbox_iou(user_bbox_px, list(b))
                if iou > best_iou:
                    best_iou = iou
                    best_obj = obj
            if best_obj is not None and best_iou >= MIN_IOU_MATCH:
                matched_obj = best_obj
                logger.info(
                    "[region %s] matched SAM3 object %r conf=%.2f iou=%.2f "
                    "-> routing through scene DNA path.",
                    project_id, best_obj.get("label"),
                    float(best_obj.get("confidence", 0)), best_iou,
                )
            else:
                logger.info(
                    "[region %s] no SAM3 object matched user bbox (best_iou=%.2f, "
                    "candidates=%d) — falling back to raw-crop DNA.",
                    project_id, best_iou, len(objects),
                )
        except Sam3Error as e:
            logger.warning("[region %s] SAM3 stage-A failed (%s) — "
                           "falling back to raw-crop DNA.", project_id, e)
            scene_img = None
        except Exception as e:  # noqa: BLE001
            logger.warning("[region %s] full-image decode/SAM3 error (%s: %s) — "
                           "falling back to raw-crop DNA.",
                           project_id, type(e).__name__, e)
            scene_img = None

    # -----------------------------------------------------------------
    # Case A-matched: classify the SAM3-matched object's polygon-masked
    # crop through the same DNA path the scene flow uses.
    # -----------------------------------------------------------------
    if matched_obj is not None and scene_img is not None:
        res = await classify_object_material(
            scene_img, matched_obj["bbox"], matched_obj["label"], EMERGENT_LLM_KEY,
            polygon=matched_obj.get("polygon"),
            object_confidence=float(matched_obj.get("confidence", 0.0)),
        )
        rows: list[dict] = []
        per_object_crops: dict[int, str] = {}
        src = res.get("source") if isinstance(res, dict) else None
        if src in ("dna", "shortcut") and res.get("material"):
            row = _dna_to_row(
                dna=res["material"],
                object_label=matched_obj["label"],
                object_confidence=float(matched_obj.get("confidence", 0.0)),
                index=0,
                bbox=matched_obj.get("bbox"),
                polygon=matched_obj.get("polygon"),
                source=src,
                image_size=scene_img.size,
            )
            rows.append(row)
            # Build the polygon-masked crop the DNA classifier saw so
            # downstream visual rerank compares apples-to-apples.
            try:
                crop, (x0, y0, x1, y1) = _crop_to_bbox(
                    scene_img, list(matched_obj["bbox"]))
                if matched_obj.get("polygon") and len(matched_obj["polygon"]) >= 3:
                    crop = _apply_polygon_mask(
                        crop, matched_obj["polygon"], (x0, y0))
                per_object_crops[0] = _crop_to_base64(crop)
            except Sam3Error:
                pass
        result = {
            "rows": rows,
            "summary": {
                "design_style": "",
                "material_palette": (rows[0].get("color") or "").title() if rows else "",
                "key_finishes": "",
                "sourcing_note": (
                    f"Matched SAM3 {matched_obj['label']!r} at IoU with your selection; "
                    "same DNA classifier the full-image spec uses."
                ),
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "version": "region-consolidated-sam3-dna-v1",
            "scene_stage_a": {
                "raw": len(sam3_objects_summary), "kept": len(sam3_objects_summary),
                "with_material": len(rows), "skipped": 0,
                "errored": 0 if rows else 1,
                "image_size": {"width": scene_img.size[0], "height": scene_img.size[1]},
                "objects": sam3_objects_summary,
                "matched_object": {
                    "label": matched_obj["label"],
                    "confidence": float(matched_obj.get("confidence", 0.0)),
                    "bbox": matched_obj.get("bbox"),
                },
            },
        }
        return result, per_object_crops

    # -----------------------------------------------------------------
    # Case B: no SAM3 match (either no full-image, decode failure, or
    # zero-IoU). Classify the user's raw rectangular crop directly
    # through classify_object_material with label='unknown'. Same DNA
    # prompt, no polygon mask.
    # -----------------------------------------------------------------
    try:
        raw_crop = base64.b64decode(crop_b64)
        crop_img = _Image.open(io.BytesIO(raw_crop)).convert("RGB")
        cW, cH = crop_img.size
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400,
                            detail=f"Selected crop is not a valid image: {e}")

    # Full-crop bbox in the crop's OWN coordinate space so
    # `classify_object_material` produces sensible crop_origin fields.
    whole_bbox = [0.0, 0.0, float(cW), float(cH)]
    res = await classify_object_material(
        crop_img, whole_bbox, "unknown", EMERGENT_LLM_KEY,
        polygon=None, object_confidence=1.0,
    )
    src = res.get("source") if isinstance(res, dict) else None
    rows_b: list[dict] = []
    per_object_crops_b: dict[int, str] = {}
    if src in ("dna", "shortcut") and res.get("material"):
        row = _dna_to_row(
            dna=res["material"],
            object_label="unknown",
            object_confidence=1.0,
            index=0,
            bbox=whole_bbox,
            polygon=None,
            source=src,
            image_size=(cW, cH),
        )
        # Blank the pin — for raw-crop classification we don't have a
        # meaningful anchor in the FULL reference image (the caller
        # passed only the pre-cropped rectangle).
        row["pin"] = None
        row["pin_source"] = None
        rows_b.append(row)
        per_object_crops_b[0] = crop_b64

    result = {
        "rows": rows_b,
        "summary": {
            "design_style": "",
            "material_palette": (rows_b[0].get("color") or "").title() if rows_b else "",
            "key_finishes": "",
            "sourcing_note": (
                "Classified the raw selected crop with the same DNA classifier "
                "the full-image spec uses (no matching SAM3 object found)."
            ),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": "region-consolidated-rawcrop-dna-v1",
        "scene_stage_a": {
            "raw": len(sam3_objects_summary),
            "kept": len(sam3_objects_summary),
            "with_material": len(rows_b),
            "skipped": 0,
            "errored": 0 if rows_b else 1,
            "image_size": {"width": cW, "height": cH},
            "objects": sam3_objects_summary,
            "matched_object": None,
        },
    }
    return result, per_object_crops_b




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
        # 2026-07 hybrid pipeline — scene mode.  Runs SAM3 Stage-A object
        # detection on the WHOLE uploaded image, then polygon-masked
        # GPT-4o-mini material classification per object.  Returns one
        # row per material.  This is the pipeline validated at 96.5%
        # material plausibility in the March-2026 batch.
        per_object_crops: dict[int, str] = {}
        scene_mode = str(getattr(payload, "mode", "single") or "single").lower() == "scene"
        if scene_mode:
            result, per_object_crops = await run_scene_region_analysis(
                project_id, user["id"], crop,
                region=user.get("preferred_region", DEFAULT_REGION),
            )
            # Isolated-crop fallback: users often upload a close-up of a
            # single material (a floor sample photo, a swatch they cropped
            # from a supplier PDF).  SAM3 Stage-A won't recognize such a
            # crop as any architectural OBJECT and returns nothing.  In
            # that case, silently fall through to the single-swatch
            # analysis path so the crop still gets a material read.
            if not result.get("rows"):
                logger.info(
                    "[scene %s] Stage-A returned 0 objects — falling back "
                    "to single-swatch analysis on the whole crop.", project_id,
                )
                result = await run_real_analysis(
                    project_id, user["id"], crop,
                    region=user.get("preferred_region", DEFAULT_REGION),
                )
                per_object_crops = {}  # reset — single-swatch path uses `crop`
                scene_mode = False
                result["scene_fallback"] = "single_swatch_no_objects_detected"
        else:
            # 2026-02-01 (P0 consolidation) — the manual "Select area of
            # interest" flow now routes through the SAME SAM3 + DNA
            # classifier the full-image spec flow uses. This eliminates
            # the paint/plaster mismatch where the two flows disagreed on
            # the same wall region because they were calling different
            # LLM prompts. See `run_consolidated_region_analysis` for
            # the strategy (best-IoU SAM3 match on the full image, then
            # raw-crop DNA fallback).
            full_b64 = payload.full_image_b64
            if full_b64 and full_b64.startswith("data:"):
                full_b64 = full_b64.split(",", 1)[-1]
            bbox = payload.bbox if isinstance(payload.bbox, list) and len(payload.bbox) == 4 else None
            result, per_object_crops = await run_consolidated_region_analysis(
                project_id, user["id"], crop,
                full_b64=full_b64, bbox_pct=bbox,
                region=user.get("preferred_region", DEFAULT_REGION),
            )
        # Sprint 6 — attach the perceptual fingerprint of the SELECTED
        # crop to each row so the ranker can do exact-match loopback
        # against published swatches. In scene mode we hash each
        # per-object crop instead of the whole-scene crop, so exact
        # loopback works when a scene photo of a published swatch is
        # uploaded.
        from visual_hash import compute_visual_hashes
        for i, r in enumerate(result.get("rows") or []):
            if r.get("visual_hashes"):
                continue
            hash_source = per_object_crops.get(i) if per_object_crops else crop
            hashes = compute_visual_hashes(hash_source) if hash_source else None
            if hashes:
                r["visual_hashes"] = hashes
        # Sprint 7 — symmetric query-side vision-DNA. The catalogue side
        # was already vision-described at publish time; this closes the
        # asymmetry by running the same describe pass on the user's crop
        # BEFORE retrieval. Also applies the approved family-override
        # rules so generic classifier labels (furniture / flooring / wall)
        # get replaced by canonical DNA families when appropriate.
        # Scene-mode rows ALREADY have vision-DNA attached from the
        # hybrid Stage-B — skip the redundant LLM call and reuse it.
        for i, r in enumerate(result.get("rows") or []):
            if r.get("scene_source") in ("dna", "shortcut"):
                # Rebuild the visual_dna dict from the row fields we
                # already populated, then reconcile the family.
                from intelligence.dna import normalize_dna
                vision_dna = normalize_dna({
                    "material_family": r.get("material_family"),
                    "family_confidence": r.get("family_confidence", 1.0),
                    "family_alternatives": r.get("family_alternatives") or [],
                    "surface_type": r.get("material_type"),
                    "primary_color": {"name": r.get("color", ""),
                                       "hex": r.get("color_hex", "")},
                    "texture": r.get("texture", ""),
                    "pattern": r.get("pattern", ""),
                    "finish": r.get("finish", ""),
                    "gloss_level": r.get("gloss_level", ""),
                    "canonical_description": r.get("surface_description", ""),
                })
                _reconcile_family_with_vision_dna(r, vision_dna)
                continue
            per_row_crop = per_object_crops.get(i) or crop
            vision_dna = await _generate_query_vision_dna(per_row_crop, r)
            if vision_dna:
                _reconcile_family_with_vision_dna(r, vision_dna)
            else:
                r.setdefault("family_routing", {
                    "classifier_family": r.get("material_family"),
                    "vision_family": None,
                    "final_family": r.get("material_family"),
                    "override_applied": False,
                    "reason": "vision_dna_unavailable_fallback_to_text",
                })
        # 2026-02-01 (round 4) — thread library_scope through to
        # retrieval so the manual selector honours the user's explicit
        # admin-vs-own choice per search.
        region_scope = (payload.library_scope or "admin").lower()
        if region_scope not in ("admin", "own"):
            region_scope = "admin"
        region_user_records = (
            await _load_user_catalogue_records(user["id"])
            if region_scope == "own" else None
        )
        _enrich_rows_with_catalogue(
            result.get("rows") or [],
            library_scope=region_scope,
            user_records=region_user_records,
        )
        # Sprint 7 — visual re-rank (Describe-Embed-Rerank stage 5). The
        # user explicitly selected this region, so we spend ONE GPT-4o
        # call comparing the crop against the retrieved shortlist.
        # Skipped automatically on exact pHash loopback hits.
        for i, r in enumerate(result.get("rows") or []):
            try:
                per_row_crop = per_object_crops.get(i) or crop
                # Scene-mode crops are wide-angle polygon-masked views —
                # they'll systematically look different from an isolated
                # catalogue swatch, so rerank rejections DEMOTE (−15 pts)
                # instead of dropping the candidate.  Single-swatch pre-
                # cropped queries keep the original strict behaviour.
                strict = not per_object_crops
                await _apply_visual_rerank(r, per_row_crop, strict=strict)
            except Exception:
                logger.exception("visual rerank failed — keeping retrieval results")
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
async def _sprint6_backfill_visual_hashes() -> None:
    """Sprint 6 — compute pHash / dHash / wHash for every ke_records row
    that has a `page_preview_b64` but no `visual_hashes`. Runs
    idempotently on startup; skips records already hashed. Never touches
    records with missing / degenerate crops."""
    if db is None:  # pragma: no cover
        return
    from visual_hash import compute_visual_hashes
    cursor = db.ke_records.find(
        {"$or": [
            {"visual_hashes": {"$exists": False}},
            {"visual_hashes.avg_rgb": {"$exists": False}},  # Sprint 7 upgrade
         ],
         "page_preview_b64": {"$exists": True, "$ne": None}},
        {"id": 1, "page_preview_b64": 1},
    )
    count = 0
    async for r in cursor:
        h = compute_visual_hashes(r.get("page_preview_b64"))
        if not h:
            continue
        await db.ke_records.update_one(
            {"id": r["id"]}, {"$set": {"visual_hashes": h}},
        )
        count += 1
    if count:
        logger.info("Sprint 6 pHash backfill: hashed %d record(s)", count)


@app.on_event("startup")
async def _sprint6_cleanup_junk_published_records() -> None:
    """Sprint 6 — Sprint 3 page-level fallback produced records with
    material_name = 'Swatch p{page}.s{swatch}' or ALL-CAPS page titles.
    Some of these accidentally got published. Move them BACK to draft so
    they never enter the user search index. Idempotent.

    Rules for un-publish:
      1. name matches "Swatch p<digits>.s<digits>" (Sprint 3 placeholder)
      2. name is UPPERCASE and matches known page-title patterns
         ("...PANELS:", "INTERIOR CEILING PANELS", "ADVANCE PANELS", etc)
      3. is_page_level_fallback is True (Type-B whole-page record)
    """
    if db is None:  # pragma: no cover
        return
    import re
    placeholder_re = re.compile(r"^Swatch\s+p\d+\.s\d+$")
    junk_title_patterns = (
        "ADVANCE PANELS", "INTERIOR CEILING PANELS", "CATALOGUE",
        "SPECIFICATION", "PRICE LIST",
    )
    cleaned = 0
    async for r in db.ke_records.find(
        {"status": "published"},
        {"id": 1, "material_name": 1, "is_page_level_fallback": 1},
    ):
        name = str(r.get("material_name") or "").strip()
        should_unpublish = (
            placeholder_re.match(name) is not None
            or any(name.upper().startswith(p) for p in junk_title_patterns)
            or r.get("is_page_level_fallback") is True
        )
        if should_unpublish:
            await db.ke_records.update_one(
                {"id": r["id"]},
                {"$set": {"status": "draft",
                          "needs_review": True,
                          "sprint6_unpublished_reason": "junk-page-title-or-placeholder"}},
            )
            cleaned += 1
    if cleaned:
        logger.info("Sprint 6 junk-record cleanup: unpublished %d record(s)", cleaned)


@app.on_event("startup")
async def startup_event():
    # Studio ingestion: report which OCR providers are available. We now
    # run a provider chain (Tesseract → GPT-4o-mini Vision) so image-only
    # supplier PDFs work in production even without the tesseract binary.
    try:
        from ocr_providers import get_ocr_provider_chain
        chain = get_ocr_provider_chain()
        avail = chain.available_providers
        if avail:
            logger.info("Studio OCR: provider chain ready — %s", ", ".join(avail))
        else:
            logger.warning(
                "Studio OCR: NO providers available. Install tesseract "
                "(apt-get install tesseract-ocr) OR set EMERGENT_LLM_KEY "
                "for the GPT-4o-mini Vision fallback."
            )
    except Exception:
        logger.exception("Studio OCR: provider-chain init failed")

    # Studio ingestion — recovery sweep. Any upload left in `processing`
    # is by definition orphaned (the background task that owned it died
    # with the previous process). We flip those to `failed` with a clear
    # diagnostic so the admin can click Reprocess to retry. This makes
    # OCR jobs recoverable across application restarts, pod recycles and
    # deploys — no upload can ever be stuck on `processing` forever.
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        res = await db.ke_uploads.update_many(
            {"status": "processing"},
            {"$set": {
                "status": "failed",
                "failure_reason": (
                    "Extraction was interrupted by an application restart or "
                    "provider failure. Click Reprocess to retry — the "
                    "uploaded PDF is still saved on the server."
                ),
                "interrupted_at": now_iso,
            }},
        )
        if res.modified_count:
            logger.warning(
                "Studio recovery: reset %d upload(s) from processing → failed",
                res.modified_count,
            )
    except Exception:
        logger.exception("Studio recovery sweep failed")

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
        await _purge_dev_test_uploads()
    except Exception:
        logger.exception("Studio dev-test purge failed")
    try:
        await _recover_stuck_studio_uploads()
    except Exception:
        logger.exception("Studio stuck-upload recovery failed")
    try:
        await _refresh_studio_index()
    except Exception:
        logger.exception("Studio index warm-up failed")

    # Sprint 7 — intelligence layer warm-up. Seed DNA embeddings are cheap
    # (local, ~1s); the published-record backfill runs as a background task
    # (vision calls, idempotent, resumes across restarts).
    import asyncio as _aio
    try:
        await _seed_catalogue_dna()
    except Exception:
        logger.exception("Seed DNA index failed")
    _aio.create_task(_visual_dna_backfill())


async def _seed_demo_project() -> None:
    """Create/refresh the global read-only demo project + one demo room. This
    is stored under a synthetic admin user with role='system-demo' so it never
    surfaces in a real user's dashboard. Uses a fixed slug/id so
    GET /api/demo/project can find it without an id in the URL.

    Sprint 8.3 — Product Freeze demo. The demo is a premium warm-modern
    LIVING ROOM (fluted walnut feature wall, linen sofa, warm oak flooring,
    sheer linen drapes, brass accents, indoor foliage). Every detected zone,
    catalogue match and product corresponds to something actually visible in
    the uploaded reference image. Matches are curated from real seeded
    catalogues (no fabricated codes) with realistic ≥ 80% scores to showcase
    the Knowledge Engine at full quality — see `_curate_demo_match` below."""
    marker = {"is_demo": True, "demo_slug": "materialmatch-demo-warm-living"}
    existing = await db.projects.find_one(marker)
    now = datetime.now(timezone.utc).isoformat()
    # Premium warm-modern living room reference — vetted for the v1.0 demo.
    # Wood-forward palette (warm oak floor, wood-framed window, chunky walnut
    # coffee table, arc brass floor lamp, foliage) matches the curated zone
    # set below. Unsplash CC0.
    demo_ref_url = ("https://images.unsplash.com/photo-1618221195710-dd6b41faaea6"
                    "?w=1600&q=80&auto=format&fit=crop")
    demo_ref_b64 = ""
    try:
        import urllib.request
        req = urllib.request.Request(demo_ref_url, headers={"User-Agent": "materialmatch-demo"})
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310
            demo_ref_b64 = base64.b64encode(r.read()).decode("utf-8")
        logger.info("Demo reference image loaded (%d b64 chars)", len(demo_ref_b64))
    except Exception:
        logger.warning("Demo reference image fetch failed — demo will have no image")

    def _alt(name, why, cost, durability, maintenance, brands, use_case=""):
        return {"name": name, "why": why, "cost_tier": cost, "durability": durability,
                "maintenance": maintenance, "brands_to_check": brands, "use_case": use_case}

    # ------------------------------------------------------------------
    # Curator: convert (brand, material_name, match_percent) tuples into
    # frontend-shaped catalogue_matches by looking up the REAL seed record
    # verbatim. If a record can't be found we silently drop it — never
    # fabricate metadata. Sprint 8.3 (Product Freeze).
    # ------------------------------------------------------------------
    def _curate(picks: list) -> list:
        out: list[dict] = []
        for entry in picks:
            brand, mname, pct, sim = entry
            seed = _find_seed_record(brand, mname)
            if seed is None:
                logger.warning("Demo curator: seed record not found for %s / %s", brand, mname)
                continue
            code = seed.get("material_code")
            page = seed.get("page_number")
            out.append({
                "id": seed["id"],
                "brand": seed["brand"],
                "catalogue": seed["catalogue"],
                "material_name": seed["material_name"],
                "material_code": code,
                "material_code_display": code if code else "Code unavailable in current database",
                "page_number": page,
                "page_display": f"p.{page}" if page else "Page unavailable",
                "material_family": seed["material_family"],
                "category": seed["category"],
                "finish": seed.get("finish"),
                "color_name": seed.get("color_name"),
                "color_hex": seed.get("color_hex"),
                "texture": seed.get("texture"),
                "source": seed.get("source") or "MaterialMatch Library",
                "match_percent": pct,
                "similarity": sim,
            })
        return out

    _sim_strong = {"visual": 90, "color": 92, "finish": 88, "texture": 86}
    _sim_high = {"visual": 84, "color": 88, "finish": 82, "texture": 80}
    _sim_good = {"visual": 78, "color": 82, "finish": 78, "texture": 74}

    analysis_rows = [
        # ─── 1 · Feature wall — fluted warm-wood slat panelling ─────────
        {
            "zone": "Feature Wall — Fluted Warm-Wood Slat Panelling",
            "material_family": "Wood", "material_type": "Fluted warm-wood slat feature panel (deep honey walnut)",
            "color": "Deep warm walnut", "texture": "Vertical fluted slat", "finish": "Matte oiled",
            "design_style": "Warm contemporary", "keywords": ["fluted", "warm wood", "slat", "feature wall", "walnut"],
            "confidence": 93, "procurement_difficulty": "Easy in India",
            "pin": {"x": 50, "y": 40},
            "indian_alternative": "Greenlam Fluted Oak Panel or Merino Fluted Walnut Panel — spec vertical rhythm to match reference.",
            "brands_to_check": ["Greenlam", "Merino", "Ebco"], "vendor_type": "Panel supplier",
            "sourcing_keywords": ["fluted wood panel india", "warm walnut slat feature wall"],
            "catalogue_matches": _curate([
                ("Greenlam", "Fluted Oak Panel", 93, _sim_strong),
                ("Merino", "Fluted Walnut Panel", 90, _sim_high),
                ("Greenlam", "Smoked Oak Laminate", 84, _sim_good),
            ]),
        },
        # ─── 2 · Sofa upholstery — warm linen weave ─────────────────────
        {
            "zone": "Sofa Upholstery — Warm Linen Weave",
            "material_family": "Fabric", "material_type": "Oatmeal linen-weave upholstery",
            "color": "Warm oatmeal beige", "texture": "Fine linen weave", "finish": "Soft matte",
            "design_style": "Warm minimalist", "keywords": ["linen", "oatmeal", "beige", "upholstery", "weave"],
            "confidence": 89, "procurement_difficulty": "Easy",
            "pin": {"x": 40, "y": 68},
            "indian_alternative": "D'Decor Boucle Cream Upholstery or Fabindia Natural Cotton Slub — request performance-grade weave.",
            "brands_to_check": ["D'Decor", "Fabindia", "Sarita Handa"], "vendor_type": "Upholstery textile",
            "sourcing_keywords": ["linen upholstery india", "oatmeal sofa fabric"],
            "catalogue_matches": _curate([
                ("D'Decor", "Boucle Cream Upholstery", 88, _sim_high),
                ("Fabindia", "Natural Cotton Slub", 84, _sim_good),
                ("Sarita Handa", "Linen Slub Bedcover", 80, _sim_good),
            ]),
        },
        # ─── 3 · Coffee table — warm walnut wood block ──────────────────
        {
            "zone": "Coffee Table — Warm Walnut Wood",
            "material_family": "Furniture", "material_type": "Warm walnut low coffee table",
            "color": "Warm walnut brown", "texture": "Fine straight grain", "finish": "Matte oiled",
            "design_style": "Warm contemporary", "keywords": ["walnut", "coffee table", "warm wood", "matte"],
            "confidence": 88, "procurement_difficulty": "Easy",
            "pin": {"x": 48, "y": 78},
            "indian_alternative": "Pepperfry Warm Walnut collection or bespoke fabricator over Merino Warm Walnut HPL.",
            "brands_to_check": ["Pepperfry", "Gulmohar Lane", "West Elm India"], "vendor_type": "Furniture retailer",
            "sourcing_keywords": ["warm walnut coffee table india"],
            "catalogue_matches": _curate([
                ("Pepperfry", "Warm Walnut Nightstand", 90, _sim_strong),
                ("Merino", "Warm Walnut Décor HPL", 86, _sim_high),
                ("Greenlam", "American Walnut Crown", 84, _sim_good),
            ]),
        },
        # ─── 4 · Flooring — warm oak plank ──────────────────────────────
        {
            "zone": "Flooring — Warm Oak Plank",
            "material_family": "Wood", "material_type": "Engineered warm oak plank flooring",
            "color": "Medium warm oak", "texture": "Straight plank grain", "finish": "Satin oiled",
            "design_style": "Warm contemporary", "keywords": ["oak plank", "warm floor", "engineered wood"],
            "confidence": 92, "procurement_difficulty": "Moderate",
            "pin": {"x": 20, "y": 92},
            "indian_alternative": "Kajaria Wood Oak Warm 200x1200 wood-look porcelain, or Pergo XP engineered oak.",
            "brands_to_check": ["Kajaria", "Somany", "Pergo"], "vendor_type": "Flooring showroom",
            "sourcing_keywords": ["warm oak plank flooring india", "wood look porcelain 200x1200"],
            "catalogue_matches": _curate([
                ("Kajaria", "Wood Oak Warm 200x1200", 93, _sim_strong),
                ("Somany", "Oak Plank 200x1200", 90, _sim_high),
                ("Greenlam", "Warm Oak HPL", 82, _sim_good),
            ]),
        },
        # ─── 5 · Curtains — sheer ivory linen drape ─────────────────────
        {
            "zone": "Curtains — Sheer Ivory Linen Drape",
            "material_family": "Fabric", "material_type": "Floor-to-ceiling sheer ivory linen curtain",
            "color": "Warm ivory", "texture": "Loose linen weave", "finish": "Soft drape",
            "design_style": "Warm minimalist", "keywords": ["sheer linen", "ivory curtain", "drape"],
            "confidence": 90, "procurement_difficulty": "Easy",
            "pin": {"x": 88, "y": 40},
            "indian_alternative": "D'Decor Sheer Ivory Linen Panel — request generous pleat for the same drape volume.",
            "brands_to_check": ["D'Decor", "The White Window", "Deco Window"], "vendor_type": "Window furnishing",
            "sourcing_keywords": ["sheer linen curtain india", "ivory drape floor to ceiling"],
            "catalogue_matches": _curate([
                ("D'Decor", "Sheer Ivory Linen Panel", 94, _sim_strong),
                ("Fabindia", "Ivory Linen Bedding", 82, _sim_good),
                ("Sarita Handa", "Organic Cotton Sateen Set", 78, _sim_good),
            ]),
        },
        # ─── 6 · Painted wall / ceiling — warm off-white ────────────────
        {
            "zone": "Painted Walls & Ceiling — Warm Off-White",
            "material_family": "Paint", "material_type": "Warm off-white matte emulsion",
            "color": "Warm off-white", "texture": "Smooth", "finish": "Matte emulsion",
            "design_style": "Warm minimalist", "keywords": ["warm white", "matte paint", "off white", "emulsion"],
            "confidence": 95, "procurement_difficulty": "Very easy",
            "pin": {"x": 15, "y": 20},
            "indian_alternative": "Asian Paints Cotton White or Warm Ivory (Royale) — confirm sheen with sample.",
            "brands_to_check": ["Asian Paints", "Berger", "Nerolac", "Dulux"], "vendor_type": "Paint retailer",
            "sourcing_keywords": ["asian paints cotton white", "warm off white emulsion india"],
            "catalogue_matches": _curate([
                ("Asian Paints", "Cotton White", 94, _sim_strong),
                ("Asian Paints", "Warm Ivory", 90, _sim_high),
                ("Asian Paints", "Almond Whisper", 85, _sim_good),
            ]),
        },
        # ─── 7 · Table lamp — sculpted base + fabric shade ──────────────
        {
            "zone": "Table Lamp — Sculpted Base with Warm Shade",
            "material_family": "Lighting", "material_type": "Sculpted base table lamp with warm fabric shade",
            "color": "Warm ivory & brushed brass", "texture": "Smooth base, fine woven shade", "finish": "Matte + brushed satin",
            "design_style": "Warm modern", "keywords": ["table lamp", "warm shade", "brass base", "sculpted"],
            "confidence": 85, "procurement_difficulty": "Moderate",
            "pin": {"x": 85, "y": 55},
            "indian_alternative": "Jainsons Emporio Marble Base Table Lamp or The White Teak Co. brass table lamp.",
            "brands_to_check": ["Jainsons Emporio", "The White Teak Co.", "Havells"], "vendor_type": "Lighting studio",
            "sourcing_keywords": ["table lamp warm brass india", "sculpted base table lamp"],
            "catalogue_matches": _curate([
                ("Jainsons Emporio", "Marble Base Table Lamp", 88, _sim_high),
                ("The White Teak Co.", "Slim Brass Floor Lamp", 82, _sim_good),
                ("Whispering Homes", "Fluted Glass Wall Sconce", 78, _sim_good),
            ]),
        },
        # ─── 8 · Accent chair — textured neutral upholstery ─────────────
        {
            "zone": "Accent Chair — Textured Neutral Upholstery",
            "material_family": "Fabric", "material_type": "Textured neutral upholstery lounge chair",
            "color": "Light warm greige", "texture": "Bouclé / dense linen", "finish": "Soft textured",
            "design_style": "Warm contemporary", "keywords": ["boucle", "accent chair", "neutral", "textured"],
            "confidence": 87, "procurement_difficulty": "Easy",
            "pin": {"x": 20, "y": 66},
            "indian_alternative": "Pepperfry Bouclé Reading Chair or Urban Ladder bouclé lounge — request tight loop weave.",
            "brands_to_check": ["Pepperfry", "Urban Ladder", "D'Decor"], "vendor_type": "Furniture + upholstery",
            "sourcing_keywords": ["boucle accent chair india", "neutral upholstered lounge chair"],
            "catalogue_matches": _curate([
                ("Pepperfry", "Bouclé Reading Chair", 90, _sim_strong),
                ("D'Decor", "Boucle Cream Upholstery", 86, _sim_high),
                ("Urban Ladder", "Bouclé Upholstered Headboard", 80, _sim_good),
            ]),
        },
        # ─── 9 · Metal accents — brushed brass ──────────────────────────
        {
            "zone": "Metal Accents — Brushed Brass Trims & Pulls",
            "material_family": "Hardware", "material_type": "Brushed brass trim / cove profile",
            "color": "Warm brass", "texture": "Brushed satin", "finish": "Brushed satin brass",
            "design_style": "Warm contemporary", "keywords": ["brass", "brushed", "trim", "cove profile", "hardware"],
            "confidence": 86, "procurement_difficulty": "Easy in India",
            "pin": {"x": 60, "y": 60},
            "indian_alternative": "Häfele India brass trims & pulls, Ebco fluted brass wall strip for feature accents.",
            "brands_to_check": ["Häfele India", "Ebco", "Godrej Locks"], "vendor_type": "Architectural hardware",
            "sourcing_keywords": ["brushed brass trim india", "brass cove profile"],
            "catalogue_matches": _curate([
                ("Häfele India", "Slim Brass Pull Handle 320mm", 89, _sim_high),
                ("Häfele India", "Brass Cove Profile 12mm", 86, _sim_high),
                ("Ebco", "Fluted Brass Wall Cladding Strip", 83, _sim_good),
            ]),
        },
        # ─── 10 · Indoor foliage — no catalogue recommendation ──────────
        {
            "zone": "Indoor Foliage — Statement Plant",
            "material_family": "Plant", "material_type": "Live indoor foliage in matte planter",
            "color": "Deep green with warm-neutral pot", "texture": "Natural leaf",
            "finish": "Natural, unfinished",
            "design_style": "Biophilic accent", "keywords": ["plant", "foliage", "indoor greenery"],
            "confidence": 82, "procurement_difficulty": "Nursery — not a catalogue item",
            "pin": {"x": 75, "y": 82},
            "indian_alternative": "Ugaoo or Nurserylive — Fiddle Leaf Fig / Rubber Plant + minimal matte planter.",
            "brands_to_check": ["Ugaoo", "Nurserylive"], "vendor_type": "Nursery",
            "sourcing_keywords": ["fiddle leaf fig india", "indoor plant nursery"],
            # TRUST RULE — plants are not a catalogue-searchable material. We
            # intentionally return no matches so the UI shows
            # "No high-confidence catalogue match found."
            "catalogue_matches": [],
            "match_buckets": {"best": [], "possible": [], "low": [],
                              "counts": {"best": 0, "possible": 0, "low": 0}},
        },
    ]

    # Attach match_buckets for the 9 curated rows (bucketed with the same
    # curated list so best[] contains up to 3 hand-picked hits). Every row
    # is flagged `curated=True` so /api/demo/project preserves the hand-picked
    # matches verbatim instead of re-scoring on every read.
    for r in analysis_rows:
        r["curated"] = True
        if r.get("match_buckets") is None or "match_buckets" not in r:
            r["match_buckets"] = _bucket_matches(r.get("catalogue_matches") or [])

    # Attach brain + classification + likely_family without letting the
    # standard matcher overwrite the curated catalogue_matches (setdefault /
    # "not in row" guards preserve them).
    _enrich_rows_with_catalogue(analysis_rows, top_k=8)
    products_list = [
        {"id": "product_1", "product_name": "Sculpted Brushed-Brass Table Lamp",
         "category": "lighting",
         "description": "Warm brushed-brass sculpted base table lamp with fabric drum shade — matches the reference side-table.",
         "style_keywords": ["modern", "warm", "sculpted"], "color_keywords": ["brass", "warm"],
         "material_keywords": ["brass", "fabric"], "finish_keywords": ["brushed", "matte"],
         "estimated_price_inr": "₹8,999", "search_keywords": ["brushed brass table lamp warm shade india"],
         "confidence": 88},
        {"id": "product_2", "product_name": "Bouclé Neutral Accent Lounge Chair",
         "category": "furniture",
         "description": "Sculptural bouclé lounge chair — anchors the reading corner shown in the reference.",
         "style_keywords": ["contemporary", "curved", "cozy"], "color_keywords": ["greige", "warm"],
         "material_keywords": ["boucle", "wood"], "finish_keywords": ["soft"],
         "estimated_price_inr": "₹28,999", "search_keywords": ["boucle accent chair india warm"],
         "confidence": 86},
        {"id": "product_3", "product_name": "Warm Walnut Low Coffee Table",
         "category": "furniture",
         "description": "Low-slung walnut-veneered coffee table with clean rectangular form.",
         "style_keywords": ["warm modern", "minimalist"], "color_keywords": ["walnut", "warm brown"],
         "material_keywords": ["walnut", "wood"], "finish_keywords": ["matte oiled"],
         "estimated_price_inr": "₹22,499", "search_keywords": ["walnut coffee table india"],
         "confidence": 87},
        {"id": "product_4", "product_name": "Sheer Ivory Linen Curtain Panel",
         "category": "textile-decor",
         "description": "Floor-to-ceiling sheer ivory linen drape — reproduces the soft glow behind the sofa.",
         "style_keywords": ["warm minimalist"], "color_keywords": ["ivory", "warm"],
         "material_keywords": ["linen"], "finish_keywords": ["sheer"],
         "estimated_price_inr": "₹4,499 per panel", "search_keywords": ["sheer ivory linen curtain india"],
         "confidence": 84},
        {"id": "product_5", "product_name": "Statement Fiddle Leaf Fig with Matte Planter",
         "category": "plant-planter",
         "description": "Live indoor foliage in a matte warm-neutral planter — mirrors the reference biophilic accent.",
         "style_keywords": ["biophilic", "warm minimalist"], "color_keywords": ["deep green", "warm neutral"],
         "material_keywords": ["plant", "ceramic"], "finish_keywords": ["natural", "matte"],
         "estimated_price_inr": "₹3,199", "search_keywords": ["fiddle leaf fig india indoor"],
         "confidence": 82},
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
            {"filename": "materialmatch-demo-living-room.pdf", "page_number": 16,
             "match_percent": 93, "material_name": "Greenlam Fluted Oak Panel",
             "explanation": "Same vertical fluted rhythm and deep warm-wood tone as the sofa feature wall."},
            {"filename": "materialmatch-demo-living-room.pdf", "page_number": 22,
             "match_percent": 93, "material_name": "Kajaria Wood Oak Warm 200x1200",
             "explanation": "Warm oak plank finish that mirrors the medium-tone timber flooring in the reference."},
            {"filename": "materialmatch-demo-living-room.pdf", "page_number": 8,
             "match_percent": 94, "material_name": "D'Decor Sheer Ivory Linen Panel",
             "explanation": "Soft, generously-pleated sheer linen — reproduces the floor-to-ceiling drape."},
        ],
        "generated_at": now,
    }
    demo_doc = {
        **marker,
        "name": "Earthen Serenity Living — Demo",
        "client_name": "Reference Residence · MaterialMatch v1.0",
        "user_id": "system-demo",
        "reference_image_b64": demo_ref_b64,
        "status": "completed",
        "preferred_region": "IN",
        "mock_analysis": {
            "summary": {
                "overall_style": "Warm contemporary living — fluted walnut, warm oak, linen and brushed brass",
                "design_style": "Warm contemporary living",
                "material_palette": "Fluted walnut, warm oak, oatmeal linen, sheer ivory, warm off-white paint, brushed brass",
                "key_finishes": "Fluted vertical wood slat, matte oiled walnut, linen weave, sheer drape, brushed satin brass",
                "sourcing_note": "Every spec is sourceable in India — Greenlam / Merino for panelling, Kajaria / Somany for warm oak flooring, D'Decor / Fabindia for linen, Asian Paints Cotton White for walls, Häfele India for brass hardware.",
                "palette": ["Warm walnut", "Warm oak", "Oatmeal", "Warm ivory", "Brushed brass", "Deep foliage green"],
                "dominant_materials": ["Fluted walnut feature wall", "Warm oak flooring", "Linen sofa upholstery", "Sheer ivory drape"],
                "confidence": 92,
            },
            "rows": analysis_rows,
            "generated_at": now,
            "version": "demo-v3-living-room",
        },
        "products_detected": {
            "products": enriched_products,
            "generated_at": now,
            "region": "IN",
            "version": "demo-v3",
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
        "name": "Primary Living Room",
        "room_type": "living-room",
        "order": 0,
        "current_site_photos": [],
        "moodboards": [],
        "reference_images": [],
        "final_render_images": [],
        "concept_overview": (
            "A warm contemporary living room built around three anchor gestures: a "
            "fluted deep-walnut feature wall behind the sofa, warm oak plank flooring "
            "underfoot, and a floor-to-ceiling sheer ivory linen drape that softens the "
            "afternoon light. An oatmeal linen sofa is grounded by a low warm-walnut "
            "coffee table; a bouclé accent chair and a sculpted brushed-brass table lamp "
            "hold the reading corner. Statement foliage introduces a biophilic accent, "
            "and brushed-brass hardware ties the whole palette together. Every spec is "
            "sourceable in India from real supplier catalogues indexed in the "
            "MaterialMatch Knowledge Engine."
        ),
        "concept_overview_ai_draft": "",
        "designer_notes": (
            "Delivery in phases: fluted-panel feature wall + flooring first (5 weeks), "
            "then upholstery + drapes (2 weeks), then lighting, hardware and styling "
            "(1 week). Final on-site styling over a weekend."
        ),
        "pinned_material_row_ids": [
            "Feature Wall — Fluted Warm-Wood Slat Panelling",
            "Sofa Upholstery — Warm Linen Weave",
            "Flooring — Warm Oak Plank",
            "Curtains — Sheer Ivory Linen Drape",
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
    # Sprint 8.3 (Product Freeze) — rows flagged `curated=True` keep their
    # hand-picked catalogue matches verbatim so the demo showcases the
    # Knowledge Engine at its best quality. Non-curated rows (if any) are
    # re-enriched live so new Studio uploads still influence them.
    rows = (doc.get("mock_analysis") or {}).get("rows") or []
    rows_to_reenrich = [r for r in rows if not r.get("curated")]
    for r in rows_to_reenrich:
        for k in ("catalogue_matches", "match_buckets", "brain",
                  "searched_categories", "searched_libraries", "excluded_libraries"):
            r.pop(k, None)
    if rows_to_reenrich:
        _enrich_rows_with_catalogue(rows_to_reenrich)
    return _sanitize_demo_project(doc)


@api_router.get("/demo/reference-image")
async def get_demo_reference_image():
    """Public: fetch the demo project's reference image as a data URL.
    The demo asset is a PNG (see _seed_demo_project → demo_ref_url) so the
    data URL declares image/png. Browsers still decode based on the actual
    signature bytes, but declaring the correct mime prevents CDN sniffing
    hiccups on the demo page."""
    doc = await db.projects.find_one({"is_demo": True, "demo_slug": "materialmatch-demo-warm-living"})
    if not doc or not doc.get("reference_image_b64"):
        raise HTTPException(status_code=404, detail="Demo reference not available")
    b64 = doc["reference_image_b64"]
    # Auto-detect PNG vs JPEG from the first bytes so we always send the
    # correct mime type without hardcoding the asset format.
    mime = "image/png" if b64.startswith("iVBOR") else "image/jpeg"
    return {"data_url": f"data:{mime};base64,{b64}"}


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
    # Round 8 — preview swatch for the click-to-enlarge lightbox.
    # Frontend `addCatalogueMatchToShortlist` propagates these from the
    # catalogue_matches entry.  All optional — custom / product items
    # legitimately have none of them.
    swatch_crop_b64: Optional[str] = None
    color_hex: Optional[str] = None
    material_code: Optional[str] = None


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
        # Round 8 — swatch preview fields for the shortlist lightbox.
        # Persist as-is (base64 payloads can be large but we already
        # store base64 reference images on the project doc, so this
        # doesn't change the storage tier).
        "swatch_crop_b64": payload.swatch_crop_b64 or None,
        "color_hex": (payload.color_hex or "").strip() or None,
        "material_code": (payload.material_code or "").strip() or None,
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


def _pdf_page_ocr_text(page, dpi: int = 200) -> str:
    """Render a PDF page to a PNG and run it through the OCR provider
    chain — Tesseract first (fast, offline, may not be present in
    production), GPT-4o-mini Vision second (persistent, always
    available via the Emergent Universal Key). Returns plain UTF-8
    text; downstream material extraction does not need to know which
    provider produced it.

    `dpi` is chosen by the caller — small PDFs get 200 DPI, very
    large PDFs drop to ~130 DPI to keep memory bounded."""
    try:
        from ocr_providers import get_ocr_provider_chain
        scale = max(0.8, dpi / 72.0)
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        png_bytes = pix.tobytes("png")
        chain = get_ocr_provider_chain()
        text, _provider = chain.transcribe(png_bytes)
        return text
    except Exception:
        logger.exception("OCR failed for page")
        return ""


def _looks_like_name_stub(s: str) -> bool:
    """Lightweight name-quality check used by page-title / collection-name
    detection. Kept small so it can also serve as a first-pass filter
    for OCR gibberish."""
    if not s or len(s) < 4 or len(s) > 90:
        return False
    if s.isnumeric():
        return False
    first = next((c for c in s if not c.isspace()), "")
    if not first.isalpha():
        return False
    letters = sum(1 for c in s if c.isalpha())
    if letters < 3:
        return False
    if letters / max(1, len(s)) < 0.45:
        return False
    low = s.lower()
    if any(bad in low for bad in ("qr code", "www.", "http", "copyright", "warranty", "material match")):
        return False
    return True


def _page_level_fallback_rect(page):
    """Type-B catalogue fallback: many scanned supplier PDFs render each
    page as a SINGLE full-page raster image (a scan of a physical
    swatch page). Our per-swatch geometry filter rejects those as
    "hero banners", so we get 0 records for the whole catalogue.

    When (a) no swatch survived the geometry / pixel gates AND (b) the
    page contains at least one embedded image that covers most of the
    page, treat the whole page as one page-level swatch candidate. The
    admin can then use the Preview page + Edit workflow to split /
    correct these records in the Review Queue. Returns None if the
    page doesn't fit the Type-B shape."""
    try:
        images = page.get_images(full=True)
    except Exception:
        return None
    if not images:
        return None
    page_area = max(1.0, page.rect.width * page.rect.height)
    for img in images:
        try:
            for r in page.get_image_rects(img[0]):
                area = r.width * r.height
                if area / page_area >= 0.60:
                    # Give the fallback rect a small internal margin so
                    # the dominant-colour sampler doesn't hit white gutters.
                    m_x = r.width * 0.15
                    m_y = r.height * 0.15
                    return fitz.Rect(
                        r.x0 + m_x, r.y0 + m_y,
                        r.x1 - m_x, r.y1 - m_y,
                    )
        except Exception:
            continue
    return None


def _ocr_available() -> bool:
    """Return True if AT LEAST ONE OCR provider (local or cloud) can
    service a page. Used to decide whether image-only pages should be
    processed at all."""
    try:
        from ocr_providers import get_ocr_provider_chain
        return bool(get_ocr_provider_chain().available_providers)
    except Exception:
        return False


def _has_local_ocr() -> bool:
    """Return True if a LOCAL (offline / cheap) OCR provider is present.
    Used to decide whether the extractor should do per-strip OCR on
    individual swatches — cheap with tesseract, prohibitively expensive
    with cloud vision, which already handles the whole page at once."""
    try:
        from ocr_providers import get_ocr_provider_chain, TesseractProvider
        chain = get_ocr_provider_chain()
        for p in chain._providers:
            if isinstance(p, TesseractProvider) and p.is_available():
                return True
        return False
    except Exception:
        return False


def _tesseract_available() -> bool:
    """Backwards-compat: kept for existing callers. Returns True if
    ANY OCR provider is available (tesseract or the cloud fallback).
    We keep the historical name so all existing gates keep working."""
    return _ocr_available()


def _detect_swatches_on_page(page) -> list:
    """Find candidate material-swatch bounding boxes on a page. Returns a
    list of `fitz.Rect` in page coordinates. Filters out decorative graphics,
    QR codes, logos, huge hero images, lifestyle photos and page-fill banners.

    Heuristic (generic — works across manufacturer catalogues):
      - Uses `page.get_image_rects(xref)` for every embedded image.
      - Rejects rects < 1.2% or > 30% of the page area (icons, QR, hero shots).
      - Rejects extreme aspect ratios (banners, thin strips).
      - Rejects tiny near-square rects < ~50pt on the shorter side (QR / icon).
      - Rejects images whose pixel colour distribution is either
        photograph-like (very high stddev in all 3 channels — lifestyle
        renders) or QR-like (near-black+near-white binary distribution).
      - Dedups near-identical rects."""
    try:
        images = page.get_images(full=True)
    except Exception:
        return []
    page_area = max(1.0, page.rect.width * page.rect.height)
    rects: list = []
    for img in images:
        xref = img[0]
        try:
            for r in page.get_image_rects(xref):
                area = r.width * r.height
                pct = area / page_area
                # Size filter — reject icons / QRs (too small) and hero /
                # lifestyle images (too large). 1.2%..30% is the sweet spot
                # for real material swatches across supplier catalogues.
                if pct < 0.012 or pct > 0.30:
                    continue
                # Aspect-ratio filter — reject banners / thin strips.
                ar = r.width / max(1.0, r.height)
                if ar > 3.5 or ar < 0.30:
                    continue
                # Minimum shorter-side pixels: a real swatch is usually
                # >= 55 pt on the short edge. Anything smaller is either
                # a UI icon, a code stamp or a QR.
                if min(r.width, r.height) < 55:
                    continue
                rects.append(r)
        except Exception:
            continue
    # Dedup near-duplicates (some PDFs embed the same image twice).
    unique: list = []
    for r in rects:
        if not any(abs(r.x0 - u.x0) < 4 and abs(r.y0 - u.y0) < 4
                   and abs(r.x1 - u.x1) < 4 and abs(r.y1 - u.y1) < 4 for u in unique):
            unique.append(r)
    # Content filter — reject photographic / QR-code content by sampling
    # the pixel distribution of each candidate swatch.
    filtered: list = []
    for r in unique:
        if _looks_like_material_swatch(page, r):
            filtered.append(r)
    return filtered


def _looks_like_material_swatch(page, rect) -> bool:
    """Return True if the clipped region looks like a material swatch
    (finish sample, colour chip, veneer/laminate face) rather than a
    photograph, QR code, logo or decorative graphic. Uses only pixel
    statistics so it works for any supplier catalogue."""
    try:
        pix = page.get_pixmap(clip=rect, matrix=fitz.Matrix(0.35, 0.35), alpha=False)
        w, h, n = pix.width, pix.height, pix.n
        if n < 3 or w * h < 25:
            return False
        samples = pix.samples
        # Reservoir stats: mean + variance per channel + fraction of
        # near-black and near-white pixels.
        total = w * h
        sum_r = sum_g = sum_b = 0
        sq_r = sq_g = sq_b = 0
        black = white = 0
        for i in range(0, len(samples), n):
            r = samples[i]
            g = samples[i + 1]
            b = samples[i + 2]
            sum_r += r
            sum_g += g
            sum_b += b
            sq_r += r * r
            sq_g += g * g
            sq_b += b * b
            mx = max(r, g, b)
            mn = min(r, g, b)
            if mx < 40:
                black += 1
            elif mn > 220:
                white += 1
        mean_r = sum_r / total
        mean_g = sum_g / total
        mean_b = sum_b / total
        var_r = max(0.0, sq_r / total - mean_r * mean_r)
        var_g = max(0.0, sq_g / total - mean_g * mean_g)
        var_b = max(0.0, sq_b / total - mean_b * mean_b)
        std_all = (var_r + var_g + var_b) ** 0.5
        black_pct = black / total
        white_pct = white / total
        # QR / logo pattern: heavy black+white polarisation, low mid-tones.
        if (black_pct + white_pct) > 0.55 and std_all > 55:
            return False
        # Fully-photographic content (very high colour variance across all
        # 3 channels) → lifestyle render or product photo, not a swatch.
        if var_r > 4200 and var_g > 4200 and var_b > 4200 and std_all > 130:
            return False
        # Nearly-empty white cards / faint icons — not a real swatch.
        if white_pct > 0.85:
            return False
        return True
    except Exception:
        # If we can't sample it, be conservative and keep the rect —
        # downstream text-association filter will still drop noise.
        return True


def _swatch_dominant_hex_and_thumb(page, rect) -> tuple[str, str | None]:
    """Sample a swatch rectangle and return (color_hex, thumbnail_b64)."""
    try:
        pix = page.get_pixmap(clip=rect, matrix=fitz.Matrix(1.4, 1.4), alpha=False)
        samples = pix.samples  # RGB bytes
        n = max(1, len(samples) // 3)
        r = sum(samples[0::3]) // n
        g = sum(samples[1::3]) // n
        b = sum(samples[2::3]) // n
        return f"#{r:02X}{g:02X}{b:02X}", base64.b64encode(pix.tobytes("jpeg")).decode()
    except Exception:
        return "#B7ADA0", None


def _nearest_text_for_rect(page, rect, radius: float = 260.0) -> str:
    """Return concatenated text of the closest text blocks to the swatch rect.
    Falls back to on-the-fly OCR of a text strip beneath the swatch."""
    try:
        blocks = page.get_text("blocks") or []
    except Exception:
        blocks = []
    scored = []
    cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
    for b in blocks:
        if len(b) < 5:
            continue
        x0, y0, x1, y1, txt = b[0], b[1], b[2], b[3], (b[4] or "").strip()
        if not txt or b[6] if len(b) > 6 else False:
            continue
        if not txt:
            continue
        bx, by = (x0 + x1) / 2, (y0 + y1) / 2
        dx, dy = bx - cx, by - cy
        dist = (dx * dx + dy * dy) ** 0.5
        if dist > radius:
            continue
        scored.append((dist, txt))
    scored.sort(key=lambda t: t[0])
    txt = " ".join(t for _, t in scored[:4]).strip()
    return txt


def _ocr_text_below_swatch(page, rect) -> str:
    """OCR a horizontal strip immediately below a swatch to catch labels
    that live outside the embedded PDF text stream (common in scanned or
    lifestyle-render catalogues). Uses the shared OCR provider chain so
    the same call works with tesseract locally and with GPT-4o-mini
    Vision in production."""
    try:
        from ocr_providers import get_ocr_provider_chain
        chain = get_ocr_provider_chain()
        if not chain.available_providers:
            return ""
        strip = fitz.Rect(
            max(0, rect.x0 - 6),
            rect.y1,
            min(page.rect.width, rect.x1 + 6),
            min(page.rect.height, rect.y1 + max(80, rect.height * 0.6)),
        )
        pix = page.get_pixmap(clip=strip, matrix=fitz.Matrix(220 / 72, 220 / 72), alpha=False)
        text, _p = chain.transcribe(pix.tobytes("png"))
        return (text or "").strip()
    except Exception:
        logger.exception("swatch-strip OCR failed")
        return ""


def _classify_category_from_text(text_l: str) -> str:
    if any(k in text_l for k in ("laminate", "hpl", "décor", "decor ")):
        return "Laminates"
    if any(k in text_l for k in ("paint", "emulsion", "shade")):
        return "Paints"
    if "veneer" in text_l:
        return "Veneers"
    if any(k in text_l for k in ("marble", "quartz", "granite", "onyx")):
        return "Stone"
    if any(k in text_l for k in ("tile", "porcelain", "vitrified")):
        return "Tiles"
    if any(k in text_l for k in ("linen", "boucle", "cotton", "fabric", "sateen")):
        return "Fabric"
    if any(k in text_l for k in ("pendant", "sconce", "lamp", "chandelier")):
        return "Lighting"
    return "Laminates"


# ── Sprint 4: region classification + category verification ─────────────

REGION_CLASSES = (
    "MATERIAL_SWATCH", "LIFESTYLE_IMAGE", "LOGO", "QR_CODE",
    "SPECIFICATION_TABLE", "TEXT_BLOCK", "CERTIFICATION",
    "DECORATIVE_GRAPHIC", "UNKNOWN",
)

_CATEGORY_KEYWORDS = {
    "Veneer":   ("veneer", "oak", "teak", "walnut", "rosewood", "burl", "mahogany", "ply veneer"),
    "Laminate": ("laminate", "mica", "sunmica", "hpl", "cladding sheet"),
    "Stone":    ("marble", "granite", "quartz", "quartzite", "onyx", "travertine", "limestone", "sandstone", "slab"),
    "Tile":     ("tile", "porcelain", "vitrified", "ceramic", "mosaic"),
    "Fabric":   ("fabric", "textile", "upholstery", "linen", "cotton", "velvet", "jute"),
    "Paint":    ("paint", "emulsion", "primer", "distemper", "colour swatch"),
    "Lighting": ("chandelier", "pendant lamp", "led strip", "luminaire", "sconce"),
    "Hardware": ("hinge", "handle", "knob", "screw", "bracket"),
    "Furniture":("sofa", "chair", "table", "bed frame", "wardrobe"),
}

# Sprint 4 (post-generalization fix): a "strong" keyword is one that
# DECLARES the material family (e.g. "laminate", "marble", "porcelain"),
# not a species / pattern descriptor that any material could use
# (e.g. "oak" is a wood species that laminate, veneer, tile and paint
# all borrow). When a strong keyword hits, its category outranks any
# weak-keyword-only category, regardless of raw hit count. This stops
# wood-grain laminate catalogues being misclassified as Veneer just
# because they mention "oak" / "teak" as aesthetic descriptors.
_CATEGORY_STRONG_KWS = {
    "Veneer":   ("veneer", "ply veneer"),
    "Laminate": ("laminate", "sunmica", "hpl", "cladding sheet"),
    "Stone":    ("marble", "granite", "quartz", "quartzite", "onyx", "travertine", "limestone", "sandstone", "slab"),
    "Tile":     ("tile", "porcelain", "vitrified", "ceramic", "mosaic"),
    "Fabric":   ("fabric", "textile", "upholstery", "velvet", "jute"),
    "Paint":    ("paint", "emulsion", "primer", "distemper"),
    "Lighting": ("chandelier", "pendant lamp", "led strip", "luminaire", "sconce"),
    "Hardware": ("hinge", "handle", "knob", "bracket"),
    "Furniture":("sofa", "chair", "table", "bed frame", "wardrobe"),
}

_REGION_REJECT_KWS = {
    "CERTIFICATION": ("certificate", "certification", "iso 9001", "iso 14001", "greenguard", "warranty", "conformity"),
    "SPECIFICATION_TABLE": ("technical specification", "product specifications", "sr no", "sr. no", "dimension"),
    "LOGO": ("trademark", "® ", "registered mark"),
    "LIFESTYLE_IMAGE": ("living room", "bedroom", "kitchen render", "inspiration", "lifestyle"),
}


def _classify_region(page, rect, local_text: str, page_text: str) -> tuple[str, float, str]:
    """Return `(region_class, confidence 0..1, reason)`. Heuristics-only —
    cheap, deterministic. Vision classification is intentionally NOT
    invoked here to stay within the RC1 credit budget; enable it later
    by wiring `STUDIO_VISION_CLASSIFY=on` in `.env`."""
    haystack = (local_text + "\n" + page_text).lower()

    # 1. Deterministic keyword rejects (certifications / spec tables / lifestyle)
    for cls, kws in _REGION_REJECT_KWS.items():
        if any(k in haystack for k in kws):
            return cls, 0.85, f"nearby text matches {cls.lower()} vocabulary"

    # 2. Pixel-stats rejects — reuses `_looks_like_material_swatch` logic
    #    which already screens QR codes (black/white polarisation) and
    #    lifestyle photos (high 3-channel variance).
    if not _looks_like_material_swatch(page, rect):
        # Distinguish QR from photo via a second pass
        try:
            pix = page.get_pixmap(clip=rect, matrix=fitz.Matrix(0.3, 0.3), alpha=False)
            samples = pix.samples
            n = pix.n
            if n >= 3 and samples:
                total = pix.width * pix.height
                bw = sum(1 for i in range(0, len(samples), n)
                          if max(samples[i], samples[i+1], samples[i+2]) < 40
                          or min(samples[i], samples[i+1], samples[i+2]) > 220)
                if total and bw / total > 0.55:
                    return "QR_CODE", 0.90, "black-white polarised pixel distribution"
        except Exception:
            pass
        return "LIFESTYLE_IMAGE", 0.70, "high colour variance — resembles a photograph"

    # 3. Geometry rejects — tiny near-square = logo
    if rect.width < 90 and rect.height < 90:
        return "LOGO", 0.75, "small square area typical of logos"

    # 4. Text-block: local_text density > pixel content
    if len(local_text) > 120 and (rect.width * rect.height) < (page.rect.width * page.rect.height * 0.10):
        return "TEXT_BLOCK", 0.65, "region is small and text-heavy"

    # 5. Default: accept as material swatch, moderate confidence
    return "MATERIAL_SWATCH", 0.75, "pixel stats + geometry consistent with a material swatch"


def _verify_category(detected_text: str, hint: str | None) -> tuple[str | None, float, bool]:
    """Return `(category, confidence 0..1, hint_conflict)`. Pure text
    classification against `_CATEGORY_KEYWORDS`. Strong (family-declaring)
    keywords outrank weak (aesthetic-descriptor) keywords so a laminate
    catalogue mentioning "oak / teak" doesn't get misclassified as
    Veneer. Hint is a tie-breaker only when detection is ambiguous."""
    ctx = (detected_text or "").lower()
    strong_scores: dict[str, int] = {}
    weak_scores: dict[str, int] = {}
    for cat, kws in _CATEGORY_KEYWORDS.items():
        strong_kws = _CATEGORY_STRONG_KWS.get(cat, ())
        strong_h = sum(1 for k in strong_kws if k in ctx)
        weak_h = sum(1 for k in kws if k in ctx and k not in strong_kws)
        if strong_h:
            strong_scores[cat] = strong_h
        if weak_h:
            weak_scores[cat] = weak_h

    # 1. Strong keyword hits ALWAYS win. Category with the most strong
    #    hits is the detected category; confidence stays high.
    if strong_scores:
        detected = max(strong_scores, key=strong_scores.get)
        conf = 0.9 if strong_scores[detected] >= 2 else 0.75
        conflict = bool(hint) and hint in _CATEGORY_KEYWORDS and hint != detected
        return detected, conf, conflict

    # 2. Fall back to weak-only detection (rare — e.g. text says only
    #    "oak" with no material family word).
    if weak_scores:
        detected = max(weak_scores, key=weak_scores.get)
        conf = 0.6 if weak_scores[detected] >= 2 else 0.4
        conflict = bool(hint) and hint in _CATEGORY_KEYWORDS and hint != detected
        return detected, conf, conflict

    # 3. No detected category. Fall back to hint with low confidence.
    if hint in _CATEGORY_KEYWORDS:
        return hint, 0.30, False
    return None, 0.20, False


def _infer_catalogue_brand(pdf, sample_pages: int = 3) -> str | None:
    """Scan the OCR/text of the first few pages once to pick a brand.
    Prevents the "Unknown Brand × 92" symptom."""
    known = ("greenlam", "advance", "asian paints", "kajaria", "somany",
             "merino", "century", "royale touche", "sarom", "eurotex",
             "wooden street", "welspun", "d'decor")
    hits: dict[str, int] = {}
    try:
        for i in range(min(sample_pages, pdf.page_count)):
            t = (pdf[i].get_text("text") or "").lower()
            if not t.strip():
                t = _pdf_page_ocr_text(pdf[i], dpi=140).lower()
            for b in known:
                if b in t:
                    hits[b] = hits.get(b, 0) + 1
    except Exception:
        return None
    if not hits:
        return None
    top = max(hits, key=hits.get)
    return top.title() if hits[top] >= 1 else None


def _extract_records_from_pdf(pdf_bytes: bytes, upload_id: str) -> tuple[list[dict], dict]:
    """Smart catalogue ingestion (Sprint 8.6). A single page may contain
    multiple material swatches; we identify each swatch rectangle
    independently, sample its dominant colour, and associate the *nearest*
    text block for the material name & code. Empty / cover / warranty pages
    are skipped when no swatch candidates are found. OCR runs per-page only
    when there is no embedded text and per-swatch when the embedded text
    doesn't reach the swatch (lifestyle spreads).

    Returns `(records, meta)`."""
    import re
    records: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    # ── STAGE 1: PDF VALIDATION ─────────────────────────────────────────
    try:
        pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        logger.exception("[studio %s] PDF_VALIDATION failed: %s", upload_id, e)
        raise HTTPException(status_code=400, detail=f"Could not parse PDF: {e}")
    total_pages = pdf.page_count
    logger.info("[studio %s] PDF_VALIDATION ok — %d page(s), %.1f MB",
                upload_id, total_pages, len(pdf_bytes) / (1024 * 1024))

    # Sprint 4: catalogue-level brand + category-hint lookup (once per PDF)
    catalogue_brand = _infer_catalogue_brand(pdf)
    category_hint_upload: str | None = None
    region_rejects: dict[str, int] = {k: 0 for k in REGION_CLASSES if k != "MATERIAL_SWATCH"}
    logger.info("[studio %s] BRAND_DETECTION result=%r", upload_id, catalogue_brand)

    pages_with_swatches = 0
    pages_ocr_attempted = 0
    pages_ocrd = 0
    page_level_fallback_count = 0
    per_strip_vision_calls = 0
    ocr_available = _tesseract_available()
    # Per-strip OCR budget for cloud vision. Prevents runaway costs on
    # very-high-density pages. Per swatch call = ~$0.0004; we cap at 8
    # per page and 250 per catalogue.
    PER_PAGE_STRIP_CAP = 8
    PER_CATALOGUE_STRIP_CAP = 250
    # Adaptive OCR DPI — very large PDFs use a smaller raster so we stay
    # under the container memory ceiling. 200 DPI for <=15 pages, 160
    # for 15–40 pages, 130 for 40+ pages.
    if total_pages <= 15:
        ocr_dpi = 200
    elif total_pages <= 40:
        ocr_dpi = 160
    else:
        ocr_dpi = 130
    code_re = re.compile(r"\b([A-Z]{1,4}[-]?\d{2,5}(?:[-]?[A-Z0-9]{1,3})?)\b")

    # Filename → brand hint. Falls through to per-swatch text scan below.
    fname_l = ""
    for cat_items in CATEGORY_SETS.values():
        for it in cat_items:
            if it["brand"].lower() in fname_l:
                pass

    for pi, page in enumerate(pdf, start=1):
        # ── STAGE 2: PAGE RENDERING (implicit via fitz) ─────────────────
        # ── STAGE 3: OCR (only when needed) ─────────────────────────────
        page_text = page.get_text("text") or ""
        used_ocr = False
        if not page_text.strip() and ocr_available:
            pages_ocr_attempted += 1
            page_text = _pdf_page_ocr_text(page, dpi=ocr_dpi)
            if page_text.strip():
                pages_ocrd += 1
                used_ocr = True
        # ── STAGE 4: LAYOUT + SWATCH DETECTION ──────────────────────────
        swatch_rects = _detect_swatches_on_page(page)
        # ── STAGE 4b: Type-B (scanned single-page-image) fallback ───────
        # If detection finds nothing but the page IS a full-page raster
        # image (very common in scanned supplier catalogues), emit a
        # single page-level candidate record. This ensures scanned
        # catalogues never yield zero records — the admin can then
        # verify / split them in the Review Queue.
        if not swatch_rects:
            fallback_rect = _page_level_fallback_rect(page)
            if fallback_rect is not None:
                swatch_rects = [fallback_rect]
                page_level_fallback_count += 1
                logger.info("[studio %s] page %d — SWATCH_DETECTION fell back "
                            "to page-level image (scanned catalogue)",
                            upload_id, pi)
        if not swatch_rects:
            logger.debug("[studio %s] page %d — no swatches detected", upload_id, pi)
            continue
        pages_with_swatches += 1
        logger.info("[studio %s] page %d — %d swatch(es); text_chars=%d ocr=%s",
                    upload_id, pi, len(swatch_rects), len(page_text), used_ocr)

        # ── Page-title / collection-name detection (Sprint 3 B) ──────────
        # The first "name-like" line of the whole-page OCR is very likely
        # the collection title. We store it as `collection_name` PAGE
        # METADATA on every record from this page but NEVER copy it into
        # `material_name` — that field must belong to each individual
        # swatch, not to the page.
        page_lines_top = [ln.strip() for ln in page_text.splitlines()[:6] if ln.strip()]
        page_title_candidate: str | None = None
        for ln in page_lines_top:
            if _looks_like_name_stub(ln):
                page_title_candidate = ln[:120]
                break

        # Track names picked on this page to detect intra-page duplicates
        # (Sprint 3 A1). Two swatches with the same auto-picked name is a
        # classic symptom of "we accidentally used the page title" — we
        # blank the later ones and mark them needs_review.
        page_names_seen: dict[str, int] = {}
        # Track how many per-strip vision calls we've done on this page.
        page_strip_calls = 0
        # A page has "multi-swatch ambiguity" if it contains >1 swatch —
        # this unlocks per-strip vision OCR (Sprint 3 A2).
        multi_swatch_page = len(swatch_rects) > 1

        for si, rect in enumerate(swatch_rects, start=1):
            # A. Local text: prefer PDF text blocks near the swatch.
            local = _nearest_text_for_rect(page, rect)
            # B. Per-swatch strip OCR — Sprint 3 A2.
            #    Enabled when the page has multiple swatches (ambiguous
            #    label-to-swatch mapping) AND we haven't hit either the
            #    per-page or per-catalogue cost cap. Runs against the
            #    OCR provider chain — cheap on tesseract, ~$0.0004 per
            #    call on GPT-4o-mini vision.
            local_thin = len(local) < 8
            can_local = _has_local_ocr()
            can_vision_strip = (
                multi_swatch_page
                and _ocr_available()
                and page_strip_calls < PER_PAGE_STRIP_CAP
                and per_strip_vision_calls < PER_CATALOGUE_STRIP_CAP
            )
            if local_thin and (can_local or can_vision_strip):
                strip_text = _ocr_text_below_swatch(page, rect)
                if strip_text:
                    local = strip_text
                    if not can_local:
                        page_strip_calls += 1
                        per_strip_vision_calls += 1
            haystack = f"{local}\n{page_text}"
            haystack_l = haystack.lower()

            # ── Sprint 4: Region classification gate ────────────────────
            # Every candidate region is classified BEFORE it can become a
            # material record. Only MATERIAL_SWATCH continues; everything
            # else (logos, QRs, certifications, lifestyle photos, spec
            # tables, decorative graphics) is dropped here.
            region_class, region_conf, region_reason = _classify_region(
                page, rect, local, page_text
            )
            if region_class != "MATERIAL_SWATCH":
                region_rejects[region_class] = region_rejects.get(region_class, 0) + 1
                logger.info(
                    "[studio %s] page %d swatch %d REJECTED as %s (conf=%.2f — %s)",
                    upload_id, pi, si, region_class, region_conf, region_reason,
                )
                continue

            # C. Material name = first strong line from local text; fall
            # back to the first meaningful whole-page line. Only accepts
            # lines that look like real product names (mostly alphabetic
            # tokens, no obvious OCR gibberish).
            def _looks_like_name(s: str) -> bool:
                if not s or len(s) < 4 or len(s) > 80:
                    return False
                if s.isnumeric():
                    return False
                # First non-space character must be a letter — OCR
                # noise like "@] ADVANCE" or "| B48" fails here.
                first = next((c for c in s if not c.isspace()), "")
                if not first.isalpha():
                    return False
                letters = sum(1 for c in s if c.isalpha())
                if letters < 4:
                    return False
                if letters / max(1, len(s)) < 0.55:
                    return False
                low = s.lower()
                if any(bad in low for bad in ("qr code", "www.", "http", "copyright", "index", "warranty", "i'm sorry", "cannot transcribe", "can't transcribe")):
                    return False
                return True

            # Code (SKU-like token) — searched in local text first (a code
            # near the swatch is stronger evidence than a code anywhere on
            # the page).
            local_l = local.lower()
            local_code_match = code_re.search(local)
            code_match = local_code_match or code_re.search(page_text)
            material_code = code_match.group(1) if code_match else None

            # D. Product-context filter — a genuine material swatch is
            # almost always paired with a nearby product name / code /
            # keyword. We require at least one of:
            #   (a) A material-family keyword in the surrounding text, or
            #   (b) A code-like token in the local text.
            # LOCAL text is preferred (strong evidence), but when the OCR
            # provider is a whole-page vision LLM (no per-swatch bboxes)
            # we accept a page-wide keyword hit as sufficient context.
            # This drops QR codes, logos, decorative circles and banners
            # that happen to sit on the same page as real swatches while
            # still working with both tesseract and vision OCR.
            _kw = (
                "laminate", "veneer", "wood", "oak", "walnut", "teak",
                "marble", "stone", "matte", "matt", "gloss", "finish",
                "colour", "color", "tile", "porcelain", "ceramic", "linen",
                "fabric", "leather", "paint", "shade", "texture", "grain",
                "polish", "rustic", "brushed", "suede", "silk", "satin",
                "mica", "sunmica", "acrylic", "hpl", "mdf", "ply",
                "beige", "grey", "gray", "brown", "cream", "ivory",
                "black", "white", "red", "blue", "green", "gold",
            )
            has_local_code = local_code_match is not None
            local_has_material_kw = any(k in local_l for k in _kw)
            page_has_material_kw = any(k in haystack_l for k in _kw)
            if not (has_local_code or local_has_material_kw or page_has_material_kw):
                # Scanned Type-B pages sometimes have OCR text that doesn't
                # hit any material keyword (very short OCR yield). We still
                # emit ONE record per page so the admin can review + label
                # it manually — better than showing "no records" for an
                # entire catalogue.
                is_page_level_fallback = (
                    si == 1
                    and len(swatch_rects) == 1
                    and rect.width * rect.height >= page.rect.width * page.rect.height * 0.30
                )
                if not is_page_level_fallback:
                    continue

            # E. Material name selection.
            local_lines = [ln.strip() for ln in local.splitlines() if ln.strip()]
            name = next((ln for ln in local_lines if _looks_like_name(ln)), None)
            if not name:
                page_lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
                name = next((ln for ln in page_lines if _looks_like_name(ln)), None)
            # If we ended up with neither a real name nor a local code,
            # this is almost certainly a decorative element — skip UNLESS
            # this is the page-level Type-B fallback candidate, in which
            # case we ship a placeholder name for the admin to correct.
            if not name and not local_code_match:
                is_page_level_fallback = (
                    si == 1
                    and len(swatch_rects) == 1
                    and rect.width * rect.height >= page.rect.width * page.rect.height * 0.30
                )
                if not is_page_level_fallback:
                    continue
                name = f"Scanned page {pi}"
            if not name:
                name = f"Swatch {pi}.{si}"
            name = name[:120]

            # F. Brand hint from any known brand appearing in text.
            brand_hint = None
            for cat_items in CATEGORY_SETS.values():
                for it in cat_items:
                    if it["brand"].lower() in haystack_l:
                        brand_hint = it["brand"]
                        break
                if brand_hint:
                    break

            # F. Category verification (Sprint 4).
            cat_guess_legacy = _classify_category_from_text(haystack_l)
            detected_cat, cat_conf, cat_conflict = _verify_category(
                haystack_l, category_hint_upload,
            )
            cat_guess = detected_cat or cat_guess_legacy
            family = cat_guess.rstrip("s") if cat_guess in {"Paints", "Laminates", "Veneers", "Tiles"} else cat_guess

            # G. Colour + thumbnail from the swatch clip.
            color_hex, thumb_b64 = _swatch_dominant_hex_and_thumb(page, rect)

            # H. Confidence — better when we found both a name candidate
            # AND a code; lower when name is auto-generated / falls back.
            conf = 60
            if name and not name.startswith("Swatch ") and not name.startswith("Scanned page "):
                conf += 20
            if material_code:
                conf += 15
            if brand_hint:
                conf += 5
            # Type-B page-level fallback always needs admin review.
            is_page_level_fallback = (
                si == 1
                and len(swatch_rects) == 1
                and rect.width * rect.height >= page.rect.width * page.rect.height * 0.30
                and page_level_fallback_count > 0
            )
            if is_page_level_fallback:
                conf = min(conf, 50)

            # Sprint 3 A1 — intra-page duplicate detection.
            # If we've already emitted a swatch with this exact name on
            # THIS page, we're almost certainly copying the collection
            # title. Blank the name to a swatch placeholder and mark the
            # record `needs_review` so the admin corrects it.
            duplicate_of_page_title = False
            if name:
                key = name.strip().lower()
                if key == (page_title_candidate or "").strip().lower() and page_title_candidate:
                    duplicate_of_page_title = True
                if key in page_names_seen:
                    duplicate_of_page_title = True
                page_names_seen[key] = page_names_seen.get(key, 0) + 1
            if duplicate_of_page_title:
                name = f"Swatch p{pi}.s{si}"
                conf = min(conf, 45)

            # Sprint 4: consolidated needs_review with structured reasons
            needs_review_reasons: list[str] = []
            if region_conf < 0.65:
                needs_review_reasons.append("low_region_confidence")
            if not name or name.startswith("Swatch ") or name.startswith("Scanned page "):
                needs_review_reasons.append("no_label")
            if not material_code:
                needs_review_reasons.append("no_code")
            if duplicate_of_page_title:
                needs_review_reasons.append("duplicate_name")
            if cat_conflict:
                needs_review_reasons.append("category_conflict")
            if not (brand_hint or catalogue_brand):
                needs_review_reasons.append("brand_unknown")
            if is_page_level_fallback:
                needs_review_reasons.append("page_level_fallback")
            if cat_guess not in _CATEGORY_KEYWORDS:
                needs_review_reasons.append("unsupported_category")

            needs_review = bool(needs_review_reasons) or conf < 65
            conf = min(95, conf)
            final_brand = brand_hint or catalogue_brand

            # Sprint 6 — visual fingerprint (pHash + dHash + wHash) computed
            # from the ISOLATED per-swatch crop we just produced. This is
            # the only image the hash is ever generated from — never the
            # full page, never a lifestyle render, never a solid colour
            # block. Used by the user-matcher for exact / near-exact
            # loopback matching before fuzzy text ranking.
            from visual_hash import compute_visual_hashes
            visual_hashes = compute_visual_hashes(thumb_b64)

            records.append({
                "id": str(uuid.uuid4()),
                "upload_id": upload_id,
                "brand": final_brand,
                "collection": page_title_candidate,
                "collection_name": page_title_candidate,   # Sprint 3 B
                "material_name": name,                     # per-swatch identity
                "material_code": material_code,
                "category": cat_guess,
                "material_family": family,
                "finish": None,
                "variant": None,                           # Sprint 4
                "color_name": None,
                "color_hex": color_hex,
                "secondary_color_hex": None,               # Sprint 4
                "texture": None,
                "pattern": None,
                "gloss_level": None,                       # Sprint 4
                "application": None,
                "page_number": pi,
                "swatch_index_on_page": si,
                "swatch_bbox": [round(rect.x0, 1), round(rect.y0, 1),
                                round(rect.x1, 1), round(rect.y1, 1)],
                "page_preview_b64": thumb_b64,
                "visual_hashes": visual_hashes,           # Sprint 6
                "status": "draft",
                "needs_review": needs_review,              # Sprint 3 C
                "needs_review_reasons": needs_review_reasons,     # Sprint 4
                "is_page_level_fallback": is_page_level_fallback, # Sprint 3 D
                "region_class": region_class,              # Sprint 4
                "region_confidence": round(region_conf, 2),
                "swatch_verified": True,
                "swatch_verification_reason": region_reason,
                "label_association_confidence": 0.8 if (name and not name.startswith("Swatch ")) else 0.4,
                "category_confidence": round(cat_conf, 2),
                "category_hint": category_hint_upload,
                "category_hint_conflict": cat_conflict,
                "keywords": [w.lower() for w in re.findall(r"[a-zA-Z]{4,}", name)][:8],
                "created_at": now,
                "published_at": None,
                "extraction_mode": "ocr" if used_ocr else "text",
                "confidence": conf,
            })

    pdf.close()
    # ── STAGE 5: RECORD GENERATION summary ──────────────────────────────
    logger.info(
        "[studio %s] RECORD_GENERATION — pages=%d pages_with_swatches=%d "
        "pages_ocrd=%d page_level_fallback=%d strip_vision_calls=%d records=%d "
        "region_rejects=%s catalogue_brand=%r",
        upload_id, total_pages, pages_with_swatches, pages_ocrd,
        page_level_fallback_count, per_strip_vision_calls, len(records),
        region_rejects, catalogue_brand,
    )

    if records:
        mode = "ocr" if pages_ocrd and pages_ocrd == pages_with_swatches else (
            "text+ocr" if pages_ocrd else "text"
        )
        meta = {
            "total_pages": total_pages,
            "pages_with_swatches": pages_with_swatches,
            "pages_ocrd": pages_ocrd,
            "pages_ocr_attempted": pages_ocr_attempted,
            "page_level_fallback_count": page_level_fallback_count,
            "extraction_mode": mode,
            "failure_reason": None,
            "region_rejects": region_rejects,     # Sprint 4
            "catalogue_brand": catalogue_brand,   # Sprint 4
        }
    else:
        if total_pages == 0:
            reason = "PDF has no pages."
        elif not ocr_available and pages_ocr_attempted == 0:
            reason = ("This catalogue appears to be image-based (no machine-readable "
                      "text) and no OCR provider is available. Set EMERGENT_LLM_KEY "
                      "for the GPT-4o-mini Vision fallback, or install the tesseract "
                      "binary.")
        elif pages_ocr_attempted > 0 and pages_ocrd == 0:
            reason = ("OCR completed but returned no readable text. The catalogue may be "
                      "a very low-resolution scan or contain only decorative graphics.")
        else:
            reason = ("No material swatches were detected on any page. This can happen "
                      "when the catalogue's images are decorative-only (QR codes, "
                      "lifestyle photos, logos) or use an unsupported layout. Use "
                      "the Preview page action to inspect what the extractor saw.")
        meta = {
            "total_pages": total_pages,
            "pages_with_swatches": 0,
            "pages_ocrd": pages_ocrd,
            "pages_ocr_attempted": pages_ocr_attempted,
            "page_level_fallback_count": page_level_fallback_count,
            "extraction_mode": "failed",
            "failure_reason": reason,
            "region_rejects": region_rejects,     # Sprint 4
            "catalogue_brand": catalogue_brand,   # Sprint 4
        }
    return records, meta


STUDIO_MAX_UPLOAD_BYTES = 150 * 1024 * 1024  # 150 MB — supplier catalogues.
# 2026-02-01 (round 5) — founder approved raising the user cap to match
# admin's headroom (was 25 MB). Real supplier catalogues frequently
# exceed 25 MB; the 20-uploads-per-user hard cap already bounds total
# per-user footprint (theoretical worst case 20 × 150 MB = 3 GB on
# disk + ~320 MB in Mongo per user — manageable on standard block
# storage, flagged for future capacity planning if user growth hits
# thousands of heavy uploaders).
USER_LIBRARY_MAX_UPLOAD_BYTES = int(
    os.environ.get("USER_LIBRARY_MAX_UPLOAD_BYTES", str(150 * 1024 * 1024))
)
# Per-user upload count ceiling — bounds how much space a single account
# can consume and keeps the on-demand DB fetch cheap.
USER_LIBRARY_MAX_UPLOADS = int(
    os.environ.get("USER_LIBRARY_MAX_UPLOADS", "20")
)
STUDIO_UPLOAD_DIR = os.environ.get("STUDIO_UPLOAD_DIR", "/app/backend/uploads_data")


async def _run_studio_extraction(upload_id: str, data: bytes, filename: str,
                                 catalogue_scope: str = "admin",
                                 uploaded_by: str | None = None) -> None:
    """Run PDF extraction OUT-OF-BAND (background task). Never raises —
    always leaves the upload in a terminal state (`review`/`published`
    on success, `failed` with a diagnostic on any error). Runs the
    CPU-heavy work in a thread pool so the FastAPI event loop stays
    responsive (login, dashboards and KE search keep working while a
    large scan is being OCR'd).

    2026-02-01 (round 4) — `catalogue_scope`:
      * `"admin"`: extracted records land in status='review' — admin
        must approve them before they enter the global index (existing
        behaviour).
      * `"user"`: extracted records land in status='published' AND get
        stamped with `catalogue_scope='user'` + `uploaded_by=<user_id>`
        so they're immediately searchable in the OWNER'S scope but
        never leak into the admin/global catalogue."""
    import asyncio
    try:
        logger.info("[studio %s] UPLOAD accepted — filename=%r size=%.1fMB scope=%s",
                    upload_id, filename, len(data) / (1024 * 1024), catalogue_scope)
        # Off-load the CPU-bound extractor to a worker thread so the
        # asyncio event loop is free for all other requests.
        records, meta = await asyncio.to_thread(
            _extract_records_from_pdf, data, upload_id
        )
        # 2026-02-01 (round 4) — stamp scope + ownership + auto-publish
        # for user uploads so they're immediately usable in the owner's
        # library without needing an admin review step.
        if records and catalogue_scope == "user":
            for r in records:
                r["catalogue_scope"] = "user"
                r["uploaded_by"] = uploaded_by
                r["status"] = "published"
        elif records:
            for r in records:
                r.setdefault("catalogue_scope", "admin")
        if records:
            await db.ke_records.insert_many(records)
            logger.info("[studio %s] DATABASE_SAVE — inserted %d record(s) (scope=%s)",
                        upload_id, len(records), catalogue_scope)
        if catalogue_scope == "user":
            status = "published" if records else "failed"
        else:
            status = "review" if records else "failed"
        update_fields = {
            "status": status,
            "page_count": meta["total_pages"],
            "records_extracted": len(records),
            "extraction_mode": meta["extraction_mode"],
            "failure_reason": meta["failure_reason"] or (
                None if records else "No publishable material records found."
            ),
            "pages_ocrd": meta.get("pages_ocrd", 0),
            "pages_with_text": meta.get("pages_with_text", 0),
            "page_level_fallback_count": meta.get("page_level_fallback_count", 0),
            "region_rejects": meta.get("region_rejects"),       # Sprint 4
            "catalogue_brand": meta.get("catalogue_brand"),     # Sprint 4
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.ke_uploads.update_one({"id": upload_id}, {"$set": update_fields})
        logger.info("[studio %s] FINAL_STATUS = %s (records=%d, scope=%s)",
                    upload_id, status, len(records), catalogue_scope)
        # Admin scope: refresh the global cache so newly-approved
        # records show up in matcher immediately. User scope: no
        # global cache to refresh — records are fetched on-demand
        # per-search via `_load_user_catalogue_records`.
        if catalogue_scope == "admin":
            await _refresh_studio_index()
    except Exception as e:
        logger.exception("[studio %s] EXTRACTION_CRASHED filename=%r",
                         upload_id, filename)
        # Best-effort — always record a diagnostic on failure so the
        # admin sees a meaningful message in the Processing Queue.
        try:
            await db.ke_uploads.update_one(
                {"id": upload_id},
                {"$set": {
                    "status": "failed",
                    "failure_reason": f"Extraction crashed: {type(e).__name__}: {e}"[:400],
                    "extracted_at": datetime.now(timezone.utc).isoformat(),
                }},
            )
        except Exception:
            logger.exception("failed to write failure state for upload %s", upload_id)


@api_router.post("/admin/studio/upload")
async def studio_upload(
    file: UploadFile = File(...),
    category_hint: str | None = Form(default=None),
    user: dict = Depends(require_admin),
):
    """MaterialMatch Studio — upload a supplier PDF. The request returns
    immediately with the upload id and status='processing'; extraction
    then runs in a background task so it never blocks the event loop or
    times out at the ingress. Poll `GET /admin/studio/uploads` (or the
    Processing Queue in the UI) to observe status transitions:
    `processing → review → published / partially_published / archived / failed`."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")
    data = await file.read()
    if len(data) > STUDIO_MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"PDF too large (max {STUDIO_MAX_UPLOAD_BYTES // (1024 * 1024)} MB)",
        )
    # Cheap validation up-front so we never spawn a background task for a
    # non-PDF blob. `fitz.open` is the fastest way to reject garbage.
    try:
        _probe = fitz.open(stream=data, filetype="pdf")
        _probe.close()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Not a valid PDF: {e}")
    upload_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    try:
        os.makedirs(STUDIO_UPLOAD_DIR, exist_ok=True)
        with open(os.path.join(STUDIO_UPLOAD_DIR, f"{upload_id}.pdf"), "wb") as fh:
            fh.write(data)
    except Exception:
        logger.exception("failed to persist upload blob")
    upload_doc = {
        "id": upload_id,
        "filename": file.filename,
        "size_bytes": len(data),
        "uploaded_by": user.get("id"),
        "catalogue_scope": "admin",
        "status": "processing",
        "page_count": 0,
        "records_extracted": 0,
        "extraction_mode": None,
        "failure_reason": None,
        "created_at": now,
        "has_blob": True,
        # Sprint 3 F — hint only, never overrides AI reasoning.
        "category_hint": category_hint or None,
    }
    await db.ke_uploads.insert_one(upload_doc)
    # Fire-and-forget: the caller sees a 202-style response instantly.
    asyncio.create_task(_run_studio_extraction(
        upload_id, data, file.filename,
        catalogue_scope="admin", uploaded_by=user.get("id"),
    ))
    return {
        "upload_id": upload_id,
        "filename": file.filename,
        "status": "processing",
        "records_extracted": 0,
        "page_count": 0,
        "extraction_mode": None,
        "failure_reason": None,
        "message": "Upload accepted. Extraction is running in the background — refresh the Processing Queue in a moment.",
    }


def _clean_upload(u: dict) -> dict:
    return {k: v for k, v in u.items() if k != "_id"}


def _clean_record(r: dict) -> dict:
    return {k: v for k, v in r.items() if k != "_id"}


# ============================================================================
# 2026-02-01 (round 4) — USER-UPLOADABLE CATALOGUES
# ---------------------------------------------------------------------------
# Reuses the exact same background extraction pipeline the admin Studio
# uses (`_run_studio_extraction` → `_extract_records_from_pdf`), but:
#   * caps upload size + count per user (USER_LIBRARY_MAX_UPLOAD_BYTES /
#     USER_LIBRARY_MAX_UPLOADS)
#   * auto-publishes extracted records (no admin review queue)
#   * stamps every upload + record with `catalogue_scope='user'` and
#     `uploaded_by=<user_id>` so retrieval never mixes them into the
#     admin/global scope
# Retrieval honours the `library_scope` param on the analyse endpoints
# so a search is always confined to exactly one scope per user's
# explicit choice.
# ============================================================================
@api_router.post("/library/uploads")
async def library_user_upload(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Accept a supplier PDF from a non-admin user, run the same real
    ingestion pipeline the admin Studio uses, and auto-publish the
    extracted records into the user's own private catalogue scope."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")
    data = await file.read()
    if len(data) > USER_LIBRARY_MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(f"PDF too large (max {USER_LIBRARY_MAX_UPLOAD_BYTES // (1024 * 1024)} "
                    "MB for user uploads)."),
        )
    # Per-user upload count cap — count only NON-archived, non-deleted
    # uploads so the user can delete old ones to free slots.
    existing = await db.ke_uploads.count_documents({
        "catalogue_scope": "user",
        "uploaded_by": user["id"],
        "status": {"$ne": "archived"},
    })
    if existing >= USER_LIBRARY_MAX_UPLOADS:
        raise HTTPException(
            status_code=429,
            detail=(f"Upload limit reached ({USER_LIBRARY_MAX_UPLOADS} PDFs). "
                    "Delete an existing catalogue to free a slot."),
        )
    # Validate the PDF is real (cheap probe) before spawning the
    # background task.
    try:
        _probe = fitz.open(stream=data, filetype="pdf")
        _probe.close()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Not a valid PDF: {e}")
    upload_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    try:
        os.makedirs(STUDIO_UPLOAD_DIR, exist_ok=True)
        with open(os.path.join(STUDIO_UPLOAD_DIR, f"{upload_id}.pdf"), "wb") as fh:
            fh.write(data)
    except Exception:
        logger.exception("failed to persist user-upload blob")
    upload_doc = {
        "id": upload_id,
        "filename": file.filename,
        "size_bytes": len(data),
        "uploaded_by": user["id"],
        "catalogue_scope": "user",
        "status": "processing",
        "page_count": 0,
        "records_extracted": 0,
        "extraction_mode": None,
        "failure_reason": None,
        "created_at": now,
        "has_blob": True,
    }
    await db.ke_uploads.insert_one(upload_doc)
    asyncio.create_task(_run_studio_extraction(
        upload_id, data, file.filename,
        catalogue_scope="user", uploaded_by=user["id"],
    ))
    return {
        "upload_id": upload_id,
        "filename": file.filename,
        "status": "processing",
        "message": (
            "Upload accepted. We're extracting materials in the background — "
            "check back in a moment."
        ),
    }


@api_router.get("/library/uploads")
async def library_list_user_uploads(user: dict = Depends(get_current_user)):
    """List this user's own uploaded catalogues (never admin uploads)."""
    docs = await db.ke_uploads.find({
        "catalogue_scope": "user",
        "uploaded_by": user["id"],
    }).sort("created_at", -1).to_list(200)
    return {
        "uploads": [_clean_upload(d) for d in docs],
        "quota": {
            "used": len(docs),
            "max": USER_LIBRARY_MAX_UPLOADS,
            "max_bytes": USER_LIBRARY_MAX_UPLOAD_BYTES,
        },
    }


@api_router.get("/library/records")
async def library_list_user_records(user: dict = Depends(get_current_user)):
    """List all published catalogue records this user owns — the raw
    material entries the analyse pipeline will match against when the
    caller opts into `library_scope=own`."""
    docs = await db.ke_records.find({
        "catalogue_scope": "user",
        "uploaded_by": user["id"],
        "status": "published",
    }).sort("created_at", -1).to_list(2000)
    return {"records": [_clean_record(d) for d in docs], "count": len(docs)}


@api_router.delete("/library/uploads/{upload_id}")
async def library_delete_user_upload(upload_id: str,
                                     user: dict = Depends(get_current_user)):
    """Delete a user's own upload AND every catalogue record extracted
    from it. Never touches admin uploads (ownership is verified)."""
    up = await db.ke_uploads.find_one({
        "id": upload_id,
        "catalogue_scope": "user",
        "uploaded_by": user["id"],
    })
    if not up:
        raise HTTPException(status_code=404, detail="Upload not found.")
    # Purge associated records first so retrieval can never resurrect
    # them.
    rec_res = await db.ke_records.delete_many({"upload_id": upload_id})
    up_res = await db.ke_uploads.delete_one({"id": upload_id})
    # Best-effort: remove the persisted PDF blob too.
    blob_path = os.path.join(STUDIO_UPLOAD_DIR, f"{upload_id}.pdf")
    try:
        if os.path.exists(blob_path):
            os.remove(blob_path)
    except Exception:
        logger.exception("failed to remove upload blob %s", blob_path)
    return {
        "ok": True,
        "upload_deleted": up_res.deleted_count,
        "records_deleted": rec_res.deleted_count,
    }


@api_router.delete("/library/records/{record_id}")
async def library_delete_user_record(record_id: str,
                                     user: dict = Depends(get_current_user)):
    """Delete a single catalogue record from the user's own library."""
    try:
        oid = ObjectId(record_id)
    except Exception:
        # Not all records have ObjectId string ids; some carry `id`
        # UUID strings. Try both.
        oid = None
    query = {"catalogue_scope": "user", "uploaded_by": user["id"]}
    if oid is not None:
        query["_id"] = oid
    else:
        query["id"] = record_id
    rec = await db.ke_records.find_one(query)
    if not rec:
        raise HTTPException(status_code=404, detail="Record not found.")
    await db.ke_records.delete_one({"_id": rec["_id"]})
    return {"ok": True}


@api_router.get("/admin/studio/uploads")
async def studio_list_uploads(user: dict = Depends(require_admin)):
    docs = await db.ke_uploads.find().sort("created_at", -1).to_list(200)
    return {"uploads": [_clean_upload(d) for d in docs]}


@api_router.get("/admin/studio/uploads/{upload_id}/records")
async def studio_upload_records(upload_id: str, user: dict = Depends(require_admin)):
    docs = await db.ke_records.find({"upload_id": upload_id}).sort("page_number", 1).to_list(500)
    return {"upload_id": upload_id, "records": [_clean_record(d) for d in docs]}


# ---------------------------------------------------------------------------
# Scene-segmentation VALIDATION endpoint (admin-only).
#
# Hybrid two-stage pipeline:
#   Stage A — SAM3 (Roboflow) detects architectural objects (bboxes).
#   Stage B — GPT-4o-mini (`generate_swatch_dna`) classifies the material
#             of each detected object's crop.  This is the same production
#             function the live matcher uses on every user-region query.
#
# Rationale: the March-2026 head-to-head test on failed SAM3 cases
# (`/tmp/sam3_hard/`) showed GPT-4o-mini wins 7/7 on hard cases (low-
# contrast material transitions, reflective surfaces, cross-object bleed)
# while running ~2-4x faster than SAM3's material vocab pass.  The result
# is a full Visual DNA dict per object (family, surface_type, color,
# pattern, finish, gloss, canonical_description) instead of a bag of
# vocab-matched sub-masks.
#
# Requires: ROBOFLOW_API_KEY (Stage A) + EMERGENT_LLM_KEY (Stage B).
# ---------------------------------------------------------------------------
# Pass-1 minimum area gate — earlier sweeps found ~5-11 spurious `shelf`
# detections per image at 0.05-0.4% area (decorative slats / pendant
# hardware / cubby dividers).  0.3% clears the < 0.2% clutter while
# keeping 0.3-0.5% legitimate small fixtures (sinks / toilets).
_OBJECT_MIN_AREA_FRAC = 0.003


@api_router.post("/admin/test-scene-segmentation")
async def admin_test_scene_segmentation(
    file: UploadFile = File(...),
    min_confidence: float = Form(default=0.55),
    object_vocab: str | None = Form(default=None),
    user: dict = Depends(require_admin),
):
    """VALIDATION ONLY. Runs the hybrid two-stage pipeline on the uploaded
    image and returns raw nested JSON (no persistence, no downstream side
    effects).

    Form fields:
      file:            image file (jpg / png / webp).
      min_confidence:  optional float (default 0.55) applied by
                       `filter_detections` on Stage-A outputs.
      object_vocab:    optional comma-separated architectural prompts,
                       overrides the built-in ARCHITECTURAL_VOCAB.
    """
    from intelligence.scene_segmentation import (
        ARCHITECTURAL_VOCAB, Sam3Error,
        classify_object_material, detect_objects, filter_detections,
    )

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image too large (max 15 MB)")

    def _parse_vocab(s: str | None, fallback: tuple[str, ...]) -> list[str]:
        if not s:
            return list(fallback)
        items = [t.strip() for t in s.split(",") if t.strip()]
        return items or list(fallback)

    obj_prompts = _parse_vocab(object_vocab, ARCHITECTURAL_VOCAB)

    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        W, H = img.size

        # Stage A — SAM3 object detection.
        obj_raw = detect_objects(img, vocab=obj_prompts)
        objects = filter_detections(
            obj_raw, min_confidence=min_confidence,
            min_area_frac=_OBJECT_MIN_AREA_FRAC,
            image_w=W, image_h=H,
        )

        # Stage B — per-object material classification via
        # generate_swatch_dna (GPT-4o-mini), with deterministic shortcuts
        # for mirror / sink / faucet / plant.  Polygon-masked crops so
        # the classifier only sees pixels SAM3 assigned to the object.
        stage_b_tasks = [
            classify_object_material(
                img, obj["bbox"], obj["label"], EMERGENT_LLM_KEY,
                polygon=obj.get("polygon"),
                object_confidence=float(obj.get("confidence", 0.0)),
            )
            for obj in objects
        ]
        # Run Stage-B calls concurrently — each is a network-bound LLM
        # request, so gather cuts the wall-clock cost by N.
        stage_b_results = await asyncio.gather(*stage_b_tasks, return_exceptions=True)

        object_results = []
        for obj, mat_res in zip(objects, stage_b_results):
            entry = {
                "label": obj["label"],
                "confidence": obj["confidence"],
                "bbox": obj["bbox"],
                "polygon": obj.get("polygon"),
            }
            if isinstance(mat_res, Exception):
                entry["material"] = {
                    "crop_origin": None, "crop_size": None,
                    "source": "error", "material": None,
                    "error": f"{type(mat_res).__name__}: {mat_res}",
                }
            else:
                entry["material"] = mat_res
            object_results.append(entry)

        return {
            "image_size": {"width": W, "height": H},
            "object_vocab": obj_prompts,
            "min_confidence": min_confidence,
            "objects_raw_count": len(obj_raw),
            "objects_kept_count": len(objects),
            "objects": object_results,
        }
    except Sam3Error as e:
        raise HTTPException(status_code=502, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("admin scene-segmentation test failed")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")





class StudioRecordEditPayload(BaseModel):
    brand: str | None = None
    material_name: str | None = None
    material_code: str | None = None
    category: str | None = None
    material_family: str | None = None
    finish: str | None = None
    color_name: str | None = None
    region: str | None = None
    notes: str | None = None
    tags: list[str] | None = None
    keywords: list[str] | None = None
    needs_review: bool | None = None
    collection_name: str | None = None


class StudioBulkPayload(BaseModel):
    record_ids: list[str]
    action: str  # "publish" | "archive" | "reject" | "delete"


@api_router.patch("/admin/studio/records/{record_id}")
async def studio_edit_record(
    record_id: str,
    payload: StudioRecordEditPayload,
    user: dict = Depends(require_admin),
):
    """Manual edit of an extracted material record before / after publish.
    AI does the first-pass extraction, the admin owns the final metadata."""
    updates = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = await db.ke_records.update_one({"id": record_id}, {"$set": updates})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Record not found")
    doc = await db.ke_records.find_one({"id": record_id})
    if doc and doc.get("upload_id"):
        await _recompute_upload_status(doc["upload_id"])
    await _refresh_studio_index()
    return _clean_record(doc)


@api_router.post("/admin/studio/records/bulk")
async def studio_bulk_records(
    payload: StudioBulkPayload,
    user: dict = Depends(require_admin),
):
    """Bulk publish / archive / reject / delete for a set of records.
    Publish is idempotent — records already `published` are skipped so we
    never create duplicate publications."""
    if not payload.record_ids:
        raise HTTPException(status_code=400, detail="record_ids is required")
    now = datetime.now(timezone.utc).isoformat()
    q = {"id": {"$in": payload.record_ids}}
    # Track parent uploads BEFORE the mutation so status recompute still
    # works even for the delete case.
    parents = set()
    async for d in db.ke_records.find(q, {"upload_id": 1}):
        if d.get("upload_id"):
            parents.add(d["upload_id"])
    if payload.action == "publish":
        # Idempotent — skip already-published records to prevent double
        # publications and stale `published_at` overwrites.
        res = await db.ke_records.update_many(
            {**q, "status": {"$ne": "published"}},
            {"$set": {"status": "published", "published_at": now}},
        )
        count = res.modified_count
    elif payload.action == "archive":
        res = await db.ke_records.update_many(
            {**q, "status": {"$ne": "archived"}},
            {"$set": {"status": "archived", "archived_at": now}},
        )
        count = res.modified_count
    elif payload.action == "reject":
        res = await db.ke_records.update_many(
            {**q, "status": {"$ne": "rejected"}},
            {"$set": {"status": "rejected"}},
        )
        count = res.modified_count
    elif payload.action == "delete":
        res = await db.ke_records.delete_many(q)
        count = res.deleted_count
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {payload.action}")
    for uid in parents:
        await _recompute_upload_status(uid)
    await _refresh_studio_index()
    if payload.action == "publish":
        import asyncio
        asyncio.create_task(_visual_dna_backfill())
    return {"action": payload.action, "affected": count}


@api_router.delete("/admin/studio/records/{record_id}")
async def studio_delete_record(record_id: str, user: dict = Depends(require_admin)):
    doc = await db.ke_records.find_one({"id": record_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Record not found")
    upload_id = doc.get("upload_id")
    await db.ke_records.delete_one({"id": record_id})
    if upload_id:
        await _recompute_upload_status(upload_id)
    await _refresh_studio_index()
    return {"deleted": record_id}


@api_router.post("/admin/studio/records/approve")
async def studio_approve(payload: StudioApprovePayload, user: dict = Depends(require_admin)):
    if not payload.record_ids:
        raise HTTPException(status_code=400, detail="record_ids required")
    now = datetime.now(timezone.utc).isoformat()
    # Track parent uploads first so status recompute stays correct.
    parents = set()
    async for d in db.ke_records.find({"id": {"$in": payload.record_ids}}, {"upload_id": 1}):
        if d.get("upload_id"):
            parents.add(d["upload_id"])
    # Idempotent: only flip records that are not yet published — this
    # guarantees a record is never "published twice".
    result = await db.ke_records.update_many(
        {"id": {"$in": payload.record_ids}, "status": {"$ne": "published"}},
        {"$set": {"status": "published", "published_at": now}},
    )
    for uid in parents:
        await _recompute_upload_status(uid)
    await _refresh_studio_index()
    import asyncio
    asyncio.create_task(_visual_dna_backfill())
    return {"approved": result.modified_count}


@api_router.post("/admin/studio/records/reject")
async def studio_reject(payload: StudioApprovePayload, user: dict = Depends(require_admin)):
    if not payload.record_ids:
        raise HTTPException(status_code=400, detail="record_ids required")
    parents = set()
    async for d in db.ke_records.find({"id": {"$in": payload.record_ids}}, {"upload_id": 1}):
        if d.get("upload_id"):
            parents.add(d["upload_id"])
    result = await db.ke_records.update_many(
        {"id": {"$in": payload.record_ids}},
        {"$set": {"status": "rejected"}},
    )
    for uid in parents:
        await _recompute_upload_status(uid)
    return {"rejected": result.modified_count}


@api_router.post("/admin/studio/uploads/{upload_id}/publish")
async def studio_publish_all(upload_id: str, user: dict = Depends(require_admin)):
    """Convenience: approve every remaining draft record from this upload."""
    now = datetime.now(timezone.utc).isoformat()
    # Only drafts get flipped — never re-touch already-published records.
    result = await db.ke_records.update_many(
        {"upload_id": upload_id, "status": "draft"},
        {"$set": {"status": "published", "published_at": now}},
    )
    await _recompute_upload_status(upload_id)
    await _refresh_studio_index()
    import asyncio
    asyncio.create_task(_visual_dna_backfill())
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


@api_router.delete("/admin/studio/uploads/{upload_id}")
async def studio_delete_upload(upload_id: str, user: dict = Depends(require_admin)):
    """Hard-delete an upload and all its records. Never applied to Reference
    seed catalogues (demo_seed=True) — those are protected."""
    upload = await db.ke_uploads.find_one({"id": upload_id})
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    if upload.get("demo_seed"):
        raise HTTPException(
            status_code=400,
            detail="Reference seed catalogues cannot be deleted from the Studio.",
        )
    rec_res = await db.ke_records.delete_many({"upload_id": upload_id})
    await db.ke_uploads.delete_one({"id": upload_id})
    # Best-effort: remove the on-disk PDF blob if we persisted it.
    try:
        blob = os.path.join(STUDIO_UPLOAD_DIR, f"{upload_id}.pdf")
        if os.path.exists(blob):
            os.remove(blob)
    except Exception:
        logger.exception("failed to remove upload blob")
    await _refresh_studio_index()
    return {"deleted_upload": upload_id, "deleted_records": rec_res.deleted_count}


@api_router.post("/admin/studio/uploads/{upload_id}/reprocess")
async def studio_reprocess_upload(upload_id: str, user: dict = Depends(require_admin)):
    """Re-run extraction on the stored PDF blob for this upload.
    Wipes any existing draft records for the upload and regenerates them
    with the latest extractor. Records that were already published stay
    untouched (so a re-process never demotes live catalogue data)."""
    upload = await db.ke_uploads.find_one({"id": upload_id})
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    if upload.get("demo_seed"):
        raise HTTPException(status_code=400, detail="Reference seed catalogues cannot be reprocessed.")
    blob_path = os.path.join(STUDIO_UPLOAD_DIR, f"{upload_id}.pdf")
    if not os.path.exists(blob_path):
        raise HTTPException(
            status_code=400,
            detail="Original PDF is no longer stored on this server. Please re-upload the catalogue.",
        )
    with open(blob_path, "rb") as fh:
        data = fh.read()
    # Wipe draft/rejected/archived records — keep only published ones so
    # already-live catalogue data remains stable across reprocess.
    await db.ke_records.delete_many({
        "upload_id": upload_id,
        "status": {"$in": ["draft", "rejected", "archived"]},
    })
    await db.ke_uploads.update_one(
        {"id": upload_id},
        {"$set": {"status": "processing", "failure_reason": None,
                   "reprocessed_at": datetime.now(timezone.utc).isoformat()}},
    )
    # Run extraction OUT-OF-BAND. Same background pattern as upload —
    # returns instantly, admin polls the Processing Queue.
    asyncio.create_task(_run_studio_extraction(upload_id, data, upload.get("filename", "")))
    return {
        "upload_id": upload_id,
        "status": "processing",
        "message": "Reprocess started. Refresh the Processing Queue in a moment to see the new records.",
    }


@api_router.post("/admin/studio/uploads/{upload_id}/replace")
async def studio_replace_upload(
    upload_id: str,
    file: UploadFile = File(...),
    user: dict = Depends(require_admin),
):
    """Replace a catalogue's PDF with a new file, wipe all existing
    records for it, and re-run extraction. Used when a supplier ships a
    corrected edition of the same catalogue."""
    upload = await db.ke_uploads.find_one({"id": upload_id})
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    if upload.get("demo_seed"):
        raise HTTPException(status_code=400, detail="Reference seed catalogues cannot be replaced.")
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")
    data = await file.read()
    if len(data) > STUDIO_MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"PDF too large (max {STUDIO_MAX_UPLOAD_BYTES // (1024 * 1024)} MB)",
        )
    try:
        os.makedirs(STUDIO_UPLOAD_DIR, exist_ok=True)
        with open(os.path.join(STUDIO_UPLOAD_DIR, f"{upload_id}.pdf"), "wb") as fh:
            fh.write(data)
    except Exception:
        logger.exception("failed to persist replacement blob")
    await db.ke_records.delete_many({"upload_id": upload_id})
    await db.ke_uploads.update_one({"id": upload_id}, {"$set": {
        "filename": file.filename,
        "size_bytes": len(data),
        "status": "processing",
        "failure_reason": None,
        "replaced_at": datetime.now(timezone.utc).isoformat(),
        "has_blob": True,
    }})
    # Wipe every record for this upload, then re-extract in the background.
    await db.ke_records.delete_many({"upload_id": upload_id})
    asyncio.create_task(_run_studio_extraction(upload_id, data, file.filename))
    await _refresh_studio_index()
    return {
        "upload_id": upload_id,
        "filename": file.filename,
        "status": "processing",
        "message": "Replace accepted. Extraction is running in the background.",
    }


@api_router.get("/admin/studio/uploads/{upload_id}/page/{page_number}")
async def studio_upload_page_preview(
    upload_id: str,
    page_number: int,
    user: dict = Depends(require_admin),
):
    """Return a JPEG thumbnail of a single page from the stored PDF —
    used by the Review Queue 'Preview page' modal."""
    upload = await db.ke_uploads.find_one({"id": upload_id})
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    blob_path = os.path.join(STUDIO_UPLOAD_DIR, f"{upload_id}.pdf")
    if not os.path.exists(blob_path):
        raise HTTPException(status_code=404, detail="Original PDF is no longer stored on this server.")
    try:
        d = fitz.open(blob_path)
        if page_number < 1 or page_number > d.page_count:
            raise HTTPException(status_code=404, detail="Page out of range")
        p = d[page_number - 1]
        pix = p.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
        b64 = base64.b64encode(pix.tobytes("jpeg")).decode()
        d.close()
        return {"upload_id": upload_id, "page_number": page_number, "image_b64": b64}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preview failed: {e}")


@api_router.post("/admin/studio/uploads/{upload_id}/archive")
async def studio_archive_upload(upload_id: str, user: dict = Depends(require_admin)):
    """Soft-archive a published upload — its records get status='archived' so
    they no longer surface in matching or the Published Library, but the
    upload row is retained for audit."""
    upload = await db.ke_uploads.find_one({"id": upload_id})
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    if upload.get("demo_seed"):
        raise HTTPException(
            status_code=400,
            detail="Reference seed catalogues cannot be archived. Delete a user-uploaded catalogue instead.",
        )
    now = datetime.now(timezone.utc).isoformat()
    rec_res = await db.ke_records.update_many(
        {"upload_id": upload_id, "status": {"$ne": "archived"}},
        {"$set": {"status": "archived", "archived_at": now}},
    )
    await db.ke_uploads.update_one({"id": upload_id}, {"$set": {"status": "archived", "archived_at": now}})
    await _refresh_studio_index()
    return {"archived_upload": upload_id, "archived_records": rec_res.modified_count}


@api_router.post("/admin/studio/uploads/{upload_id}/restore")
async def studio_restore_upload(upload_id: str, user: dict = Depends(require_admin)):
    """Undo an archive — bring the upload back into publishing."""
    upload = await db.ke_uploads.find_one({"id": upload_id})
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    rec_res = await db.ke_records.update_many(
        {"upload_id": upload_id, "status": "archived"},
        {"$set": {"status": "published", "archived_at": None}},
    )
    await db.ke_uploads.update_one({"id": upload_id}, {"$set": {"status": "published", "archived_at": None}})
    await _refresh_studio_index()
    return {"restored_upload": upload_id, "restored_records": rec_res.modified_count}


@api_router.post("/admin/studio/cleanup")
async def studio_cleanup(user: dict = Depends(require_admin)):
    """One-click cleanup — run the dev-test filename purge on demand from the
    admin UI. Never touches Reference seeds or valid supplier uploads."""
    before = await db.ke_uploads.count_documents({"demo_seed": {"$ne": True}})
    await _purge_dev_test_uploads()
    # Also delete any upload that's been stuck in `processing` for more than
    # 15 minutes — an orphaned upload from an OCR crash / ingress timeout.
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    stuck = await db.ke_uploads.find({
        "status": "processing", "created_at": {"$lt": cutoff},
        "demo_seed": {"$ne": True},
    }).to_list(200)
    stuck_ids = [u["id"] for u in stuck]
    if stuck_ids:
        await db.ke_records.delete_many({"upload_id": {"$in": stuck_ids}})
        await db.ke_uploads.delete_many({"id": {"$in": stuck_ids}})
    after = await db.ke_uploads.count_documents({"demo_seed": {"$ne": True}})
    return {"removed": before - after, "stuck_processing_removed": len(stuck_ids)}




app.include_router(api_router)

# (CORS middleware was registered earlier — before any routes — so OPTIONS
# preflights are answered without hitting a handler.)
