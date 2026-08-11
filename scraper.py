"""
scraper.py
-----------
Motor de extracción y análisis heurístico de AuditFlow AI.

Usa Playwright (Chromium headless) para renderizar la página como lo haría
un navegador real (necesario para SPAs modernas) y BeautifulSoup para
parsear el DOM resultante. También mide tiempos de carga como proxy de
Core Web Vitals, ya que herramientas como Lighthouse requieren Chrome
DevTools Protocol completo -- aquí generamos una aproximación honesta
basada en timings reales de red/renderizado capturados por Playwright.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


class ScraperError(Exception):
    """Error controlado durante el scraping (URL inválida, timeout, DNS, etc.)."""


@dataclass
class AuditIssue:
    pillar: str          # "accesibilidad" | "rendimiento" | "seo" | "privacidad"
    severity: str         # "alta" | "media" | "baja"
    title: str
    detail: str


@dataclass
class RawAuditData:
    url: str
    final_url: str
    status_code: int | None
    html: str
    title: str | None
    meta_description: str | None
    lang_attr: str | None
    h1_tags: list[str] = field(default_factory=list)
    h2_tags: list[str] = field(default_factory=list)
    images_total: int = 0
    images_without_alt: int = 0
    links_total: int = 0
    links_without_text: int = 0
    has_viewport_meta: bool = False
    has_canonical: bool = False
    cookie_banner_detected: bool = False
    privacy_policy_link_found: bool = False
    forms_without_labels: int = 0
    inline_scripts_count: int = 0
    external_scripts_count: int = 0
    page_weight_bytes: int = 0
    # Timings (ms), aproximación de Core Web Vitals reales
    dom_content_loaded_ms: float = 0.0
    load_event_ms: float = 0.0
    time_to_first_byte_ms: float = 0.0
    largest_content_paint_approx_ms: float = 0.0


COOKIE_KEYWORDS = [
    "cookie", "cookies", "consentimiento", "consent", "gdpr", "rgpd",
    "aceptar todas", "accept all", "política de cookies", "cookie policy",
]

PRIVACY_KEYWORDS = [
    "política de privacidad", "privacy policy", "aviso de privacidad",
    "términos y condiciones", "terms of service", "privacidad",
]


async def _fetch_page(url: str, timeout_ms: int = 20000) -> tuple[str, dict]:
    """
    Renderiza la URL con Playwright y devuelve el HTML final más un
    diccionario de métricas de timing. Lanza ScraperError en fallos.
    """
    timings: dict = {}
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"],
            )
        except Exception as exc:  # binarios no instalados, permisos, etc.
            raise ScraperError(
                f"No se pudo iniciar el navegador headless: {exc}. "
                "Verificá que 'playwright install chromium' se haya ejecutado."
            ) from exc

        try:
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (compatible; AuditFlowAI/1.0; "
                    "+https://auditflow.ai/bot)"
                ),
                viewport={"width": 1366, "height": 768},
            )
            page = await context.new_page()

            start = time.perf_counter()
            try:
                response = await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                # Reintento con condición más laxa si la red nunca queda "idle"
                # (común en sitios con polling/analytics constante).
                try:
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                except PlaywrightTimeoutError as exc:
                    raise ScraperError(
                        f"Tiempo de espera agotado al cargar '{url}'. "
                        "El sitio tardó demasiado en responder."
                    ) from exc

            load_end = time.perf_counter()

            if response is None:
                raise ScraperError(f"No se obtuvo respuesta HTTP válida para '{url}'.")

            status_code = response.status
            if status_code >= 400:
                raise ScraperError(
                    f"El sitio respondió con código HTTP {status_code}. "
                    "Verificá que la URL sea correcta y esté accesible públicamente."
                )

            # Métricas de performance vía Navigation Timing API del propio navegador
            try:
                perf = await page.evaluate(
                    """() => {
                        const nav = performance.getEntriesByType('navigation')[0];
                        if (!nav) return null;
                        return {
                            ttfb: nav.responseStart - nav.requestStart,
                            domContentLoaded: nav.domContentLoadedEventEnd - nav.startTime,
                            loadEvent: nav.loadEventEnd - nav.startTime,
                            transferSize: nav.transferSize || 0
                        };
                    }"""
                )
            except Exception:
                perf = None

            html = await page.content()
            final_url = page.url

            wall_clock_ms = (load_end - start) * 1000
            if perf:
                timings["ttfb"] = max(perf.get("ttfb", 0.0), 0.0)
                timings["dom_content_loaded"] = max(perf.get("domContentLoaded", 0.0), 0.0)
                timings["load_event"] = max(perf.get("loadEvent", wall_clock_ms), 0.0)
                timings["transfer_size"] = perf.get("transferSize", 0) or len(html.encode("utf-8"))
            else:
                # Fallback si la Navigation Timing API no está disponible
                timings["ttfb"] = wall_clock_ms * 0.15
                timings["dom_content_loaded"] = wall_clock_ms * 0.7
                timings["load_event"] = wall_clock_ms
                timings["transfer_size"] = len(html.encode("utf-8"))

            timings["status_code"] = status_code
            timings["final_url"] = final_url
            return html, timings
        finally:
            await browser.close()


def _normalize_url(raw_url: str) -> str:
    raw_url = raw_url.strip()
    if not raw_url:
        raise ScraperError("La URL no puede estar vacía.")
    parsed = urlparse(raw_url)
    if not parsed.scheme:
        raw_url = f"https://{raw_url}"
        parsed = urlparse(raw_url)
    if parsed.scheme not in ("http", "https"):
        raise ScraperError("Solo se admiten URLs con esquema http o https.")
    if not parsed.netloc:
        raise ScraperError(f"La URL '{raw_url}' no es válida.")
    return raw_url


def _analyze_html(html: str, url: str, timings: dict) -> RawAuditData:
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None

    meta_desc_tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    meta_description = meta_desc_tag.get("content", "").strip() if meta_desc_tag else None

    html_tag = soup.find("html")
    lang_attr = html_tag.get("lang") if html_tag else None

    h1_tags = [h.get_text(strip=True) for h in soup.find_all("h1")]
    h2_tags = [h.get_text(strip=True) for h in soup.find_all("h2")]

    images = soup.find_all("img")
    images_total = len(images)
    images_without_alt = sum(
        1 for img in images if not img.get("alt") or not img.get("alt").strip()
    )

    links = soup.find_all("a")
    links_total = len(links)
    links_without_text = sum(
        1 for a in links
        if not a.get_text(strip=True) and not a.get("aria-label")
    )

    viewport_tag = soup.find("meta", attrs={"name": "viewport"})
    has_viewport_meta = viewport_tag is not None

    canonical_tag = soup.find("link", attrs={"rel": "canonical"})
    has_canonical = canonical_tag is not None

    page_text_lower = soup.get_text(" ", strip=True).lower()
    html_lower = html.lower()

    cookie_banner_detected = any(kw in page_text_lower for kw in COOKIE_KEYWORDS) or any(
        kw in html_lower for kw in ["cookie-banner", "cookie_consent", "cookieconsent", "cky-consent"]
    )

    privacy_policy_link_found = False
    for a in links:
        link_text = a.get_text(strip=True).lower()
        href = (a.get("href") or "").lower()
        if any(kw in link_text for kw in PRIVACY_KEYWORDS) or any(
            kw in href for kw in ["privacy", "privacidad"]
        ):
            privacy_policy_link_found = True
            break

    forms = soup.find_all("form")
    forms_without_labels = 0
    for form in forms:
        inputs = form.find_all(["input", "textarea", "select"])
        labels = form.find_all("label")
        labeled_ids = {lbl.get("for") for lbl in labels if lbl.get("for")}
        for inp in inputs:
            input_type = (inp.get("type") or "").lower()
            if input_type in ("hidden", "submit", "button"):
                continue
            has_aria = bool(inp.get("aria-label") or inp.get("aria-labelledby"))
            has_id_label = inp.get("id") in labeled_ids if inp.get("id") else False
            if not (has_aria or has_id_label):
                forms_without_labels += 1

    scripts = soup.find_all("script")
    inline_scripts_count = sum(1 for s in scripts if not s.get("src"))
    external_scripts_count = sum(1 for s in scripts if s.get("src"))

    return RawAuditData(
        url=url,
        final_url=timings.get("final_url", url),
        status_code=timings.get("status_code"),
        html=html,
        title=title,
        meta_description=meta_description,
        lang_attr=lang_attr,
        h1_tags=h1_tags,
        h2_tags=h2_tags,
        images_total=images_total,
        images_without_alt=images_without_alt,
        links_total=links_total,
        links_without_text=links_without_text,
        has_viewport_meta=has_viewport_meta,
        has_canonical=has_canonical,
        cookie_banner_detected=cookie_banner_detected,
        privacy_policy_link_found=privacy_policy_link_found,
        forms_without_labels=forms_without_labels,
        inline_scripts_count=inline_scripts_count,
        external_scripts_count=external_scripts_count,
        page_weight_bytes=int(timings.get("transfer_size", len(html.encode("utf-8")))),
        dom_content_loaded_ms=round(timings.get("dom_content_loaded", 0.0), 1),
        load_event_ms=round(timings.get("load_event", 0.0), 1),
        time_to_first_byte_ms=round(timings.get("ttfb", 0.0), 1),
        largest_content_paint_approx_ms=round(
            timings.get("dom_content_loaded", 0.0) * 1.15, 1
        ),
    )


async def scrape_url(raw_url: str) -> RawAuditData:
    """
    Punto de entrada principal del módulo. Normaliza la URL, la renderiza
    con Playwright y devuelve los datos crudos ya parseados.
    """
    url = _normalize_url(raw_url)
    try:
        html, timings = await _fetch_page(url)
    except ScraperError:
        raise
    except Exception as exc:  # cualquier otro fallo no anticipado de red/DNS
        raise ScraperError(f"No se pudo acceder a '{url}': {exc}") from exc

    if not html or len(html.strip()) < 20:
        raise ScraperError(f"'{url}' devolvió una página vacía o sin contenido HTML útil.")

    return _analyze_html(html, url, timings)


def build_issues(data: RawAuditData) -> list[AuditIssue]:
    """
    Analizador lógico: convierte los datos crudos en una lista de
    problemas detectados, clasificados por pilar y severidad.
    """
    issues: list[AuditIssue] = []

    # --- SEO técnico ---
    if not data.title:
        issues.append(AuditIssue("seo", "alta", "Falta la etiqueta <title>",
                                  "La página no tiene título, lo que perjudica gravemente el CTR en resultados de búsqueda."))
    elif len(data.title) > 60:
        issues.append(AuditIssue("seo", "baja", "Título demasiado largo",
                                  f"El título tiene {len(data.title)} caracteres; Google suele truncar más allá de ~60."))

    if not data.meta_description:
        issues.append(AuditIssue("seo", "alta", "Falta meta description",
                                  "No hay etiqueta meta description, lo que reduce el control sobre el snippet en buscadores."))
    elif len(data.meta_description) > 160:
        issues.append(AuditIssue("seo", "baja", "Meta description demasiado larga",
                                  f"Tiene {len(data.meta_description)} caracteres; se recomienda no superar 160."))

    if len(data.h1_tags) == 0:
        issues.append(AuditIssue("seo", "alta", "Ausencia de encabezado H1",
                                  "No se detectó ningún H1. El H1 es clave para la jerarquía semántica y el SEO on-page."))
    elif len(data.h1_tags) > 1:
        issues.append(AuditIssue("seo", "media", "Múltiples etiquetas H1",
                                  f"Se detectaron {len(data.h1_tags)} etiquetas H1; lo recomendado es una sola por página."))

    if not data.has_canonical:
        issues.append(AuditIssue("seo", "media", "Falta etiqueta canonical",
                                  "Sin <link rel='canonical'> hay riesgo de contenido duplicado en buscadores."))

    if data.links_without_text > 0:
        issues.append(AuditIssue("seo", "baja", "Enlaces sin texto descriptivo",
                                  f"{data.links_without_text} enlaces no tienen texto ni aria-label, afectando SEO y accesibilidad."))

    # --- Accesibilidad (WCAG) ---
    if not data.lang_attr:
        issues.append(AuditIssue("accesibilidad", "alta", "Falta atributo lang en <html>",
                                  "Sin el atributo 'lang', los lectores de pantalla no pueden anunciar el idioma correcto (WCAG 3.1.1)."))

    if data.images_total > 0 and data.images_without_alt > 0:
        pct = round(data.images_without_alt / data.images_total * 100)
        issues.append(AuditIssue(
            "accesibilidad", "alta", "Imágenes sin atributo alt",
            f"{data.images_without_alt} de {data.images_total} imágenes ({pct}%) no tienen texto alternativo (WCAG 1.1.1)."
        ))

    if not data.has_viewport_meta:
        issues.append(AuditIssue("accesibilidad", "media", "Falta meta viewport",
                                  "Sin meta viewport la experiencia en móviles y el zoom accesible se ven comprometidos."))

    if data.forms_without_labels > 0:
        issues.append(AuditIssue(
            "accesibilidad", "alta", "Campos de formulario sin etiqueta asociada",
            f"{data.forms_without_labels} campos no tienen <label> ni aria-label (WCAG 1.3.1 / 4.1.2)."
        ))

    # --- Rendimiento (Core Web Vitals aproximados) ---
    if data.time_to_first_byte_ms > 600:
        issues.append(AuditIssue(
            "rendimiento", "alta", "TTFB elevado",
            f"El Time to First Byte es de {data.time_to_first_byte_ms:.0f} ms; se recomienda mantenerlo por debajo de 600 ms."
        ))
    if data.largest_content_paint_approx_ms > 2500:
        issues.append(AuditIssue(
            "rendimiento", "alta", "LCP aproximado por encima del umbral 'Bueno'",
            f"El LCP estimado es de {data.largest_content_paint_approx_ms:.0f} ms (umbral recomendado: <2500 ms)."
        ))
    elif data.largest_content_paint_approx_ms > 4000:
        issues.append(AuditIssue(
            "rendimiento", "alta", "LCP muy por encima del umbral aceptable",
            f"El LCP estimado es de {data.largest_content_paint_approx_ms:.0f} ms, clasificado como 'Pobre' por Google."
        ))

    if data.page_weight_bytes > 3_000_000:
        mb = data.page_weight_bytes / 1_000_000
        issues.append(AuditIssue(
            "rendimiento", "media", "Peso de página elevado",
            f"La página pesa aproximadamente {mb:.1f} MB, lo que puede ralentizar la carga en conexiones móviles."
        ))

    if data.external_scripts_count > 15:
        issues.append(AuditIssue(
            "rendimiento", "media", "Exceso de scripts externos",
            f"Se detectaron {data.external_scripts_count} scripts externos, lo que puede bloquear el renderizado."
        ))

    # --- Privacidad / Cumplimiento (GDPR / Cookies) ---
    if not data.cookie_banner_detected:
        issues.append(AuditIssue(
            "privacidad", "alta", "No se detectó banner de consentimiento de cookies",
            "No se encontró un banner o mecanismo visible de consentimiento de cookies, requerido por GDPR/RGPD y normativas similares."
        ))

    if not data.privacy_policy_link_found:
        issues.append(AuditIssue(
            "privacidad", "alta", "No se encontró enlace a política de privacidad",
            "No se detectó un enlace visible a una política de privacidad o términos, un requisito legal básico en la mayoría de jurisdicciones."
        ))

    return issues


def summarize_by_pillar(issues: list[AuditIssue]) -> dict:
    """Calcula un score simple 0-100 por pilar en base a severidad de issues."""
    pillars = ["accesibilidad", "rendimiento", "seo", "privacidad"]
    penalty = {"alta": 25, "media": 12, "baja": 5}
    result = {}
    for pillar in pillars:
        pillar_issues = [i for i in issues if i.pillar == pillar]
        score = 100
        for issue in pillar_issues:
            score -= penalty.get(issue.severity, 5)
        score = max(score, 0)
        result[pillar] = {
            "score": score,
            "issues": pillar_issues,
            "total_issues": len(pillar_issues),
        }
    return result
