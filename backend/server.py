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

app = FastAPI(title="MaterialMatch AI")
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("materialmatch")


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
        return user
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


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
            "preferred_region": DEFAULT_REGION}


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
        return {"ok": True}
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
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": "mock-v2",
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
    "You are an expert interior design materials analyst. You inspect interior "
    "reference images and identify materials, finishes, colors, and textures with "
    "precision suitable for architects and interior designers. Be conservative — "
    "only report zones whose material you can clearly identify from the image. "
    "Always respond with ONLY a valid JSON object. No markdown fences, no prose, "
    "no commentary outside the JSON."
)

ANALYSIS_USER_PROMPT = (
    "Analyse this interior reference image. For each clearly identifiable material "
    "zone, return one entry. Do NOT pad the list — quality over quantity. If a zone "
    "is ambiguous or occluded, omit it.\n\n"
    "Return ONLY this JSON shape:\n"
    "{\n"
    '  "rows": [\n'
    "    {\n"
    '      "zone": "string — e.g. Floor, Walls, Ceiling, Sofa, Coffee Table",\n'
    '      "material_family": "one of: ' + ", ".join(MATERIAL_FAMILIES) + '",\n'
    '      "material_type": "concise product-category, e.g. Engineered Oak",\n'
    '      "color": "short descriptive colour name",\n'
    '      "texture": "short texture descriptor",\n'
    '      "finish": "short finish descriptor",\n'
    '      "design_style": "short style label, e.g. Scandinavian",\n'
    '      "keywords": ["3-6 short lowercase tags"],\n'
    '      "confidence": 0\n'
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Rules:\n"
    "- confidence is an INTEGER between 0 and 100, NOT a float between 0 and 1.\n"
    "- material_family MUST be one of the listed enum values.\n"
    "- Return 1 to 12 rows. Prefer fewer high-confidence rows over many guesses.\n"
    "- Reply with ONLY the JSON object."
)

INDIA_ANALYSIS_BLOCK = (
    "\n\n" + INDIAN_BRAND_CONTEXT + "\n\n"
    "BECAUSE the user prefers India sourcing, you MAY add ONE optional extra field "
    "per row:\n"
    '  "indian_alternative": "short ≤ 120 char hint, e.g. \\"Kota stone honed — '
    'similar to Travertine; widely available via Indian regional suppliers\\""\n'
    "Include this field only when an Indian-market alternative is genuinely "
    "useful AND your confidence is ≥ 70. Otherwise set it to null or omit it. "
    "Use Indian interior-design terminology (e.g. PU matte, MDF with laminate, "
    "vitrified tile, teak veneer, Kota stone) where it reads naturally in the "
    "main fields too — but never invent brand names without justification."
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


def _validate_analysis_payload(data) -> list:
    """Strictly validate the LLM payload. Raises ValueError on any deviation. Returns clean rows."""
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
            "indian_alternative": _clean_optional_text(r.get("indian_alternative"), max_len=120),
        })
    return cleaned


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
                "rows": cleaned,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "version": f"real-openai-{LLM_MODEL_ANALYSIS}-v1",
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
    return analysis




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
    "BECAUSE the user prefers India sourcing, you MAY add ONE optional extra field "
    "per candidate:\n"
    '  "indian_alternative": "short ≤ 120 char hint, e.g. \\"Comparable to '
    'Greenlam veneer in matt PU finish — widely stocked across Indian dealers.\\""\n'
    "Include it only when an India-market parallel is genuinely useful AND the "
    "candidate is a real product_material_candidate AND match_percent ≥ 55. "
    "Otherwise set the field to null or omit it. Use Indian interior-design "
    "terminology where natural — never invent brand-SKU pairs."
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


