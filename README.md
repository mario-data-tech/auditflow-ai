# AuditFlow AI

Plataforma SaaS de auditoría web automatizada: Accesibilidad (WCAG),
Rendimiento (Core Web Vitals), SEO Técnico y Privacidad (GDPR/Cookies),
con análisis priorizado por IA y reporte PDF con marca blanca.

## 1. Instalación

```bash
# 1. Cloná / entrá al proyecto
cd auditflow-ai

# 2. Creá un entorno virtual (recomendado)
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 3. Instalá dependencias Python
pip install -r requirements.txt

# 4. Instalá el navegador headless de Playwright (obligatorio)
playwright install --with-deps chromium
```

## 2. Variables de entorno

Copiá el archivo de ejemplo y completá tu API key de OpenAI:

```bash
cp .env.example .env
```

Contenido de `.env`:

```
OPENAI_API_KEY=sk-tu-api-key-aqui
OPENAI_MODEL=gpt-4o-mini
BRAND_NAME=AuditFlow AI
BRAND_TAGLINE=Auditoría web automatizada con Inteligencia Artificial
```

> Si `OPENAI_API_KEY` no está configurada, la app sigue funcionando en
> **modo degradado**: el motor heurístico funciona igual, solo se omite
> el resumen ejecutivo y la priorización comercial generados por IA.

## 3. Ejecutar en local

```bash
python run.py
```

Abrí `http://localhost:8000` en el navegador.

Alternativa directa con Uvicorn:

```bash
uvicorn app.main:app --reload
```

## 4. Estructura del proyecto

```
auditflow-ai/
├── app/
│   ├── main.py            # Rutas FastAPI (/, /api/audit, /api/audit/pdf)
│   ├── scraper.py         # Motor de scraping (Playwright) + reglas heurísticas
│   ├── ai_analyzer.py     # Integración con OpenAI (resumen, priorización, código)
│   ├── pdf_generator.py   # Generación del PDF con marca blanca (ReportLab)
│   ├── templates/
│   │   └── index.html     # Frontend (Tailwind CDN + JS vanilla)
│   └── static/             # Archivos estáticos (opcional)
├── requirements.txt
├── run.py
├── render.yaml             # Configuración lista para desplegar en Render
├── .env.example
└── .gitignore
```

## 5. Despliegue

### Render
1. Subí el proyecto a un repositorio de GitHub.
2. En Render: **New > Blueprint** y apuntá al repo (usa `render.yaml`
   automáticamente), o creá un **Web Service** manual con:
   - Build Command: `pip install -r requirements.txt && playwright install --with-deps chromium`
   - Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
3. Configurá `OPENAI_API_KEY` en la sección Environment del servicio.

### Vercel
Vercel Functions no soporta binarios de Chromium de Playwright de forma
nativa (límite de tamaño de función serverless). Para desplegar en
Vercel, usá el frontend estático + un backend en Render/Railway/Fly.io
para el servicio de scraping, o migrá `scraper.py` a un servicio de
scraping gestionado (Browserless, ScrapingBee) que sí sea compatible
con funciones serverless.

### Docker (opcional, recomendado para cualquier VPS)

```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## 6. Notas técnicas

- **Core Web Vitals**: se calculan a partir de la Navigation Timing API
  real del navegador (TTFB, DOMContentLoaded, Load) capturada por
  Playwright. El LCP es una aproximación derivada de esos timings, no
  un valor de campo (CrUX) ni de laboratorio completo tipo Lighthouse.
- **Manejo de errores**: URLs inválidas, timeouts de red, códigos HTTP
  ≥400 y fallos del navegador headless se capturan como `ScraperError`
  y se traducen a HTTP 422 con mensaje claro para el usuario.
- **Resiliencia de la IA**: cualquier fallo de OpenAI (timeout, rate
  limit, JSON inválido, falta de API key) degrada automáticamente a un
  análisis basado solo en las reglas heurísticas, sin romper la app.
