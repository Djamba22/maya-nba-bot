import os
import asyncio
import logging
import pytesseract
import re
from PIL import Image
import io
import aiohttp
from bs4 import BeautifulSoup
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

# ==================== DATA AGGREGATOR (Multi-Source) ====================

class DataAggregator:
    def __init__(self, player_name):
        self.player_name = player_name
        self.data = {
            'sofascore': {'ppg': 20.0, 'mpg': 30.0, 'usage': 25.0, 'last_5_ppg': 20.0},
            'telegram_news': [],
            'matchup': {'difficulty': 'Media', 'opp_3pa_allowed': 32.0}
        }
    
    async def fetch_all_data(self):
        tasks = [self.fetch_telegram_news(), self.fetch_sofascore_mock()]
        await asyncio.gather(*tasks, return_exceptions=True)
        return self.data

    async def fetch_telegram_news(self):
        """Scraper dei canali Telegram NBA (versione web pubblica)"""
        channels = [
            "https://t.me/s/nbanews24italia",
            "https://t.me/s/InfoBasketNBA"
        ]
        
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        found_news = []
        
        async with aiohttp.ClientSession() as session:
            for channel_url in channels:
                try:
                    async with session.get(channel_url, headers=headers, timeout=5) as response:
                        html = await response.text()
                    
                    soup = BeautifulSoup(html, 'html.parser')
                    messages = soup.find_all('div', class_='tgme_widget_message_text')
                    
                    # Controlla gli ultimi 5 messaggi di ogni canale
                    for msg in messages[:5]:
                        text = msg.get_text(separator=' ', strip=True)
                        # Cerca il nome o il cognome del giocatore nel messaggio
                        name_parts = self.player_name.lower().split()
                        if len(name_parts) >= 2:
                            if name_parts[0] in text.lower() or name_parts[-1] in text.lower():
                                found_news.append(f"[{channel_url.split('/')[-1]}] {text}")
                                
                except Exception as e:
                    logger.error(f"❌ Errore scraping {channel_url}: {e}")
                    
        self.data['telegram_news'] = found_news
        logger.info(f"✅ Telegram news controllate per {self.player_name}")

    async def fetch_sofascore_mock(self):
        """Simulazione dati Sofascore (integrare API reale in produzione)"""
        pass

# ==================== MAYA ENGINE ====================

def parse_betting_data(text):
    """Estrae TUTTI i giocatori da una tabella OCR"""
    players = []
    lines = text.split('\n')
    for line in lines:
        player_match = re.search(r'([A-Z][a-z]+\s[A-Z][a-z]+)', line)
        line_match = re.search(r'(\d+\.?\d*)', line)
        odds_match = re.search(r'(\d+\.\d{2})', line)
        
        if player_match and line_match and odds_match:
            players.append({
                'name': player_match.group(0),
                'line': line_match.group(0),
                'odds': odds_match.group(0)
            })
    return players if players else [{'name': "Non riconosciuto", 'line': "N/A", 'odds': "N/A"}]

def calculate_maya_score(player_data, aggregator_data):
    name = player_data['name']
    line = float(player_data['line']) if player_data['line'] != "N/A" else 20.0
    odds = float(player_data['odds']) if player_data['odds'] != "N/A" else 2.00
    
    stats = aggregator_data.get('sofascore', {})
    news = aggregator_data.get('telegram_news', [])
    
    # Controllo infortuni/news negative dai canali Telegram
    news_penalty = 1.0
    for n in news:
        if any(word in n.lower() for word in ['out', 'dubbio', 'infortunio', 'riposo', 'rest', 'non giocherà']):
            news_penalty = 0.4  # Penalizzazione forte se ci sono notizie negative
            
    avg_ppg = stats.get('ppg', 20.0)
    prob_model = min(0.95, max(0.05, (avg_ppg / line) * news_penalty * 0.6))
    
    prob_book = 1 / odds if odds > 0 else 0
    ev = (prob_model * (odds - 1)) - (1 - prob_model) if odds > 0 else -1
    mvi = min(100, max(0, (ev * 100) + 50 + (prob_model * 20)))
    
    verdict = "🟢 BET" if ev > 0.05 else ("🟡 LEAN" if ev > 0 else "🔴 PASS")
    
    # Sintesi news per il report
    if news:
        news_summary = "⚠️ ATTN:\n• " + "\n• ".join(news[:2])
    else:
        news_summary = "✅ Nessuna notizia critica recente sui canali monitorati."
    
    return {
        'name': name, 'line': player_data['line'], 'odds': player_data['odds'],
        'prob_model': prob_model, 'prob_book': prob_book, 'ev': ev, 'mvi': mvi,
        'verdict': verdict, 'fair_odds': 1/prob_model if prob_model > 0 else 0,
        'news_summary': news_summary
    }

