from dotenv import load_dotenv
load_dotenv()

import os
import io
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
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    res = await db.users.insert_one(doc)
    uid = str(res.inserted_id)
    access = create_access_token(uid, email)
    refresh = create_refresh_token(uid)
    set_auth_cookies(response, access, refresh)
    return {"id": uid, "email": email, "name": payload.name, "role": "user"}


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
    }


@api_router.post("/auth/logout")
async def logout(response: Response, user: dict = Depends(get_current_user)):
    clear_auth_cookies(response)
    return {"ok": True}


@api_router.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return user


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
# AI Analysis - Claude Sonnet 4.5 Vision
# ============================================================================
ANALYSIS_SYSTEM_PROMPT = """You are an expert interior design materials analyst. You analyze interior reference images and identify materials, finishes, colors, and textures with precision suitable for architects and interior designers.

Always respond with ONLY valid JSON matching the requested schema. Do not include markdown code fences, prose, or any text outside the JSON object."""


async def run_analysis(project_id: str, user_id: str, custom_prompt: str = ""):
    """Run the LLM analysis: detect materials in ref image, then match each catalogue item."""
    doc = await db.projects.find_one({"_id": ObjectId(project_id), "user_id": user_id})
    if not doc:
        return
    ref_b64 = doc.get("reference_image_b64")
    if not ref_b64:
        await db.projects.update_one(
            {"_id": ObjectId(project_id)},
            {"$set": {"status": "error", "analysis_error": "No reference image"}})
        return

    await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$set": {"status": "analyzing"}}
    )

    catalogue = doc.get("catalogue_items", [])

    # === Step 1: Analyze reference image ===
    user_focus = f"\nUser focus / preferences: {custom_prompt}" if custom_prompt else ""
    analyze_prompt = f"""Analyze this interior design reference image. Identify the key materials, finishes, colors, textures, and design style.{user_focus}

Return ONLY this JSON shape:
{{
  "summary": "1-2 sentence overall description of the space and design style",
  "style_tags": ["modern", "minimalist", "scandinavian", ...],
  "color_palette": [{{"name": "Warm Oak", "hex": "#A07856"}}, ...],
  "materials": [
    {{
      "name": "White Oak Flooring",
      "category": "Wood",
      "finish": "Matte natural",
      "location": "Floor",
      "confidence": 0.92
    }}
  ]
}}

Provide 4-8 materials, 4-6 colors, 3-5 style tags. Confidence is 0.0-1.0."""

    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"analyze-{project_id}",
            system_message=ANALYSIS_SYSTEM_PROMPT,
        ).with_model(LLM_PROVIDER, LLM_MODEL)

        ref_img = ImageContent(image_base64=ref_b64)
        msg = UserMessage(text=analyze_prompt, file_contents=[ref_img])
        resp = await chat.send_message(msg)
        ref_analysis = _parse_json(resp)
    except Exception as e:
        logger.exception("Reference analysis failed")
        await db.projects.update_one(
            {"_id": ObjectId(project_id)},
            {"$set": {"status": "error", "analysis_error": str(e)}}
        )
        return

    # === Step 2: Match each catalogue item ===
    matches = []
    for idx, item in enumerate(catalogue):
        try:
            match_chat = LlmChat(
                api_key=EMERGENT_LLM_KEY,
                session_id=f"match-{project_id}-{idx}",
                system_message=ANALYSIS_SYSTEM_PROMPT,
            ).with_model(LLM_PROVIDER, LLM_MODEL)

            match_prompt = f"""You are shown two images. The FIRST is the reference inspiration image. The SECOND is a candidate material/product (named: "{item['name']}").

Given the reference image's identified materials: {json.dumps(ref_analysis.get('materials', []))}

Return ONLY this JSON shape:
{{
  "match_score": 0.0,
  "matched_material": "name of the reference material it best matches, or null",
  "explanation": "1-2 sentence reasoning explaining visual similarity in texture, color, finish, and style",
  "tags": ["wood", "matte", "warm tone"]
}}

match_score is 0.0-1.0. Be honest — score low if there's no real match."""

            ref_img = ImageContent(image_base64=ref_b64)
            cand_img = ImageContent(image_base64=item["image_b64"])
            match_msg = UserMessage(text=match_prompt, file_contents=[ref_img, cand_img])
            match_resp = await match_chat.send_message(match_msg)
            parsed = _parse_json(match_resp)
            matches.append({
                "name": item["name"],
                "index": idx,
                "score": float(parsed.get("match_score", 0)),
                "matched_material": parsed.get("matched_material"),
                "explanation": parsed.get("explanation", ""),
                "tags": parsed.get("tags", []),
            })
        except Exception as e:
            logger.exception(f"Match failed for item {idx}")
            matches.append({
                "name": item["name"],
                "index": idx,
                "score": 0.0,
                "matched_material": None,
                "explanation": f"Analysis error: {str(e)[:100]}",
                "tags": [],
            })

    matches.sort(key=lambda m: m["score"], reverse=True)

    analysis = {
        "summary": ref_analysis.get("summary", ""),
        "style_tags": ref_analysis.get("style_tags", []),
        "color_palette": ref_analysis.get("color_palette", []),
        "materials": ref_analysis.get("materials", []),
        "matches": matches,
        "custom_prompt": custom_prompt,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

    await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$set": {"status": "completed", "analysis": analysis,
                  "updated_at": datetime.now(timezone.utc).isoformat()}}
    )

    # Also save as a report
    await db.reports.insert_one({
        "user_id": user_id,
        "project_id": project_id,
        "project_name": doc.get("name", "Untitled"),
        "client_name": doc.get("client_name", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "summary": analysis["summary"],
        "match_count": len(matches),
        "top_score": matches[0]["score"] if matches else 0,
    })


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
        "material_type": "Engineered Oak Plank",
        "color": "Warm Walnut Brown",
        "texture": "Visible natural grain",
        "finish": "Matte oiled",
        "design_style": "Scandinavian",
        "keywords": ["wood", "warm", "natural", "matte", "plank"],
        "confidence": 0.92,
    },
    {
        "zone": "Walls",
        "material_type": "Lime Plaster",
        "color": "Bone White",
        "texture": "Slightly mottled",
        "finish": "Matte chalky",
        "design_style": "Wabi-sabi",
        "keywords": ["plaster", "minimal", "soft", "chalky"],
        "confidence": 0.87,
    },
    {
        "zone": "Ceiling",
        "material_type": "Painted Drywall",
        "color": "Off-white",
        "texture": "Smooth",
        "finish": "Eggshell",
        "design_style": "Modern Minimalist",
        "keywords": ["ceiling", "smooth", "neutral", "paint"],
        "confidence": 0.81,
    },
    {
        "zone": "Sofa",
        "material_type": "Bouclé Upholstery",
        "color": "Cream Beige",
        "texture": "Looped, fluffy",
        "finish": "Soft matte",
        "design_style": "Contemporary Mid-century",
        "keywords": ["fabric", "bouclé", "cozy", "neutral", "textured"],
        "confidence": 0.89,
    },
    {
        "zone": "Coffee Table",
        "material_type": "Travertine Stone",
        "color": "Sandy Cream",
        "texture": "Open-pore, banded",
        "finish": "Honed",
        "design_style": "Organic Modern",
        "keywords": ["stone", "travertine", "honed", "earthy"],
        "confidence": 0.84,
    },
    {
        "zone": "Lighting",
        "material_type": "Brushed Brass",
        "color": "Warm Gold",
        "texture": "Linear brush marks",
        "finish": "Brushed satin",
        "design_style": "Modern Luxe",
        "keywords": ["metal", "brass", "warm", "accent"],
        "confidence": 0.78,
    },
    {
        "zone": "Rug",
        "material_type": "Hand-tufted Wool",
        "color": "Sand & Ivory",
        "texture": "Loop-pile",
        "finish": "Natural fibre",
        "design_style": "Japandi",
        "keywords": ["rug", "wool", "neutral", "layered"],
        "confidence": 0.86,
    },
    {
        "zone": "Accent Wall",
        "material_type": "Vertical Slatted Oak",
        "color": "Mid-tone Honey",
        "texture": "Linear ribbed",
        "finish": "Lacquered satin",
        "design_style": "Japandi",
        "keywords": ["wood", "slatted", "linear", "warm"],
        "confidence": 0.83,
    },
]


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
    rows = [
        MOCK_MATERIAL_LIBRARY[(start + i) % len(MOCK_MATERIAL_LIBRARY)]
        for i in range(count)
    ]

    mock_analysis = {
        "rows": rows,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": "mock-v1",
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
    seed_int = int(hashlib.md5(f"{project_id}-{zone}".encode()).hexdigest()[:8], 16)
    category = _category_for(selected)

    candidates = [p for p in MOCK_PRODUCT_LIBRARY if p["category"] == category]
    if len(candidates) < 5:
        candidates += [p for p in MOCK_PRODUCT_LIBRARY if p["category"] != category]

    start = seed_int % len(candidates)
    chosen = [candidates[(start + i) % len(candidates)] for i in range(5)]

    base_pcts = [92, 86, 79, 71, 63]
    reason_pool = MATCH_REASONS_LIBRARY.get(category, MATCH_REASONS_LIBRARY["wood"])
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
    }

    await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$set": {
            f"match_results.{zone}": result,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }}
    )
    return result





