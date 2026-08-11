"""
main.py
-------
Punto de entrada de AuditFlow AI. Expone:

  GET  /                 -> formulario principal
  POST /api/audit        -> ejecuta la auditoría completa (scraping + IA)
                             y devuelve JSON con los resultados
  POST /api/audit/pdf    -> re-ejecuta la auditoría y devuelve el PDF
                             descargable con marca blanca

La auditoría se re-ejecuta en /api/audit/pdf en lugar de cachear
resultados en memoria para mantener el ejemplo simple y stateless
(apto para despliegue serverless/multi-instancia en Render/Vercel).
Para producción de alto tráfico, se recomienda cachear el resultado
de /api/audit (p. ej. Redis) y que /api/audit/pdf lo reutilice por ID.
"""

from __future__ import annotations

import io
import os
from dataclasses import asdict

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator

from .scraper import scrape_url, build_issues, summarize_by_pillar, ScraperError
from .ai_analyzer import run_ai_analysis
from .pdf_generator import generate_pdf_report

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(
    title="AuditFlow AI",
    description="Plataforma SaaS de auditoría web automatizada con IA.",
    version="1.0.0",
)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

BRAND_NAME = os.getenv("BRAND_NAME", "AuditFlow AI")
BRAND_TAGLINE = os.getenv(
    "BRAND_TAGLINE", "Auditoría web automatizada con Inteligencia Artificial"
)


class AuditRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def url_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("La URL no puede estar vacía.")
        return v.strip()


async def _run_full_audit(url: str) -> dict:
    """Orquesta scraping -> análisis heurístico -> análisis IA."""
    try:
        raw_data = await scrape_url(url)
    except ScraperError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # cualquier fallo no anticipado
        raise HTTPException(
            status_code=500, detail=f"Error inesperado durante el análisis: {exc}"
        ) from exc

    issues = build_issues(raw_data)
    pillar_summary = summarize_by_pillar(issues)

    try:
        ai_analysis = run_ai_analysis(raw_data, issues)
    except Exception as exc:
        # La IA nunca debería tumbar la auditoría completa
        ai_analysis = {
            "resumen_ejecutivo": "No se pudo generar el análisis de IA.",
            "problemas_priorizados": [],
            "modo_degradado": True,
            "error_ia": str(exc),
        }

    overall_score = round(
        sum(v["score"] for v in pillar_summary.values()) / max(len(pillar_summary), 1)
    )

    return {
        "raw_data": raw_data,
        "issues": issues,
        "pillar_summary": pillar_summary,
        "ai_analysis": ai_analysis,
        "overall_score": overall_score,
    }


def _serialize_pillar_summary(pillar_summary: dict) -> dict:
    serialized = {}
    for pillar, info in pillar_summary.items():
        serialized[pillar] = {
            "score": info["score"],
            "total_issues": info["total_issues"],
            "issues": [asdict(i) for i in info["issues"]],
        }
    return serialized


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "brand_name": BRAND_NAME, "brand_tagline": BRAND_TAGLINE},
    )


@app.post("/api/audit")
async def api_audit(payload: AuditRequest):
    result = await _run_full_audit(payload.url)
    raw_data = result["raw_data"]

    response_body = {
        "url": raw_data.final_url,
        "titulo": raw_data.title,
        "codigo_http": raw_data.status_code,
        "puntaje_global": result["overall_score"],
        "pilares": _serialize_pillar_summary(result["pillar_summary"]),
        "analisis_ia": result["ai_analysis"],
        "metricas_rendimiento": {
            "ttfb_ms": raw_data.time_to_first_byte_ms,
            "dom_content_loaded_ms": raw_data.dom_content_loaded_ms,
            "load_event_ms": raw_data.load_event_ms,
            "lcp_aproximado_ms": raw_data.largest_content_paint_approx_ms,
            "peso_pagina_kb": round(raw_data.page_weight_bytes / 1024, 1),
        },
    }
    return JSONResponse(content=response_body)


@app.post("/api/audit/pdf")
async def api_audit_pdf(payload: AuditRequest):
    result = await _run_full_audit(payload.url)
    raw_data = result["raw_data"]

    pdf_bytes = generate_pdf_report(
        data=raw_data,
        pillar_summary=result["pillar_summary"],
        ai_analysis=result["ai_analysis"],
        brand_name=BRAND_NAME,
        brand_tagline=BRAND_TAGLINE,
    )

    safe_host = "".join(c for c in raw_data.final_url if c.isalnum())[:40] or "reporte"
    filename = f"auditoria_{safe_host}.pdf"

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": BRAND_NAME}
