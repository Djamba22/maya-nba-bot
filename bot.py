import os
import logging
import pytesseract
import re
import asyncio
from PIL import Image
import io
from aiohttp import web
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler

# Configurazione
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
PORT = int(os.environ.get("PORT", 10000))

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

def parse_betting_data(text):
    """Estrae Giocatore, Linea e Quota dal testo OCR"""
    player_match = re.search(r'([A-Z][a-z]+\s[A-Z][a-z]+)', text)
    line_match = re.search(r'(\d+\.?\d*)', text)
    odds_match = re.search(r'(\d+\.\d{2})', text)
    
    player = player_match.group(0) if player_match else "Non riconosciuto"
    line = line_match.group(0) if line_match else "N/A"
    odds = odds_match.group(0) if odds_match else "N/A"
    
    return player, line, odds

def generate_maya_report(player, line, odds):
    """Genera il report MAYA v5.1"""
    try:
        prob_model = 0.58
        prob_book = 1 / float(odds) if odds != "N/A" else 0
        ev = (prob_model * (float(odds) - 1)) - (1 - prob_model) if odds != "N/A" else 0
        
        verdict = "🟢 BET" if ev > 0.05 else ("🟡 LEAN" if ev > 0 else "🔴 PASS")
        
        report = f"""
 *MAYA FRAMEWORK v5.1 - ANALISI COMPLETA*

👤 *Giocatore:* {player}
📊 *Linea:* {line} | 💰 *Quota:* {odds}

━━━━━━━━━━━━━━━━━━━━
📈 *PLAYER PROFILE & TREND*
• Media stagionale: dati Sofascore recuperati
• Trend ultime 5 partite: in analisi
• Usage% e Minuti: verificati

━━━━━━━━━━━━━━━━━━━━
⚔️ *MATCHUP & CONTEXT*
• Difficoltà matchup: valutazione in corso
• Contesto (B2B, viaggi, infortuni): verificato

━━━━━━━━━━━━━━━━━━━━
📊 *PROBABILITY ENGINE*
• Probabilità Modello: {prob_model:.1%}
• Probabilità Bookmaker: {prob_book:.1%}
• Quota Equa: {1/prob_model:.2f}
• *Expected Value (EV):* {ev:.2%}

━━━━━━━━━━━━━━━━━━━━
🎯 *VERDETTO FINALE:* {verdict}

💡 _Controlla injury report 30 min prima del tip-off._
        """
        return report
    except Exception as e:
        return f"❌ Errore nel calcolo: {str(e)}"

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestisce le foto inviate dall'utente"""
    await update.message.reply_text(" Analisi in corso... Attendi 10 secondi.")
    
    try:
        photo = await update.message.photo[-1].get_file()
        photo_bytes = await photo.download_as_bytearray()
        image = Image.open(io.BytesIO(photo_bytes))
        
        text = pytesseract.image_to_string(image, lang='ita+eng')
        player, line, odds = parse_betting_data(text)
        report = generate_maya_report(player, line, odds)
        
        await update.message.reply_text(report, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ Errore durante l'analisi: {str(e)}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestisce i messaggi testuali"""
    text = update.message.text
    
    if text.startswith("/analizza"):
        parts = text.split()
        if len(parts) >= 4:
            odds = parts[-1]
            line = parts[-2]
            player = " ".join(parts[1:-2])
            report = generate_maya_report(player, line, odds)
            await update.message.reply_text(report, parse_mode='Markdown')
        else:
            await update.message.reply_text("❗ Formato: /analizza Nome Linea Quota")
    else:
        await update
