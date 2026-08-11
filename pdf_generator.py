"""
pdf_generator.py
-----------------
Genera el reporte PDF final de la auditoría con marca blanca (white-label),
usando ReportLab. Compila: datos generales, score por pilar, listado de
problemas detectados y el análisis/recomendaciones generadas por IA,
incluyendo los fragmentos de código correctivos.
"""

from __future__ import annotations

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether,
)

from .scraper import RawAuditData

# --- Paleta de marca blanca (personalizable por variables de entorno) ---
BRAND_PRIMARY = colors.HexColor("#4338CA")   # indigo-700
BRAND_DARK = colors.HexColor("#1E1B4B")      # indigo-950
BRAND_LIGHT = colors.HexColor("#EEF2FF")     # indigo-50
COLOR_ALTA = colors.HexColor("#DC2626")
COLOR_MEDIA = colors.HexColor("#D97706")
COLOR_BAJA = colors.HexColor("#059669")

PILLAR_LABELS = {
    "accesibilidad": "Accesibilidad (WCAG)",
    "rendimiento": "Rendimiento (Core Web Vitals)",
    "seo": "SEO Técnico",
    "privacidad": "Privacidad (GDPR/Cookies)",
}

SEVERITY_COLORS = {"alta": COLOR_ALTA, "media": COLOR_MEDIA, "baja": COLOR_BAJA}
SEVERITY_LABELS = {"alta": "ALTA", "media": "MEDIA", "baja": "BAJA"}


def _build_styles() -> dict:
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "TitleBrand", parent=base["Title"], fontSize=24,
            textColor=BRAND_DARK, spaceAfter=4, alignment=TA_LEFT,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle", parent=base["Normal"], fontSize=11,
            textColor=colors.HexColor("#4B5563"), spaceAfter=14,
        ),
        "h2": ParagraphStyle(
            "H2Brand", parent=base["Heading2"], fontSize=15,
            textColor=BRAND_PRIMARY, spaceBefore=18, spaceAfter=8,
        ),
        "h3": ParagraphStyle(
            "H3Brand", parent=base["Heading3"], fontSize=12,
            textColor=BRAND_DARK, spaceBefore=10, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "BodyBrand", parent=base["Normal"], fontSize=10, leading=14,
            textColor=colors.HexColor("#111827"),
        ),
        "small": ParagraphStyle(
            "SmallBrand", parent=base["Normal"], fontSize=8.5, leading=11,
            textColor=colors.HexColor("#6B7280"),
        ),
        "code": ParagraphStyle(
            "CodeBrand", parent=base["Code"], fontSize=8.5, leading=11,
            fontName="Courier", backColor=colors.HexColor("#F3F4F6"),
            textColor=colors.HexColor("#111827"), borderPadding=6,
        ),
        "footer_center": ParagraphStyle(
            "FooterCenter", parent=base["Normal"], fontSize=8,
            textColor=colors.HexColor("#9CA3AF"), alignment=TA_CENTER,
        ),
    }
    return styles


def _score_color(score: int) -> colors.Color:
    if score >= 80:
        return COLOR_BAJA
    if score >= 50:
        return COLOR_MEDIA
    return COLOR_ALTA


def _score_table(pillar_summary: dict, styles: dict) -> Table:
    header = ["Pilar", "Puntaje", "Problemas detectados"]
    rows = [header]
    row_colors = []
    for key, label in PILLAR_LABELS.items():
        info = pillar_summary.get(key, {"score": 0, "total_issues": 0})
        rows.append([label, f"{info['score']}/100", str(info["total_issues"])])
        row_colors.append(_score_color(info["score"]))

    table = Table(rows, colWidths=[7.5 * cm, 3 * cm, 5 * cm])
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_DARK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BRAND_LIGHT]),
        ("ALIGN", (1, 0), (2, -1), "CENTER"),
    ]
    for idx, c in enumerate(row_colors, start=1):
        style_cmds.append(("TEXTCOLOR", (1, idx), (1, idx), c))
        style_cmds.append(("FONTNAME", (1, idx), (1, idx), "Helvetica-Bold"))
    table.setStyle(TableStyle(style_cmds))
    return table


