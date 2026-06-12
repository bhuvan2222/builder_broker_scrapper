import os
import asyncio
import tempfile
from pathlib import Path
from typing import Annotated, Optional
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from dotenv import load_dotenv

load_dotenv()

from services.pdf_extractor import extract_text_from_pdf, pdf_to_images_base64
from services.grok_service import analyze_project_pdf, unify_broker_results
from services.apify_service import run_all_scrapers
from services.enrichment_service import enrich_brokers
from services.export_service import generate_csv, send_csv_email

app = FastAPI(title="Builder Broker Intelligence")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

_html: str = ""


@app.on_event("startup")
async def _load_html():
    global _html
    # Path.read_text is a bound method — no open() call in async scope
    _html = await asyncio.to_thread(Path("static/index.html").read_text)


@app.get("/", response_class=HTMLResponse)
async def root():
    return _html


@app.post(
    "/api/analyze",
    responses={
        400: {"description": "Invalid file type or file too large"},
        422: {"description": "PDF has no extractable content (text or images)"},
        500: {"description": "Internal processing error"},
    },
)
async def analyze_brochure(file: Annotated[UploadFile, File()]):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    content = await file.read()
    if len(content) > 301024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 25 MB).")

    def _extract_all() -> tuple[str, list]:
        """Returns (text, images_base64). Images only populated for image-based PDFs."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(content)
            path = tmp.name
        try:
            text = extract_text_from_pdf(path)
            images = pdf_to_images_base64(path) if len(text.strip()) < 200 else []
            return text, images
        finally:
            os.unlink(path)

    try:
        pdf_text, pdf_images = await asyncio.get_event_loop().run_in_executor(None, _extract_all)

        if not pdf_text.strip() and not pdf_images:
            raise HTTPException(
                status_code=422,
                detail="Could not extract any content from this PDF. It may be password-protected or corrupted.",
            )

        mock_mode = os.getenv("SCRAPER_ENABLED", "true").lower() != "true"

        analysis = await analyze_project_pdf(pdf_text, pdf_images)
        scraper_results = await run_all_scrapers(analysis)

        all_raw = []
        for src in ["google_maps", "99acres", "magicbricks", "justdial"]:
            src_data = scraper_results[src].get("data", [])
            print(f"[SCRAPER] {src}: {len(src_data)} records, error={scraper_results[src].get('error')}")
            all_raw.extend(src_data)

        if mock_mode:
            # Mock data is already clean — skip enrichment and AI unification entirely
            print(f"[MOCK] Skipping enrichment + AI unification. Serving {len(all_raw)} records directly.")
            unified_brokers = all_raw
        else:
            import json as _json
            print(f"[SCRAPER] total raw records before AI: {len(all_raw)}")
            print(_json.dumps(all_raw[:3], indent=2, ensure_ascii=False))

            print("[ENRICH] Starting website enrichment…")
            all_raw = await enrich_brokers(all_raw)
            enriched_count = sum(1 for b in all_raw if b.get("key_person"))
            print(f"[ENRICH] Done — {enriched_count}/{len(all_raw)} brokers enriched with key person")

            unified_brokers = await unify_broker_results(all_raw, analysis["project_details"])

        return {
            "success": True,
            "project_details": analysis["project_details"],
            "search_queries": analysis["search_queries"],
            "unified_brokers": unified_brokers,
            "results": scraper_results,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CsvEmailRequest(BaseModel):
    email: Optional[EmailStr] = None
    project_name: str
    unified_brokers: list = []
    results: dict = {}


@app.post(
    "/api/download-csv",
    responses={400: {"description": "No results to export"}},
)
async def download_csv(body: CsvEmailRequest):
    csv_content = generate_csv(body.unified_brokers or [], body.results)
    if not csv_content.strip():
        raise HTTPException(status_code=400, detail="No results to export.")
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in body.project_name)
    filename = f"brokers_{safe}.csv"
    return StreamingResponse(
        iter([csv_content.encode("utf-8-sig")]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post(
    "/api/send-csv",
    responses={
        400: {"description": "No results to export"},
        500: {"description": "Email sending failed"},
    },
)
async def send_csv(body: CsvEmailRequest):
    if not body.email:
        raise HTTPException(status_code=400, detail="Email address is required.")
    csv_content = generate_csv(body.unified_brokers or [], body.results)
    if not csv_content.strip():
        raise HTTPException(status_code=400, detail="No results to export.")
    try:
        await send_csv_email(body.email, csv_content, body.project_name)
        return {"success": True, "message": f"Report sent to {body.email}"}
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Email failed: {e}")


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "groq_configured": bool(os.getenv("GROQ_API_KEY")),
        "apify_configured": bool(os.getenv("APIFY_API_TOKEN")),
        "email_configured": bool(os.getenv("SMTP_USER") and os.getenv("SMTP_PASS")),
    }
