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

# ==================== SYSTEM PROMPT MAYA v5.1 (RIGOROSO) ====================
MAYA_SYSTEM_PROMPT = """
Sei MAYA (Market Analysis & Yield Assessment), un analista quantitativo professionista specializzato in Player Props NBA (specificamente Mercato Triple).

IL TUO OBIETTIVO:
Stimare la probabilità reale che un giocatore faccia Over 2 o Over 3 triple e confrontarla con la quota del bookmaker per individuare ESCLUSIVAMENTE Expected Value (EV) positivo.

REGOLE FONDAMENTALI (DA RISPETTARE SEMPRE):
1. Non prevedere il risultato della partita. Cerca SOLO valore matematico.
2. Non usare la fama del giocatore. Usa solo dati, volume e contesto.
3. Ogni dato modifica la probabilità, quasi nessun dato la determina da solo.
4. Non contare mai due volte la stessa informazione.
5. Non forzare mai una giocata. Se non c'è valore, il verdetto è PASS.

PROTOCOLLO DI ANALISI (Segui questo ordine mentale):
1. SMART SCREENER: Quota < 1.40 = PASS automatico (nessun valore).
2. PLAYER PROFILE & VOLUME: Quanti tiri da 3 fa a partita? (3PA). È un tiratore puro o di volume?
3. TREND ENGINE: Come ha tirato nelle ultime 5 partite? (Hot/Cold streak).
4. MATCHUP & CONTEXT: La difesa avversaria concede molte triple? C'è back-to-back? Infortuni ai playmaker che aumentano il suo volume?
5. NEWS: Ci sono notizie di infortunio, dubbio o rest? (Se sì, penalizza pesantemente).

FORMATO OUTPUT (Rispondi SEMPRE in Italiano, usando questo schema esatto):

🧠 *ANALISI MAYA v5.1*
 *Giocatore:* [Nome]
 *Mercato:* [Over 2 o Over 3 Triple]
💰 *Quota:* [Quota]

📊 *DATI CHIAVE:*
• Volume 3PA: [Stima tiri a partita]
• Trend recente: [Analisi ultime partite]
• Matchup: [Analisi difesa avversaria]

📰 *NEWS & CONTEXT:*
[Inserisci qui le news trovate o "Nessuna news critica"]

📈 *VALUTAZIONE MAYA:*
• Probabilità Reale Stimata: [X]%
• Probabilità Implicita (Book): [Y]%
• Expected Value (EV): [Z]%

🎯 *VERDETTO FINALE:* [🟢 BET / 🟡 LEAN / 🔴 PASS]
 *Motivo:* [Spiegazione sintetica e tecnica del perché]

IMPORTANTE: Non usare asterischi doppi (**) o underscore (_) per il grassetto/corsivo perché rompono Telegram. Usa solo il testo normale e le emoji.
"""

# ==================== DATA AGGREGATOR (NEWS) ====================
class DataAggregator:
    def __init__(self, player_name):
        self.player_name = player_name
        self.news = []
    
    async def fetch_telegram_news(self):
        channels = ["https://t.me/s/nbanews24italia", "https://t.me/s/InfoBasketNBA"]
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        
        spam_keywords = ['fantabasket', 'lega', 'registrati', 'codice', 'dunk', 'promo', 'vota', 'sondaggio', 'buona fortuna', 'link', 'clicca']
        relevant_keywords = ['out', 'dubbio', 'infortunio', 'riposo', 'rest', 'non giocherà', 'titolare', 'starter', 'probable', 'questionable', 'minuti', 'limitazione']
        
        async with aiohttp.ClientSession() as session:
            for url in channels:
                try:
                    async with session.get(url, headers=headers, timeout=5) as resp:
                        html = await resp.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    for msg in soup.find_all('div', class_='tgme_widget_message_text')[:20]:
                        text = msg.get_text(separator=' ', strip=True)
                        if any(s in text.lower() for s in spam_keywords): continue
                        
                        # Cerca il nome del giocatore nel messaggio
                        name_parts = self.player_name.lower().split()
                        if len(name_parts) >= 2:
                            if name_parts[0] in text.lower() or name_parts[-1] in text.lower():
                                # Se il messaggio contiene parole chiave rilevanti
                                if any(kw in text.lower() for kw in relevant_keywords):
                                    self.news.append(text[:150])
                except Exception as e:
                    pass # Silenzia errori di rete per non bloccare il bot

