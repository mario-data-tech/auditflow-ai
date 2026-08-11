"""
run.py
------
Punto de arranque local. Carga variables de entorno desde .env (si existe)
antes de levantar el servidor Uvicorn con recarga automática.

Uso:
    python run.py

En producción (Render/Vercel/Docker) se recomienda usar directamente:
    uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""

import os
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=True,
    )