@api_router.post("/projects/{project_id}/analyze")
async def start_analysis(project_id: str, background: BackgroundTasks,
                         prompt: str = Form(""),
                         user: dict = Depends(get_current_user)):
    doc = await db.projects.find_one({"_id": ObjectId(project_id), "user_id": user["id"]})
    if not doc:
        raise HTTPException(status_code=404, detail="Project not found")
    if not doc.get("reference_image_b64"):
        raise HTTPException(status_code=400, detail="Upload a reference image first")
    if not doc.get("catalogue_items"):
        raise HTTPException(status_code=400, detail="Upload catalogue items first")

    await db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$set": {"status": "queued", "custom_prompt": prompt, "analysis": None}}
    )
    background.add_task(run_analysis, project_id, user["id"], prompt)
    return {"ok": True, "status": "queued"}


@api_router.get("/projects/{project_id}/status")
async def analysis_status(project_id: str, user: dict = Depends(get_current_user)):
    doc = await db.projects.find_one(
        {"_id": ObjectId(project_id), "user_id": user["id"]},
        {"status": 1, "analysis_error": 1}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    return {"status": doc.get("status", "draft"), "error": doc.get("analysis_error")}


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
# Health
# ============================================================================
@api_router.get("/")
async def root():
    return {"app": "MaterialMatch AI", "status": "ok"}


# ============================================================================
# Startup
# ============================================================================
@app.on_event("startup")
async def startup_event():
    try:
        await db.users.create_index("email", unique=True)
        await db.projects.create_index([("user_id", 1), ("created_at", -1)])
        await db.reports.create_index([("user_id", 1), ("created_at", -1)])
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