# ==================== OCR ENGINE (PARSER TABELLE BET365) ====================
def parse_betting_table(text):
    """
    Legge tabelle strutturate come:
    Nome Giocatore | Quota Colonna 1 (Over 2) | Quota Colonna 2 (Over 3)
    Es: Luka Doncic 1.09 1.33
    """
    players = []
    lines = text.split('\n')
    
    # Pattern: Nome (2+ parole maiuscole/minuscole) seguito da due numeri decimali
    pattern = re.compile(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s+(\d+\.\d{2})\s+(\d+\.\d{2})')
    
    for line in lines:
        match = pattern.search(line.strip())
        if match:
            name = match.group(1)
            odds_col2 = match.group(2) # Over 2 Triple
            odds_col3 = match.group(3) # Over 3 Triple
            
            # Aggiungiamo entrambe le opzioni per lo stesso giocatore
            players.append({'name': name, 'market': 'Over 2 Triple', 'odds': odds_col2})
            players.append({'name': name, 'market': 'Over 3 Triple', 'odds': odds_col3})
            
    # Rimuovi duplicati se l'OCR legge male
    unique_players = []
    seen = set()
    for p in players:
        key = (p['name'], p['market'])
        if key not in seen:
            seen.add(key)
            unique_players.append(p)
            
    return unique_players

# ==================== AI ANALYSIS ENGINE ====================
async def analyze_with_ai(player):
    if not groq_client:
        return "⚠️ Groq AI non configurata. Contatta l'admin."
    
    news_text = "\n- ".join(player.get('news', [])) if player.get('news') else "Nessuna news critica trovata sui canali monitorati."
    
    prompt = f"""
Analizza questa prop NBA seguendo rigorosamente il protocollo MAYA v5.1:

GIOCATORE: {player['name']}
MERCATO: {player['market']}
QUOTA BOOKMAKER: {player['odds']}

NEWS E CONTESTO RECENTE:
- {news_text}

Applica il tuo ragionamento quantitativo e restituisci il report formattato.
"""
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.1-70b-versatile", # Modello potente e gratuito
            messages=[
                {"role": "system", "content": MAYA_SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2, # Basso per analisi deterministiche
            max_tokens=1200
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Errore Groq: {e}")
        return f"❌ Errore AI: {str(e)}"

# ==================== HANDLERS TELEGRAM ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = "✅ ATTIVA (Groq AI - Llama 3.1 70B)" if groq_client else "❌ NON CONFIGURATA"
    await update.message.reply_text(
        f"🏀 MAYA Framework v5.1 - AI Analyzer\n\n"
        f" Stato AI: {status}\n\n"
        f"📸 Come usare il bot:\n"
        f"1. Fai uno screenshot della tabella quote (Bet365 o simili).\n"
        f"2. Il bot leggerà TUTTI i giocatori e le colonne (Over 2 e Over 3 triple).\n"
        f"3. L'AI applicherà il protocollo MAYA v5.1 per trovare le value bet.\n\n"
        f"📰 Fonti News: @nbanews24italia + @InfoBasketNBA"
    )

async def safe_send_message(update, text):
    """Invia messaggio gestendo errori di formattazione Telegram"""
    try:
        await update.message.reply_text(text, parse_mode='Markdown')
    except Exception:
        try:
            # Fallback: rimuove caratteri problematici e invia testo puro
            clean_text = text.replace('*', '').replace('_', '').replace('`', '')
            await update.message.reply_text(clean_text)
        except Exception:
            await update.message.reply_text("Messaggio ricevuto ma formattazione complessa.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(" Lettura tabella in corso... Attendi 20-40 secondi.")
    
    try:
        photo = await update.message.photo[-1].get_file()
        photo_bytes = await photo.download_as_bytearray()
        image = Image.open(io.BytesIO(photo_bytes))
        
        # OCR
        text = pytesseract.image_to_string(image, lang='ita+eng')
        players = parse_betting_table(text)
        
        if not players:
            await update.message.reply_text("❌ Non ho trovato giocatori con quote nella tabella. Assicurati che lo screenshot sia nitido e mostri i nomi completi.")
            return
        
        await update.message.reply_text(f"📊 Trovati {len(players)} mercati. Analisi AI MAYA in corso...")
        
        # Analizza i primi 6 mercati per non saturare l'AI e rispettare i limiti
        # (Puoi aumentare a 10 se vuoi, ma richiede più tempo)
        for p in players[:6]:
            aggregator = DataAggregator(p['name'])
            await aggregator.fetch_telegram_news()
            p['news'] = aggregator.news
            
            analysis = await analyze_with_ai(p)
            await safe_send_message(update, analysis)
            await asyncio.sleep(1) # Pausa per non floodare Telegram
            
    except Exception as e:
        await update.message.reply_text(f"❌ Errore sistema: {str(e)}")
        logger.error(f"Errore handle_photo: {e}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.startswith("/analizza"):
        parts = update.message.text.split()
        if len(parts) >= 4:
            player = {'name': " ".join(parts[1:-2]), 'market': 'Custom', 'odds': parts[-1]}
            await update.message.reply_text(f"🧠 Analisi AI di {player['name']} in corso...")
            aggregator = DataAggregator(player['name'])
            await aggregator.fetch_telegram_news()
            player['news'] = aggregator.news
            report = await analyze_with_ai(player)
            await safe_send_message(update, report)
        else:
            await update.message.reply_text("❗ Formato: /analizza Nome Giocatore Quota")
    else:
        await update.message.reply_text(" Invia uno screenshot di una tabella quote.")

# ==================== SERVER HTTP & BOT (ASYNCIO PURO) ====================
async def health_check(request):
    return web.Response(text="MAYA AI Bot v5.1 is running 🏀")

async def run_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"🌐 Server HTTP attivo su porta {PORT}")
    while True: await asyncio.sleep(3600)

async def run_bot():
    if not TOKEN:
        logger.error("❌ TOKEN Telegram mancante!")
        return
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("analizza", handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    logger.info("🤖 Bot Telegram attivo con polling")
    while True: await asyncio.sleep(3600)

async def main():
    logger.info("🚀 Avvio MAYA AI Bot v5.1...")
    logger.info(f"🔑 Telegram: {'OK' if TOKEN else 'MANCANTE'}")
    logger.info(f"🤖 Groq AI: {'OK' if groq_client else 'NON CONFIGURATA'}")
    # Avvia bot e server in parallelo
    await asyncio.gather(run_bot(), run_server())

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot fermato manualmente")
    except Exception as e:
        logger.error(f"Errore fatale: {e}")
