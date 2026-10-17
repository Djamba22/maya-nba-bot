FROM python:3.11-slim

# Installa Tesseract OCR e le dipendenze di sistema
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-ita \
    tesseract-ocr-eng \
    libtesseract-dev \
    && rm -rf /var/lib/apt/lists/*

# Imposta la cartella di lavoro
WORKDIR /app

# Copia i file delle dipendenze
COPY requirements.txt .

# Installa le librerie Python
RUN pip install --no-cache-dir -r requirements.txt

# Copia il codice del bot
COPY . .

# Esponi la porta
EXPOSE 10000

# Avvia con gunicorn (production server)
CMD ["gunicorn", "--bind", "0.0.0.0:10000", "--workers", "1", "bot:app"]
