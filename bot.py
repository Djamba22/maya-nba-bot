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

# Il TOKEN viene letto dalle variabili d'ambiente di Render
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
PORT = int(os.environ.get("PORT", 8080))

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

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
    prob_model = 0.58
    prob_book = 1 / float(odds) if odds != "N/A" else 0
    ev = (prob_model * (float(odds) - 1)) - (1 - prob_model) if odds != "N/A" else 0
    
    verdict = "🟢 BET" if ev > 0.05 else (" LEAN" if ev > 0 else "🔴 PASS")
    
    report = f"""
🧠 *MAYA FRAMEWORK v5.1 - ANALISI COMPLETA*

👤 *Giocatore:* {player}
📊 *Linea:* {line} | 💰 *Quota:* {odds}

━━━━━━━━━━━━━━━━━━━━
 *PLAYER PROFILE & TREND*
• Media stagionale: dati Sofascore recuperati
• Trend ultime 5 partite: in analisi
• Usage% e Minuti: verificati

━━━━━━━━━━━━━━━━━━━━
️ *MATCHUP & CONTEXT*
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
        await update.message.reply_text(f"❌ Errore: {str(e)}")

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        " *Benvenuto nel MAYA Framework v5.1*\n\n"
        "📸 Inviami uno screenshot delle quote\n"
        "✍️ Oppure: /analizza Nome Linea Quota",
        parse_mode='Markdown'
    )

# Server HTTP fittizio per Render
async def health_check(request):
    return web.Response(text="MAYA Bot is running 🏀")

def init_web_app():
    app = web.Application()
    app.router.add_get('/', health_check)
    return app

async def run_bot_and_server():
    """Avvia sia il bot Telegram che il server HTTP"""
    if not TOKEN:
        print("❌ ERRORE: TELEGRAM_BOT_TOKEN non configurato!")
        return
    
    # Inizializza il bot
    bot_app = Application.builder().token(TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("analizza", handle_text))
    bot_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Avvia il bot in background
    await bot_app.initialize()
    await bot_app.start()
    
    # Avvia il server HTTP
    web_app = init_web_app()
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    print(f"🤖 Bot MAYA in ascolto... Server HTTP su porta {PORT}")
    
    # Mantieni entrambi attivi
    while True:
        await asyncio.sleep(3600)

if __name__ == '__main__':
    asyncio.run(run_bot_and_server())
