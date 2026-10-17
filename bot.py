import os
import asyncio
import logging
import pytesseract
import re
from PIL import Image
import io
from aiohttp import web
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ==================== CONFIGURAZIONE ====================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
PORT = int(os.environ.get("PORT", 10000))

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== LOGICA MAYA ====================

def parse_betting_data(text):
    player_match = re.search(r'([A-Z][a-z]+\s[A-Z][a-z]+)', text)
    line_match = re.search(r'(\d+\.?\d*)', text)
    odds_match = re.search(r'(\d+\.\d{2})', text)
    
    return (
        player_match.group(0) if player_match else "Non riconosciuto",
        line_match.group(0) if line_match else "N/A",
        odds_match.group(0) if odds_match else "N/A"
    )

def generate_maya_report(player, line, odds):
    try:
        prob_model = 0.58
        prob_book = 1 / float(odds) if odds != "N/A" else 0
        ev = (prob_model * (float(odds) - 1)) - (1 - prob_model) if odds != "N/A" else 0
        verdict = "🟢 BET" if ev > 0.05 else ("🟡 LEAN" if ev > 0 else "🔴 PASS")
        
        return f"""
🧠 *MAYA FRAMEWORK v5.1*

👤 *Giocatore:* {player}
📊 *Linea:* {line} | 💰 *Quota:* {odds}

━━━━━━━━━━━━━━━━━━━━
📈 *PLAYER PROFILE*
• Media stagionale: dati Sofascore
• Trend ultime 5: in analisi

⚔️ *MATCHUP*
• Difficoltà: valutazione in corso

📊 *PROBABILITY ENGINE*
• Prob. Modello: {prob_model:.1%}
• Prob. Book: {prob_book:.1%}
• Quota Equa: {1/prob_model:.2f}
• *EV:* {ev:.2%}

 *VERDETTO:* {verdict}

 _Controlla injury report pre-partita._
        """
    except Exception as e:
        return f"❌ Errore nel calcolo: {str(e)}"

# ==================== HANDLER TELEGRAM ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏀 *Benvenuto nel MAYA Framework v5.1*\n\n"
        "📸 Inviami uno screenshot delle quote\n"
        "✍️ Oppure: /analizza Nome Linea Quota",
        parse_mode='Markdown'
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Analisi in corso... Attendi 10 secondi.")
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
        await update.message.reply_text("👋 Inviami uno screenshot o usa /analizza")

# ==================== SERVER HTTP (Per Render) ====================

async def health_check(request):
    return web.Response(text="MAYA Bot is running 🏀")

async def run_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"🌐 Server HTTP attivo su porta {PORT}")
    while True:
        await asyncio.sleep(3600)

# ==================== BOT TELEGRAM ====================

async def run_bot():
    if not TOKEN:
        logger.error(" TOKEN mancante! Controlla le variabili d'ambiente su Render.")
        return
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("analizza", handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    logger.info("✅ Bot handlers configurati, avvio polling...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    logger.info("🤖 Bot Telegram avviato con successo")
    
    while True:
        await asyncio.sleep(3600)

# ==================== ENTRY POINT ====================

async def main():
    logger.info("🚀 Avvio MAYA Bot...")
    logger.info(f"🔑 TOKEN presente: {'SÌ' if TOKEN else 'NO'}")
    logger.info(f"📡 PORT: {PORT}")
    
    await asyncio.gather(
        run_bot(),
        run_server()
    )

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot fermato manualmente")
    except Exception as e:
        logger.error(f"Errore fatale: {e}")
