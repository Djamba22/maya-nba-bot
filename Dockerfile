FROM python:3.11-slim

# Installa Tesseract e dipendenze di sistema
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-ita \
    tesseract-ocr-eng \
    libtesseract-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 10000

# FORZA l'esecuzione diretta di Python. Niente Gunicorn.
CMD ["python", "bot.py"]
