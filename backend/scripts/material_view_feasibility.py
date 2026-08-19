"""Feasibility test — programmatic 'Material View' generation from a
flat catalogue swatch.

Runs each of 3 real swatches through TWO generators and reports:
  * GPT-Image-1  (text-to-image, prompt grounded in swatch DNA)
  * Gemini Nano Banana (image-to-image, swatch as reference)

Saves outputs to /tmp/matview_out/ so we can eyeball quality, and
prints per-call latency + envelope so we can back-of-envelope the cost.

Not a pipeline integration — one-off feasibility.
"""
from __future__ import annotations
import asyncio, base64, os, time
from pathlib import Path
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(Path("/app/backend/.env"))
API_KEY = os.environ["EMERGENT_LLM_KEY"]

# Real swatch IDs from the DB (picked by material_view_feasibility.py sibling
# probe — wood laminate, marble stone, satin paint).
SWATCH_IDS = [
    ("wood",  "f2cb7040-f8cf-4114-ae43-2ddc0b1db65f"),
    ("stone", "c16fd365-7b22-4787-a528-994ff19c37f9"),
    ("paint", "65b9a1da-813a-446c-a592-04e9dd01332d"),
]

OUT = Path("/tmp/matview_out"); OUT.mkdir(parents=True, exist_ok=True)


def build_prompt(rec: dict) -> str:
    """DNA-grounded text prompt for text-to-image generation."""
    name = rec.get("material_name") or rec.get("color_name") or "material"
    family = (rec.get("material_family") or "").strip()
    finish = (rec.get("finish") or "").strip()
    texture = (rec.get("texture") or "").strip()
    pattern = (rec.get("pattern") or "").strip()
    color_name = (rec.get("color_name") or "").strip()
    color_hex = (rec.get("color_hex") or "").strip()

    parts = [
        f"Photorealistic architectural surface swatch of a real {family or 'material'} "
        f"named '{name}'.",
        f"Dominant colour: {color_name} (hex {color_hex}).",
        f"Finish: {finish}. Texture: {texture}. Pattern: {pattern}.",
        "Rendered as a large flat sample panel photographed under soft, even "
        "studio lighting, with correct micro-texture and material reflectivity "
        "as it would appear physically installed on a wall or surface. "
        "No labels, no watermark, no shadows of foreign objects, no props. "
        "Neutral background, close-up material view, catalogue-realistic.",
    ]
    return " ".join(parts)


async def run_gpt_image(rec: dict, label: str) -> dict:
    """Text-to-image with GPT-Image-1 using a DNA-grounded prompt."""
    from emergentintegrations.llm.openai.image_generation import OpenAIImageGeneration
    prompt = build_prompt(rec)
    gen = OpenAIImageGeneration(api_key=API_KEY)
    t0 = time.perf_counter()
    try:
        imgs = await gen.generate_images(
            prompt=prompt, model="gpt-image-1",
            number_of_images=1, quality="medium",
        )
    except Exception as e:
        return {"ok": False, "err": str(e)[:400], "dt_s": round(time.perf_counter()-t0, 2)}
    dt = time.perf_counter() - t0
    out = OUT / f"{label}_gpt_image_1.png"
    out.write_bytes(imgs[0])
    return {"ok": True, "dt_s": round(dt, 2), "bytes": len(imgs[0]), "path": str(out),
            "prompt_used": prompt[:200] + "…"}


async def run_nano_banana(rec: dict, label: str, swatch_b64: str) -> dict:
    """Image-to-image with Gemini Nano Banana — swatch as reference."""
    from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
    prompt = (
        "Using the attached flat catalogue swatch as the SOURCE material, "
        "produce a photorealistic 'material view' — a close-up render of "
        f"this exact {rec.get('material_family') or 'material'} "
        f"({rec.get('material_name') or rec.get('color_name')}) as a physical "
        "surface panel under soft, even architectural studio lighting. Preserve "
        "the swatch's colour, grain direction, pattern and finish exactly. "
        "Show correct micro-texture and material reflectivity as it would look "
        "installed. Neutral background. No labels, no props, no text."
    )
    chat = LlmChat(api_key=API_KEY,
                   session_id=f"matview-{label}",
                   system_message="You are a material visualization assistant.")
    chat.with_model("gemini", "gemini-3.1-flash-image-preview") \
        .with_params(modalities=["image", "text"])
    msg = UserMessage(text=prompt, file_contents=[ImageContent(swatch_b64)])
    t0 = time.perf_counter()
    try:
        text, images = await chat.send_message_multimodal_response(msg)
    except Exception as e:
        return {"ok": False, "err": str(e)[:400], "dt_s": round(time.perf_counter()-t0, 2)}
    dt = time.perf_counter() - t0
    if not images:
        return {"ok": False, "err": f"no image returned; text: {(text or '')[:200]}",
                "dt_s": round(dt, 2)}
    b = base64.b64decode(images[0]["data"])
    out = OUT / f"{label}_nano_banana.png"
    out.write_bytes(b)
    return {"ok": True, "dt_s": round(dt, 2), "bytes": len(b), "path": str(out),
            "prompt_used": prompt[:200] + "…"}


async def main() -> None:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    report: list[dict] = []
    for label, sid in SWATCH_IDS:
        rec = await db.ke_records.find_one({"id": sid})
        if not rec:
            print(f"[{label}] not found: {sid}"); continue

        swatch_b64 = rec.get("page_preview_b64") or ""
        # Save the source swatch too so we can visually compare.
        (OUT / f"{label}_source_swatch.png").write_bytes(base64.b64decode(swatch_b64))

        print(f"\n=== {label.upper()}  {rec.get('material_name')}  hex={rec.get('color_hex')} ===")
        gpt_res = await run_gpt_image(rec, label)
        print(f"  gpt-image-1     → {gpt_res}")
        nb_res = await run_nano_banana(rec, label, swatch_b64)
        print(f"  nano-banana     → {nb_res}")

        report.append({"label": label, "material": rec.get("material_name"),
                       "gpt_image_1": gpt_res, "nano_banana": nb_res})

    client.close()
    import json
    (OUT / "report.json").write_text(json.dumps(report, indent=2))
    print(f"\nAll outputs in {OUT}/")
    print(f"Report: {OUT / 'report.json'}")


if __name__ == "__main__":
    asyncio.run(main())