def _validate_batch_result(data, expected_n: int) -> list:
    """Validate a batched LLM response per-item. Returns a list of length expected_n.
    Each slot is a clean entry; slots the LLM mangled are filled with a zero-score
    fallback so the rest of the batch is not lost. Raises ValueError ONLY when
    the structural envelope is unusable or every single item fails."""
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
            if not isinstance(it, dict):
                raise ValueError("entry not object")
            idx = it.get("candidate_index")
            if not isinstance(idx, int) or not (0 <= idx < expected_n):
                # Tolerate index drift: if it looks like a positional miss, fall back to raw_pos
                if 0 <= raw_pos < expected_n and raw_pos not in seen_idx:
                    idx = raw_pos
                else:
                    raise ValueError(f"bad candidate_index {idx}")
            if idx in seen_idx:
                raise ValueError(f"duplicate candidate_index {idx}")
            cand_type = it.get("candidate_type")
            if cand_type not in CANDIDATE_TYPES:
                raise ValueError(f"candidate_type {cand_type!r} not in enum")
            det_fam = it.get("detected_family")
            if det_fam not in MATERIAL_FAMILIES:
                # Soft-coerce unknown families to "other" (a valid enum value)
                det_fam = "other" if "other" in MATERIAL_FAMILIES else None
                if det_fam is None:
                    raise ValueError("detected_family missing")
            pct = it.get("match_percent")
            if isinstance(pct, bool):
                raise ValueError("match_percent is bool")
            try:
                pct_int = int(round(float(pct)))
            except (TypeError, ValueError):
                raise ValueError("match_percent not numeric")
            pct_int = max(0, min(100, pct_int))

            reasons_raw = it.get("reasons") or []
            if not isinstance(reasons_raw, list):
                reasons_raw = []
            reasons: list = []
            for r in reasons_raw[:3]:  # cap at 3, ignore extras
                if not isinstance(r, dict):
                    continue
                cat = r.get("category")
                txt = r.get("text")
                if cat not in REASON_CATEGORIES:
                    continue
                if not isinstance(txt, str) or not txt.strip():
                    continue
                reasons.append({"category": cat, "text": txt.strip()[:120]})
            # Require at least 1 reason for genuine product candidates (so the UI is informative),
            # but allow 0 reasons for room-scene / unclear entries (they will be filtered out anyway).
            if cand_type == "product_material_candidate" and not reasons:
                raise ValueError("product candidate has no usable reasons")
            disq = it.get("disqualifier")
            if disq is not None and (not isinstance(disq, str) or not disq.strip()):
                disq = None
            if isinstance(disq, str):
                disq = disq.strip()[:160]

            seen_idx.add(idx)
            out[idx] = {
                "candidate_index": idx,
                "candidate_type": cand_type,
                "detected_family": det_fam,
                "match_percent": pct_int,
                "reasons": reasons,
                "disqualifier": disq,
                "indian_alternative": _clean_optional_text(it.get("indian_alternative"), max_len=120),
            }
        except ValueError as ve:
            item_errors.append(f"item[{raw_pos}]={ve}")
            continue

    if all(x is None for x in out):
        raise ValueError(f"no valid entries in batch_results ({len(items)} raw items, errors: {'; '.join(item_errors[:3])})")

    # Fill mangled slots with a zero-score fallback so the caller can still emit them
    for i in range(expected_n):
        if out[i] is None:
            logger.info(f"match validator-fallback idx={i} (item error)")
            out[i] = {
                "candidate_index": i,
                "candidate_type": "unclear",
                "detected_family": "other",
                "match_percent": 0,
                "reasons": [],
                "disqualifier": "Could not parse this candidate's score",
                "indian_alternative": None,
            }
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


