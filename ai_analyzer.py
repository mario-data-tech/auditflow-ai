"""
ai_analyzer.py
--------------
Capa de inteligencia artificial de AuditFlow AI.

Toma los datos crudos + issues detectados por el motor heurístico
(scraper.py) y le pide a un modelo de OpenAI que:
  a) Redacte un resumen ejecutivo del estado del sitio.
  b) Priorice los errores según impacto comercial (no solo técnico).
  c) Genere fragmentos de código correctivos listos para copiar/pegar.

El resultado se devuelve siempre como un dict con estructura fija,
validado y con manejo de fallback si la API falla o no hay API key
configurada (para que el resto de la app no se rompa en un demo).
"""

from __future__ import annotations

import json
import os

from openai import OpenAI, APIError, APIConnectionError, APITimeoutError, RateLimitError

from .scraper import AuditIssue, RawAuditData

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


class AIAnalysisError(Exception):
    """Error controlado durante el análisis con IA."""


SYSTEM_PROMPT = """\
Sos un consultor senior de auditoría web (SEO técnico, performance, \
accesibilidad WCAG y cumplimiento de privacidad/GDPR). Recibís datos \
crudos de una auditoría automatizada y una lista de problemas ya \
detectados por un motor heurístico. Tu trabajo es:

1. Escribir un resumen ejecutivo de 3 a 5 frases, en español, dirigido \
a un dueño de negocio no técnico, explicando el estado general del \
sitio y el riesgo/impacto de no corregirlo.
2. Tomar la lista de problemas detectados y priorizarlos por IMPACTO \
COMERCIAL (pérdida de conversión, riesgo legal, pérdida de tráfico \
orgánico, mala experiencia de usuario) — no solo por severidad técnica. \
Devolvé como máximo los 8 más importantes.
3. Para cada uno de esos problemas priorizados, generar un fragmento de \
código corto (HTML, CSS o JS según corresponda) que lo resuelva o \
ejemplifique la corrección, listo para copiar y pegar.

Respondé EXCLUSIVAMENTE en formato JSON válido, sin texto adicional, \
sin markdown, sin backticks, con exactamente esta forma:

{
  "resumen_ejecutivo": "string",
  "problemas_priorizados": [
    {
      "titulo": "string",
      "pilar": "accesibilidad|rendimiento|seo|privacidad",
      "impacto_comercial": "string explicando el impacto de negocio",
      "prioridad": "alta|media|baja",
      "codigo_sugerido": "string con el fragmento de código, o cadena vacía si no aplica",
      "lenguaje_codigo": "html|css|javascript|texto"
    }
  ]
}
"""


def _build_user_prompt(data: RawAuditData, issues: list[AuditIssue]) -> str:
    issues_payload = [
        {
            "pilar": i.pillar,
            "severidad": i.severity,
            "titulo": i.title,
            "detalle": i.detail,
        }
        for i in issues
    ]

    context = {
        "url_analizada": data.final_url,
        "codigo_http": data.status_code,
        "titulo_pagina": data.title,
        "meta_description": data.meta_description,
        "atributo_lang": data.lang_attr,
        "cantidad_h1": len(data.h1_tags),
        "cantidad_h2": len(data.h2_tags),
        "imagenes_totales": data.images_total,
        "imagenes_sin_alt": data.images_without_alt,
        "tiene_viewport": data.has_viewport_meta,
        "tiene_canonical": data.has_canonical,
        "banner_cookies_detectado": data.cookie_banner_detected,
        "enlace_privacidad_encontrado": data.privacy_policy_link_found,
        "campos_formulario_sin_label": data.forms_without_labels,
        "peso_pagina_kb": round(data.page_weight_bytes / 1024, 1),
        "ttfb_ms": data.time_to_first_byte_ms,
        "lcp_aproximado_ms": data.largest_content_paint_approx_ms,
        "problemas_detectados": issues_payload,
    }
    return (
        "Estos son los datos crudos y problemas detectados en la auditoría "
        f"automatizada del sitio:\n\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        "Generá el JSON de salida según las instrucciones del sistema."
    )


def _fallback_analysis(issues: list[AuditIssue]) -> dict:
    """
    Análisis de respaldo sin IA, usado si OPENAI_API_KEY no está
    configurada o la llamada a la API falla. Garantiza que la app
    siga siendo funcional en modo degradado.
    """
    severity_order = {"alta": 0, "media": 1, "baja": 2}
    sorted_issues = sorted(issues, key=lambda i: severity_order.get(i.severity, 3))[:8]
    return {
        "resumen_ejecutivo": (
            "No fue posible generar el resumen ejecutivo con IA en este momento "
            "(verificá la configuración de OPENAI_API_KEY). A continuación se "
            "muestran los hallazgos del motor de auditoría heurístico, ordenados "
            "por severidad técnica."
        ),
        "problemas_priorizados": [
            {
                "titulo": i.title,
                "pilar": i.pillar,
                "impacto_comercial": i.detail,
                "prioridad": i.severity,
                "codigo_sugerido": "",
                "lenguaje_codigo": "texto",
            }
            for i in sorted_issues
        ],
        "modo_degradado": True,
    }


def run_ai_analysis(data: RawAuditData, issues: list[AuditIssue]) -> dict:
    """
    Punto de entrada principal. Llama a OpenAI con los datos de la
    auditoría y devuelve un dict estructurado. Si la API key no está
    configurada o la llamada falla, degrada de forma controlada a un
    análisis basado únicamente en las reglas heurísticas.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return _fallback_analysis(issues)

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.3,
            max_tokens=2000,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(data, issues)},
            ],
            timeout=45,
        )
    except (APIConnectionError, APITimeoutError) as exc:
        return _fallback_analysis(issues) | {
            "error_ia": f"No se pudo conectar con OpenAI: {exc}"
        }
    except RateLimitError as exc:
        return _fallback_analysis(issues) | {
            "error_ia": f"Límite de uso de OpenAI alcanzado: {exc}"
        }
    except APIError as exc:
        return _fallback_analysis(issues) | {
            "error_ia": f"Error de la API de OpenAI: {exc}"
        }
    except Exception as exc:  # cinturón y tirantes: nunca tumbar la auditoría por la IA
        return _fallback_analysis(issues) | {
            "error_ia": f"Error inesperado al invocar IA: {exc}"
        }

    raw_content = response.choices[0].message.content
    try:
        parsed = json.loads(raw_content)
    except (json.JSONDecodeError, TypeError) as exc:
        return _fallback_analysis(issues) | {
            "error_ia": f"La respuesta de la IA no fue JSON válido: {exc}"
        }

    if "resumen_ejecutivo" not in parsed or "problemas_priorizados" not in parsed:
        return _fallback_analysis(issues) | {
            "error_ia": "La respuesta de la IA no tuvo la estructura esperada."
        }

    parsed["modo_degradado"] = False
    return parsed