def generate_pdf_report(
    data: RawAuditData,
    pillar_summary: dict,
    ai_analysis: dict,
    brand_name: str = "AuditFlow AI",
    brand_tagline: str = "Auditoría web automatizada con Inteligencia Artificial",
) -> bytes:
    """
    Genera el PDF completo del reporte y devuelve los bytes resultantes,
    listos para enviarse como respuesta HTTP descargable.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=2 * cm, bottomMargin=2 * cm,
        leftMargin=2 * cm, rightMargin=2 * cm,
        title=f"Reporte de Auditoría - {data.final_url}",
        author=brand_name,
    )
    styles = _build_styles()
    story = []

    # --- Portada / encabezado ---
    story.append(Paragraph(brand_name, styles["title"]))
    story.append(Paragraph(brand_tagline, styles["subtitle"]))
    story.append(HRFlowable(width="100%", thickness=1.2, color=BRAND_PRIMARY, spaceAfter=14))

    generated_at = datetime.now().strftime("%d/%m/%Y %H:%M")
    meta_rows = [
        ["Sitio auditado:", data.final_url],
        ["Título de la página:", data.title or "No detectado"],
        ["Código de respuesta HTTP:", str(data.status_code or "N/D")],
        ["Fecha del reporte:", generated_at],
    ]
    meta_table = Table(meta_rows, colWidths=[4.5 * cm, 11 * cm])
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TEXTCOLOR", (0, 0), (0, -1), BRAND_DARK),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 16))

    # --- Resumen ejecutivo (IA) ---
    story.append(Paragraph("Resumen Ejecutivo", styles["h2"]))
    resumen = ai_analysis.get("resumen_ejecutivo", "No disponible.")
    story.append(Paragraph(resumen, styles["body"]))
    if ai_analysis.get("modo_degradado"):
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            "⚠ Este resumen fue generado en modo degradado (sin conexión a IA).",
            styles["small"],
        ))
    story.append(Spacer(1, 12))

    # --- Score por pilar ---
    story.append(Paragraph("Puntaje por Pilar", styles["h2"]))
    story.append(_score_table(pillar_summary, styles))
    story.append(Spacer(1, 8))

    overall_score = round(
        sum(v["score"] for v in pillar_summary.values()) / max(len(pillar_summary), 1)
    )
    story.append(Paragraph(
        f"<b>Puntaje global del sitio: {overall_score}/100</b>",
        styles["body"],
    ))

    story.append(PageBreak())

    # --- Problemas priorizados por IA con código sugerido ---
    story.append(Paragraph("Problemas Priorizados por Impacto Comercial", styles["h2"]))
    problemas = ai_analysis.get("problemas_priorizados", [])
    if not problemas:
        story.append(Paragraph("No se detectaron problemas relevantes.", styles["body"]))
    for idx, p in enumerate(problemas, start=1):
        prioridad = p.get("prioridad", "media")
        color = SEVERITY_COLORS.get(prioridad, COLOR_MEDIA)
        pilar_label = PILLAR_LABELS.get(p.get("pilar", ""), p.get("pilar", "General"))

        block = []
        block.append(Paragraph(
            f'{idx}. {p.get("titulo", "Problema sin título")} '
            f'<font color="{color.hexval()}"><b>[{SEVERITY_LABELS.get(prioridad, "MEDIA")}]</b></font>'
            f' — <i>{pilar_label}</i>',
            styles["h3"],
        ))
        block.append(Paragraph(
            f'<b>Impacto comercial:</b> {p.get("impacto_comercial", "N/D")}',
            styles["body"],
        ))
        codigo = (p.get("codigo_sugerido") or "").strip()
        if codigo:
            lenguaje = p.get("lenguaje_codigo", "texto")
            block.append(Spacer(1, 4))
            block.append(Paragraph(f"<b>Corrección sugerida ({lenguaje}):</b>", styles["small"]))
            escaped_code = (
                codigo.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("\n", "<br/>")
            )
            block.append(Paragraph(escaped_code, styles["code"]))
        block.append(Spacer(1, 10))
        story.append(KeepTogether(block))

    if ai_analysis.get("error_ia"):
        story.append(Spacer(1, 10))
        story.append(Paragraph(
            f"Nota técnica: {ai_analysis['error_ia']}", styles["small"]
        ))

    story.append(PageBreak())

    # --- Detalle técnico completo por pilar ---
    story.append(Paragraph("Detalle Técnico Completo", styles["h2"]))
    for key, label in PILLAR_LABELS.items():
        info = pillar_summary.get(key, {"issues": []})
        story.append(Paragraph(label, styles["h3"]))
        issues = info.get("issues", [])
        if not issues:
            story.append(Paragraph("No se detectaron problemas en este pilar.", styles["body"]))
            continue
        rows = [["Severidad", "Hallazgo", "Detalle"]]
        for issue in issues:
            rows.append([
                SEVERITY_LABELS.get(issue.severity, issue.severity.upper()),
                issue.title,
                issue.detail,
            ])
        t = Table(rows, colWidths=[2.2 * cm, 4.5 * cm, 8.8 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_LIGHT),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E5E7EB")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(t)
        story.append(Spacer(1, 10))

    def _footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#9CA3AF"))
        canvas.drawCentredString(
            A4[0] / 2, 1.2 * cm,
            f"Generado por {brand_name} · {generated_at} · Página {doc_.page}",
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    buffer.seek(0)
    return buffer.read()