async def _run_real_match(
    project_id: str,
    user_id: str,
    selected: dict,
    manual_prompt: str,
    catalogue_files: list,
    existing_match: dict,
    region: str = DEFAULT_REGION,
) -> dict:
    """Phase-1 real catalogue match: uploaded product images only, batched scoring."""
    import asyncio
    warnings: list = []

    # 1. Filter, size-cap, normalize candidates
    raw_candidates = []
    for f in catalogue_files:
        content = await f.read()
        if not content:
            continue
        mime = f.content_type or ""
        if mime not in ("image/jpeg", "image/png", "image/webp"):
            warnings.append(f"Skipped non-image file: {f.filename}")
            continue
        if len(content) > MATCH_MAX_FILE_BYTES:
            warnings.append(f"Skipped oversized file (>5 MiB): {f.filename}")
            continue
        b64 = _normalize_image_to_b64(content)
        if not b64:
            warnings.append(f"Could not decode: {f.filename}")
            continue
        raw_candidates.append({
            "name": f.filename or "untitled",
            "size": len(content),
            "b64": b64,
        })
    if len(raw_candidates) == 0:
        raise HTTPException(status_code=400, detail="No valid product images provided. Upload JPEG/PNG/WEBP files under 5 MiB.")
    if len(raw_candidates) > MATCH_MAX_PRODUCT_IMAGES:
        raise HTTPException(status_code=422, detail=f"Too many images. Maximum {MATCH_MAX_PRODUCT_IMAGES} per match.")

    # 2. Dedup check — same uploads + same zone within window
    cand_hash = _candidate_hash([{"name": c["name"], "size": c["size"]} for c in raw_candidates])
    if existing_match and existing_match.get("candidate_hash") == cand_hash and existing_match.get("version", "").startswith("real-"):
        try:
            gen_at = datetime.fromisoformat(existing_match["generated_at"].replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - gen_at).total_seconds() < MATCH_DEDUP_WINDOW_S:
                logger.info(f"match dedup-hit project={project_id}")
                return existing_match
        except Exception:
            pass

    # 3. Build batches + concurrent dispatch
    selected_spec_json = json.dumps({k: selected.get(k) for k in
                                     ("zone", "material_family", "material_type", "color",
                                      "texture", "finish", "design_style", "keywords")}, ensure_ascii=False)
    batches = [raw_candidates[i:i + MATCH_BATCH_SIZE] for i in range(0, len(raw_candidates), MATCH_BATCH_SIZE)]
    sem = asyncio.Semaphore(LLM_MATCH_CONCURRENCY)

    async def _do_batch(bi, batch):
        async with sem:
            return await _score_one_batch(project_id, bi, selected_spec_json, manual_prompt,
                                          [c["b64"] for c in batch], region=region)

    batch_results = await asyncio.gather(*[_do_batch(bi, b) for bi, b in enumerate(batches)])

    # 4. Aggregate global candidates with calibration + family gating + candidate-type filter
    selected_family = (selected.get("material_family") or "").lower()
    scored = []
    skipped_count = 0
    for bi, (batch, results) in enumerate(zip(batches, batch_results)):
        for r in results:
            cand = batch[r["candidate_index"]]
            ctype = r.get("candidate_type", "unclear")

            # Room scenes are dropped outright with a per-filename warning
            if ctype == "room_scene_or_lifestyle":
                skipped_count += 1
                warnings.append(
                    f"Skipped {cand['name']}: image appears to be a room/lifestyle scene, "
                    "not a product/material candidate."
                )
                logger.info(
                    f"match skipped project={project_id} cand={cand['name']} reason=room_scene"
                )
                continue

            pct = _calibrate_percent(r["match_percent"])

            # Family gating
            gated_pct, gate_note = _enforce_family_gating(
                selected_family, r.get("detected_family"), pct, r.get("reasons", []), r.get("disqualifier"),
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
                continue
            scored.append({
                "name": cand["name"],
                "size": cand["size"],
                "candidate_type": ctype,
                "detected_family": r.get("detected_family"),
                "match_percent": pct,
                "reasons": r["reasons"],
                "disqualifier": r["disqualifier"],
                "indian_alternative": r.get("indian_alternative"),
            })
    if not scored:
        warnings.append(
            f"No products met the minimum {MATCH_MIN_THRESHOLD}% similarity bar."
        )
    elif len(scored) < 3:
        warnings.append(
            "Only limited relevant matches found. Upload more products from the "
            "same material category for better results."
        )

    scored.sort(key=lambda x: (-x["match_percent"], x["name"]))
    top = scored[:5]

    # 5. Shape result
    matches = []
    for i, s in enumerate(top):
        product_name = os.path.splitext(s["name"])[0].replace("_", " ").replace("-", " ").strip().title() or s["name"]
        matches.append({
            "id": f"match_{i + 1}",
            "product_name": product_name,
            "catalogue_ref": s["name"],
            "match_percent": s["match_percent"],
            "score_label": _score_label(s["match_percent"]),
            "reasons": _format_reasons_for_storage(s["reasons"]),
            "disqualifier": s["disqualifier"],
            "indian_alternative": s.get("indian_alternative"),
            "thumbnail_color": "#" + hashlib.sha256(s["name"].encode()).hexdigest()[:6],
        })

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


@app.on_event("shutdown")
async def shutdown_event():
    client.close()


# Mount the router
app.include_router(api_router)

# CORS - allow credentials with explicit origin
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
