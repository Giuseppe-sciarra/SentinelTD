# Python + Playwright da pip, come il CRM: Chromium, Chrome e le librerie di sistema li installa
# Playwright stesso durante la build. Prima si partiva dall'immagine Microsoft
# (mcr.microsoft.com/playwright/python), che esce anche giorni dopo il pacchetto pip: l'aggiornamento
# notturno doveva aspettarla. Cosi' Playwright sale appena e' su PyPI.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app

COPY requirements.txt .
# Chromium di Playwright + tutte le librerie di sistema e i font che gli servono (--with-deps).
# libatomic1: serve al Node incluso in Playwright (dalla 1.65, Node 26) e nell'immagine slim non c'e'.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libatomic1 \
 && pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

# Google Chrome: il Chromium incluso in Playwright NON ha i codec proprietari
# (H.264/AAC), quindi i video di sfondo in MP4 non vengono decodificati e nello
# screenshot resta un rettangolo vuoto. Chrome li supporta. Se il download fallisce
# il build prosegue: lo shooter ripiega su Chromium da solo.
RUN (playwright install --with-deps chrome || echo "Chrome non installato: verra' usato Chromium") \
 && rm -rf /var/lib/apt/lists/*

COPY shooter.py .

EXPOSE 8090
CMD ["uvicorn", "shooter:app", "--host", "0.0.0.0", "--port", "8090"]