def generate_maya_report(analysis):
    return f"""
🧠 *MAYA FRAMEWORK v5.1 - Multi-Source*

👤 *Giocatore:* {analysis['name']}
📊 *Linea:* {analysis['line']} | 💰 *Quota:* {analysis['odds']}

━━━━━━━━━━━━━━━━━━━━
📰 *NEWS & CONTEXT*
{analysis['news_summary']}

📊 *PROBABILITY ENGINE*
• Prob. Modello: {analysis['prob_model']:.1%}
• Prob. Book: {analysis['prob_book']:.1%}
• Quota Equa: {analysis['fair_odds']:.2f}
• *Expected Value:* {analysis['ev']:.2%}
• *Maya Value Index:* {analysis['mvi']:.0f}/100

🎯 *VERDETTO:* {analysis['verdict']}
    """

async def analyze_multiple_players(players_data):
    results = []
    for player in players_data:
        aggregator = DataAggregator(player['name'])
        data = await aggregator.fetch_all_data()
        analysis = calculate_maya_score(player, data)
        results.append(analysis)
    
    results.sort(key=lambda x: x['mvi'], reverse=True)
    return results

# ==================== HANDLER TELEGRAM ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏀 *MAYA Framework v5.1 - Multi-Source Edition*\n\n"
        "📸 *Funzionalità:*\n"
        "• Invia screenshot di SINGOLA prop\n"
        "• Invia screenshot di TABELLA completa (multi-giocatore)\n"
        "• /analizza Nome Linea Quota\n\n"
        "📰 *Fonti News:* @nbanews24italia + @InfoBasketNBA (real-time)",
        parse_mode='Markdown'
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Analisi screenshot in corso... Attendi 15 secondi.")
    try:
        photo = await update.message.photo[-1].get_file()
        photo_bytes = await photo.download_as_bytearray()
        image = Image.open(io.BytesIO(photo_bytes))
        text = pytesseract.image_to_string(image, lang='ita+eng')
        
        players = parse_betting_data(text)
        
        if len(players) == 1:
            await update.message.reply_text(f"📊 Rilevata 1 prop: {players[0]['name']}")
            aggregator = DataAggregator(players[0]['name'])
            data = await aggregator.fetch_all_data()
            analysis = calculate_maya_score(players[0], data)
            await update.message.reply_text(generate_maya_report(analysis), parse_mode='Markdown')
        else:
            await update.message.reply_text(f"📊 Rilevate {len(players)} props. Analisi multi-source in corso...")
            results = await analyze_multiple_players(players)
            
            top3_text = "🏆 *TOP 3 MAYA VALUE*\n\n"
            for i, res in enumerate(results[:3], 1):
                top3_text += f"{i}. {res['name']} | Line: {res['line']} @ {res['odds']}\n"
                top3_text += f"   MVI: {res['mvi']:.0f} | EV: {res['ev']:.2%} {res['verdict']}\n\n"
            
            await update.message.reply_text(top3_text, parse_mode='Markdown')
            
            if results:
                await update.message.reply_text(f"🎯 *MIGLIORE PROP CONSIGLIATA:*\n\n" + generate_maya_report(results[0]), parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Errore analisi: {str(e)}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text.startswith("/analizza"):
        parts = text.split()
        if len(parts) >= 4:
            odds, line, player = parts[-1], parts[-2], " ".join(parts[1:-2])
            await update.message.reply_text(f"🔍 Analisi {player} in corso...")
            aggregator = DataAggregator(player)
            data = await aggregator.fetch_all_data()
            analysis = calculate_maya_score({'name': player, 'line': line, 'odds': odds}, data)
            await update.message.reply_text(generate_maya_report(analysis), parse_mode='Markdown')
        else:
            await update.message.reply_text("❗ Formato: /analizza Nome Linea Quota")
    else:
        await update.message.reply_text("👋 Invia screenshot o usa /analizza Nome Linea Quota")

# ==================== SERVER HTTP & BOT ====================

async def health_check(request):
    return aiohttp.web.Response(text="MAYA Bot v5.1 Multi-Source is running 🏀")

async def run_server():
    app = aiohttp.web.Application()
    app.router.add_get('/', health_check)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"🌐 Server HTTP attivo su porta {PORT}")
    while True:
        await asyncio.sleep(3600)

async def run_bot():
    if not TOKEN:
        logger.error("❌ TOKEN mancante!")
        return
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("analizza", handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    logger.info("✅ Bot handlers configurati")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    logger.info("🤖 Bot Telegram attivo con polling")
    
    while True:
        await asyncio.sleep(3600)

async def main():
    logger.info("🚀 Avvio MAYA Bot v5.1 Multi-Source...")
    logger.info(f"🔑 TOKEN: {'OK' if TOKEN else 'MANCANTE'}")
    logger.info(f"📡 PORT: {PORT}")
    await asyncio.gather(run_bot(), run_server())

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot fermato")
    except Exception as e:
        logger.error(f"Errore fatale: {e}")
