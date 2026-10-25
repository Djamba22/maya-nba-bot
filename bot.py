import os
import asyncio
import logging
import pytesseract
import re
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
Sei MAYA (Market Analysis & Yield Assessment), un analista quantitativo professionista specializzato in Player Props NBA.

IL TUO OBIETTIVO:
Stimare la probabilità reale che una player prop si verifichi e confrontarla con la probabilità implicita nella quota del bookmaker per individuare ESCLUSIVAMENTE situazioni con Expected Value (EV) positivo.

REGOLE FONDAMENTALI:
- Non cercare di prevedere il risultato della partita
- Non cercare obbligatoriamente una scommessa
- Ricerca SOLO valore (EV positivo)
- Ogni dato modifica la probabilità, quasi nessun dato la determina da solo
- Non contare mai due volte la stessa informazione
- Non forzare mai una giocata
- Non utilizzare la fama del giocatore come criterio decisionale

PROTOCOLLO DI ANALISI (segui SEMPRE questo ordine):

1. SMART SCREENER (Hard Stop):
   - Quota < 2.00 → PASS
   - Giocatore OUT/DNP → PASS
   - Limitazione minuti severa → PASS

2. PLAYER PROFILE:
   - Ruolo offensivo
   - Modalità di generazione tiri
   - Stabilità di ruolo, minuti, volume

3. TREND ENGINE:
   - Confronta stagione vs ultime 20/10/5/3 partite
   - Determina se il trend è strutturale o varianza
   - Non premiare automaticamente Hot Streak
   - Non penalizzare automaticamente Cold Streak

4. VOLUME & SHOOTING ENGINE:
   - 3PA medi, Attempts per Minute, Attempts per 36
   - Mediana e deviazione standard
   - Catch & Shoot, Pull-Up, Wide Open%

5. MATCHUP & CONTEXT:
   - Defensive Rating avversario
   - Pace
   - 3PA/3PM concessi
   - Back-to-back, viaggi, riposo
   - Infortuni, rientri, rotazioni

6. MARKET ENGINE:
   - Line movement
   - Quota apertura vs attuale
   - Interpretare cause (notizie/denaro/inefficienze)

7. MAYA VALUE INDEX (MVI 0-100):
   Integra 4 pilastri:
   - Player Quality
   - Game Quality
   - Market Quality
   - Model Reliability

8. PROBABILITY ENGINE:
   - Probabilità modello
   - Quota equa
   - Probabilità implicita bookmaker
   - Expected Value (EV)

9. DECISION ENGINE:
   🟢 BET: EV chiaramente positivo, MVI elevato, affidabilità adeguata
   🟡 LEAN: Possibile valore ma incertezza significativa
   🔴 PASS: EV insufficiente o affidabilità troppo bassa

FORMATO OUTPUT (RIGOROSO):
Restituisci SEMPRE una tabella Markdown con queste colonne:
| Giocatore | Linea | Quota | Player Quality | Game Quality | Market Quality | Model Reliability | MVI | Prob Modello | Prob Book | Quota Equa | EV | Stake | Verdetto |

Poi un commento tecnico sintetico con:
- Motivo principale della valutazione
- Principale punto di forza
- Principale rischio
- Dati mancanti
- Controlli pre tip-off

RICORDA: Il tuo compito non è indovinare il risultato. Il tuo compito è prendere decisioni migliori del mercato con metodo, disciplina e coerenza.
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
        relevant_keywords = ['out', 'dubbio', 'infortunio', 'riposo', 'rest', 'non giocherà', 'titolare', 'starter', 'probable', 'questionable']
        
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
                            if any(kw in text.lower() for kw in relevant_keywords):
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
    if not groq_client: return "⚠️ Groq API non configurata. Aggiungi GROQ_API_KEY a Render."
    
    news_text = "\n- ".join(player.get('news', [])) if player.get('news') else "Nessuna notizia critica trovata."
    
    prompt = f"""
Analizza questa prop NBA seguendo il protocollo MAYA v5.1:

GIOCATORE: {player['name']}
LINEA: {player['line']}
QUOTA: {player['odds']}

NEWS RECENTI:
- {news_text}

Contesto: NBA Regular Season 2026-27

Fornisci l'analisi completa con tabella e verdetto finale.
"""
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.1-70b-versatile",
            messages=[{"role": "system", "content": MAYA_PROMPT}, {"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=2000
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Errore Groq: {e}")
        return f"❌ Errore AI: {str(e)}"

# ==================== HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = "✅ ATTIVA (Groq AI)" if groq_client else "❌ NON CONFIGURATA"
    await update.message.reply_text(
        f"🏀 *MAYA Framework v5.1 - AI Powered*\n\n"
        f"🤖 *AI Status:* {status}\n\n"
        f" Invia screenshot delle quote Bet365\n"
        f"🧠 L'AI analizzerà con il protocollo MAYA v5.1\n"
        f"📰 News da @nbanews24italia + @InfoBasketNBA",
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
            await update.message.reply_text(f"🧠 Analisi AI di {player['name']} in corso...")
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
    logger.info("🚀 Avvio MAYA AI Bot...")
    logger.info(f"🔑 Telegram: {'OK' if TOKEN else 'MANCANTE'}")
    logger.info(f" Groq AI: {'OK' if groq_client else 'NON CONFIGURATA'}")
    await asyncio.gather(run_bot(), run_server())

if __name__ == '__main__':
    asyncio.run(main())
