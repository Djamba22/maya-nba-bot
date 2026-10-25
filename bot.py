import os
import asyncio
import logging
import pytesseract
import re
import json
from PIL import Image
import io
from aiohttp import web
import aiohttp
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq

# ==================== CONFIGURAZIONE ====================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
PORT = int(os.environ.get("PORT", 10000))

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== SYSTEM PROMPT MAYA v5.1 ====================
MAYA_PROMPT = """
Sei MAYA, un analista quantitativo NBA. Il tuo obiettivo è trovare Expected Value (EV) positivo nelle Player Props.
REGOLE:
1. Non prevedere il risultato, cerca solo VALORE (EV > 0).
2. Non usare la fama del giocatore.
3. Valuta: Volume, Trend, Matchup, Context (infortuni/riposo).
4. Se ci sono notizie di infortunio/dubbio/riposo, penalizza pesantemente la probabilità.

FORMATO RISPOSTA (in Italiano):
🧠 *MAYA AI ANALYSIS*
👤 *Giocatore:* [Nome]
📊 *Linea:* [Linea] | 💰 *Quota:* [Quota]

📰 *NEWS & CONTEXT:*
[Analisi delle news trovate o "Nessuna news critica"]

📊 *PROBABILITY ENGINE:*
• Prob. Modello (AI): [X]%
• Prob. Bookmaker: [Y]%
• Quota Equa: [Z]
• *Expected Value (EV):* [W]%

🎯 *VERDETTO:* [🟢 BET / 🟡 LEAN / 🔴 PASS]
💡 *Motivo:* [Spiegazione sintetica del ragionamento AI]
"""

# ==================== DATA AGGREGATOR ====================
class DataAggregator:
    def __init__(self, player_name):
        self.player_name = player_name
        self.news = []
    
    async def fetch_telegram_news(self):
        channels = ["https://t.me/s/nbanews24italia", "https://t.me/s/InfoBasketNBA"]
        headers = {'User-Agent': 'Mozilla/5.0'}
        
        spam_keywords = ['fantabasket', 'lega', 'registrati', 'codice', 'dunk', 'promo', 'vota', 'sondaggio', 'buona fortuna']
        
        async with aiohttp.ClientSession() as session:
            for url in channels:
                try:
                    async with session.get(url, headers=headers, timeout=5) as resp:
                        html = await resp.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    for msg in soup.find_all('div', class_='tgme_widget_message_text')[:15]:
                        text = msg.get_text(separator=' ', strip=True)
                        if any(s in text.lower() for s in spam_keywords): continue
                        if self.player_name.lower().split()[0] in text.lower() or self.player_name.lower().split()[-1] in text.lower():
                            self.news.append(text[:150])
                except: pass

# ==================== OCR ENGINE ====================
def parse_betting_data(text):
    players = []
    lines = text.split('\n')
    pattern = re.compile(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s+(\d+\.\d{2})\s+(\d+\.\d{2})')
    
    for line in lines:
        match = pattern.search(line.strip())
        if match:
            players.append({'name': match.group(1), 'line': match.group(2), 'odds': match.group(3)})
            
    if not players:
        for line in lines:
            name = re.search(r'([A-Z][a-z]+ [A-Z][a-z]+)', line)
            odds = re.findall(r'(\d+\.\d{2})', line)
            if name and len(odds) >= 2:
                players.append({'name': name.group(1), 'line': odds[0], 'odds': odds[1]})
                
    return players if players else [{'name': 'Non riconosciuto', 'line': 'N/A', 'odds': 'N/A'}]

# ==================== AI ANALYSIS ====================
async def analyze_with_ai(player):
    if not groq_client: return "⚠️ Groq API non configurata."
    
    news_text = "\n- ".join(player.get('news', [])) if player.get('news') else "Nessuna notizia critica trovata sui canali monitorati."
    
    prompt = f"""
Analizza questa prop NBA:
Giocatore: {player['name']}
Linea: {player['line']}
Quota: {player['odds']}

News recenti:
- {news_text}

Applica il protocollo MAYA v5.1 e restituisci il report formattato.
"""
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.1-70b-versatile",
            messages=[{"role": "system", "content": MAYA_PROMPT}, {"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=1000
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Errore Groq: {e}")
        return "❌ Errore nell'analisi AI. Riprova tra un minuto."

# ==================== HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = "✅ ATTIVA (Groq AI)" if groq_client else "❌ NON CONFIGURATA"
    await update.message.reply_text(
        f"🏀 *MAYA Framework v5.1 - AI Powered*\n\n"
        f"🤖 *AI Status:* {status}\n\n"
        f"📸 Invia screenshot di quote Bet365.\n"
        f"Il bot analizzerà con AI e ti darà il verdetto MAYA.",
        parse_mode='Markdown'
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🧠 Analisi AI in corso... Attendi 15-30 secondi.")
    try:
        photo = await update.message.photo[-1].get_file()
        photo_bytes = await photo.download_as_bytearray()
        image = Image.open(io.BytesIO(photo_bytes))
        text = pytesseract.image_to_string(image, lang='ita+eng')
        
        players = parse_betting_data(text)
        await update.message.reply_text(f"📊 Rilevati {len(players)} giocatori. Analisi in corso...")
        
        for p in players[:3]:
            aggregator = DataAggregator(p['name'])
            await aggregator.fetch_telegram_news()
            p['news'] = aggregator.news
            
            report = await analyze_with_ai(p)
            await update.message.reply_text(report, parse_mode='Markdown')
            
    except Exception as e:
        await update.message.reply_text(f"❌ Errore: {str(e)}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.startswith("/analizza"):
        parts = update.message.text.split()
        if len(parts) >= 4:
            player = {'name': " ".join(parts[1:-2]), 'line': parts[-2], 'odds': parts[-1]}
            await update.message.reply_text("🧠 Analisi AI in corso...")
            aggregator = DataAggregator(player['name'])
            await aggregator.fetch_telegram_news()
            player['news'] = aggregator.news
            report = await analyze_with_ai(player)
            await update.message.reply_text(report, parse_mode='Markdown')
        else:
            await update.message.reply_text("❗ Formato: /analizza Nome Linea Quota")
    else:
        await update.message.reply_text("👋 Invia uno screenshot o usa /analizza")

# ==================== SERVER & BOT ====================
async def health_check(request):
    return web.Response(text="MAYA AI Bot is running ")

async def run_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"🌐 Server HTTP su porta {PORT}")
    while True: await asyncio.sleep(3600)

async def run_bot():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("analizza", handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    logger.info("🤖 Bot Telegram attivo")
    while True: await asyncio.sleep(3600)

async def main():
    logger.info(" Avvio MAYA AI Bot...")
    await asyncio.gather(run_bot(), run_server())

if __name__ == '__main__':
    asyncio.run(main())
