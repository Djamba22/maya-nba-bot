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

# Esponi la porta 8080 per Render
EXPOSE 8080

# Avvia il bot
CMD ["python", "bot.py"]
