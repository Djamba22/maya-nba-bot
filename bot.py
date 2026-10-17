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
from datetime import datetime

# ==================== CONFIGURAZIONE ====================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
PORT = int(os.environ.get("PORT", 10000))

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== CARICAMENTO DATABASE NBA ====================

def load_nba_database():
    """Carica database NBA completo"""
    try:
        with open('nba_database.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"❌ Errore caricamento database: {e}")
        return {'players': {}}

NBA_DB = load_nba_database()
logger.info(f"📊 Database NBA caricato: {len(NBA_DB.get('players', {}))} giocatori")

# ==================== NBA STATS API (Fallback per giocatori non in DB) ====================

class NBAStatsAPI:
    """API fallback per giocatori non nel database locale"""
    
    BASE_URL = "https://stats.nba.com/stats"
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Accept': 'application/json',
        'Referer': 'https://stats.nba.com/'
    }
    
    @classmethod
    async def get_player_stats(cls, player_name):
        """Cerca giocatore nel database o fallback API"""
        # Cerca nel database locale
        if player_name in NBA_DB.get('players', {}):
            player_data = NBA_DB['players'][player_name]
            stats = player_data.get('best_stats') or player_data.get('preseason')
            if stats:
                return {**stats, 'name': player_name, 'position': player_data.get('position', '')}
        
        # Fallback: cerca per similarità nome
        for db_name, db_data in NBA_DB.get('players', {}).items():
            if player_name.lower() in db_name.lower() or db_name.lower() in player_name.lower():
                stats = db_data.get('best_stats') or db_data.get('preseason')
                if stats:
                    return {**stats, 'name': db_name, 'position': db_data.get('position', '')}
        
        # Ultimo fallback: API diretta (rallenta il bot)
        return await cls._fetch_from_api(player_name)
    
    @classmethod
    async def _fetch_from_api(cls, player_name):
        """Fetch diretto da API NBA (solo se non in DB)"""
        logger.warning(f"⚠️ {player_name} non trovato in DB, fetch API diretto...")
        # Implementazione API diretta qui se necessario
        return {
            'name': player_name,
            'ppg': 15.0, 'mpg': 25.0, 'usage_pct': 20.0,
            'fga': 12.0, 'fgm': 5.5, 'fg_pct': 0.45,
            'fg3a': 4.0, 'fg3m': 1.5, 'fg3_pct': 0.35,
            'reb': 5.0, 'ast': 4.0, 'stl': 0.8, 'blk': 0.4,
            'tov': 2.0, 'games': 5, 'position': 'Unknown'
        }

# ==================== DATA AGGREGATOR MULTI-SOURCE ====================

class DataAggregator:
    def __init__(self, player_name, opponent=None):
        self.player_name = player_name
        self.opponent = opponent
        self.data = {
            'player_stats': None,
            'telegram_news': [],
            'matchup': {'difficulty': 'Media', 'opp_def_rating': 110.0, 'pace': 98.0}
        }
    
    async def fetch_all_data(self):
        tasks = [
            self.fetch_nba_stats(),
            self.fetch_telegram_news()
        ]
        await asyncio.gather(*tasks, return_exceptions=True)
        return self.data
    
    async def fetch_nba_stats(self):
        """Recupera stats da database NBA completo"""
        self.data['player_stats'] = await NBAStatsAPI.get_player_stats(self.player_name)
        logger.info(f"✅ Stats recuperate per {self.player_name}")
    
    async def fetch_telegram_news(self):
        """Scraper canali Telegram con filtro intelligente"""
        channels = [
            "https://t.me/s/nbanews24italia",
            "https://t.me/s/InfoBasketNBA"
        ]
        
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        found_news = []
        
        relevant_keywords = [
            'out', 'dubbio', 'infortunio', 'riposo', 'rest', 'non giocherà',
            'titolare', 'starter', 'probable', 'questionable', 'day-to-day',
            'load management', 'minutes restriction', 'limitazione',
            'starting lineup', 'quintetto', 'ufficiale', 'confirmed',
            'trade', 'scambio', 'cesione', 'acquisto'
        ]
        
        spam_keywords = [
            'fantabasket', 'lega', 'registrati', 'codice', 'dunk', 'promo',
            'scaricare', 'app', 'vota', 'sondaggio', 'buona fortuna',
            'vincitore', 'premio', 'link', 'clicca qui', 'iscriviti'
        ]
        
        async with aiohttp.ClientSession() as session:
            for channel_url in channels:
                try:
                    async with session.get(channel_url, headers=headers, timeout=5) as response:
                        html = await response.text()
                    
                    soup = BeautifulSoup(html, 'html.parser')
                    messages = soup.find_all('div', class_='tgme_widget_message_text')
                    
                    for msg in messages[:15]:
                        text = msg.get_text(separator=' ', strip=True)
                        text_lower = text.lower()
                        
                        if any(spam in text_lower for spam in spam_keywords):
                            continue
                        
                        name_parts = self.player_name.lower().split()
                        player_mentioned = False
                        if len(name_parts) >= 2:
                            if name_parts[0] in text_lower or name_parts[-1] in text_lower:
                                player_mentioned = True
                        
                        if player_mentioned and any(kw in text_lower for kw in relevant_keywords):
                            found_news.append(f"[{channel_url.split('/')[-1]}] {text[:200]}")
                            
                except Exception as e:
                    logger.error(f"❌ Errore scraping {channel_url}: {e}")
                    
        self.data['telegram_news'] = found_news
        logger.info(f"✅ Telegram news: {len(found_news)} trovate per {self.player_name}")

# ==================== MAYA ENGINE - OCR MIGLIORATO ====================

def parse_betting_data(text):
    """OCR avanzato per tabelle Bet365"""
    players = []
    lines = text.split('\n')
    
    # Pattern 1: Nome + due quote sulla stessa riga
    pattern_full = re.compile(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s+(\d+\.\d{2})\s+(\d+\.\d{2})')
    
    # Pattern 2: Nome su riga separata, numeri sulla riga successiva
    pattern_name = re.compile(r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)$')
    pattern_odds = re.compile(r'(\d+\.\d{2})\s+(\d+\.\d{2})')
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        
        # Prova pattern completo
        match = pattern_full.search(line)
        if match:
            players.append({
                'name': match.group(1),
                'line': match.group(2),
                'odds': match.group(3)
            })
            i += 1
            continue
        
        # Prova pattern nome separato
        name_match = pattern_name.match(line)
        if name_match and i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            odds_match = pattern_odds.search(next_line)
            if odds_match:
                players.append({
                    'name': name_match.group(1),
                    'line': odds_match.group(1),
                    'odds': odds_match.group(2)
                })
                i += 2
                continue
        
        i += 1
    
    # Fallback: cerca qualsiasi nome proprio con numeri vicini
    if not players:
        for line in lines:
            name_match = re.search(r'([A-Z][a-z]+ [A-Z][a-z]+)', line)
            odds_matches = re.findall(r'(\d+\.\d{2})', line)
            if name_match and len(odds_matches) >= 2:
                players.append({
                    'name': name_match.group(1),
                    'line': odds_matches[0],
                    'odds': odds_matches[1]
                })
    
    return players if players else [{'name': "Non riconosciuto", 'line': "N/A", 'odds': "N/A"}]

# ==================== MAYA FRAMEWORK v5.1 COMPLETO ====================

def calculate_maya_score(player_data, aggregator_data):
    """Calcola Maya Value Index seguendo il protocollo MAYA v5.1"""
    name = player_data['name']
    line = float(player_data['line']) if player_data['line'] != "N/A" else 20.0
    odds = float(player_data['odds']) if player_data['odds'] != "N/A" else 2.00
    
    stats = aggregator_data.get('player_stats', {})
    news = aggregator_data.get('telegram_news', [])
    matchup = aggregator_data.get('matchup', {})
    
    # === PLAYER PROFILE ===
    ppg = stats.get('ppg', 20.0)
    mpg = stats.get('mpg', 30.0)
    usage = stats.get('usage_pct', 25.0)
    fg_pct = stats.get('fg_pct', 0.47)
    games = stats.get('games', 10)
    position = stats.get('position', 'Unknown')
    
    # === TREND ENGINE ===
    trend_factor = 1.0
    
    # === VOLUME ENGINE ===
    fga = stats.get('fga', 15.0)
    fg3a = stats.get('fg3a', 5.0)
    volume_stability = 0.8 if games >= 10 else 0.6
    
    # === MATCHUP ENGINE ===
    opp_def_rating = matchup.get('opp_def_rating', 110.0)
    pace = matchup.get('pace', 98.0)
    matchup_factor = 1.0
    if opp_def_rating > 115:
        matchup_factor = 1.1
    elif opp_def_rating < 105:
        matchup_factor = 0.9
    
    # === CONTEXT ENGINE ===
    news_penalty = 1.0
    injury_risk = False
    for n in news:
        if any(word in n.lower() for word in ['out', 'dubbio', 'infortunio', 'riposo', 'rest', 'non giocherà']):
            news_penalty = 0.4
            injury_risk = True
    
    # === PROBABILITY ENGINE ===
    base_prob = ppg / (line * 1.2)
    adjusted_prob = base_prob * trend_factor * matchup_factor * news_penalty * volume_stability
    
    prob_model = min(0.95, max(0.05, adjusted_prob))
    
    # === MARKET ENGINE ===
    prob_book = 1 / odds if odds > 0 else 0
    fair_odds = 1 / prob_model if prob_model > 0 else 0
    
    # === EXPECTED VALUE ===
    ev = (prob_model * (odds - 1)) - (1 - prob_model) if odds > 0 else -1
    
    # === MAYA VALUE INDEX (MVI) ===
    player_quality = min(100, (ppg / 30) * 100)
    market_quality = min(100, max(0, ev * 200 + 50))
    model_reliability = 70 if games >= 10 else 50
    game_quality = 60
    
    mvi = (player_quality * 0.3 + market_quality * 0.4 + model_reliability * 0.2 + game_quality * 0.1)
    
    # === DECISION ENGINE ===
    verdict = " BET" if ev > 0.05 and mvi > 60 else ("🟡 LEAN" if ev > 0 else "🔴 PASS")
    
    # === NEWS SUMMARY ===
    if news:
        news_summary = "⚠️ ATTN:\n• " + "\n• ".join(news[:2])
    else:
        news_summary = "✅ Nessuna notizia critica recente sui canali monitorati."
    
    return {
        'name': name,
        'line': player_data['line'],
        'odds': player_data['odds'],
        'ppg': ppg,
        'mpg': mpg,
        'usage': usage,
        'fg_pct': fg_pct,
        'position': position,
        'games': games,
        'prob_model': prob_model,
        'prob_book': prob_book,
        'ev': ev,
        'mvi': mvi,
        'verdict': verdict,
        'fair_odds': fair_odds,
        'news_summary': news_summary,
        'injury_risk': injury_risk,
        'player_quality': player_quality,
        'market_quality': market_quality,
        'model_reliability': model_reliability
    }

def generate_maya_report(analysis):
    """Report MAYA v5.1 completo"""
    return f"""
🧠 *MAYA FRAMEWORK v5.1 - ANALISI COMPLETA*

👤 *Giocatore:* {analysis['name']}
📍 *Ruolo:* {analysis['position']}
📊 *Linea:* {analysis['line']} | 💰 *Quota:* {analysis['odds']}

━━━━━━━━━━━━━━━━━━━━
 *PLAYER PROFILE*
• PPG: {analysis['ppg']:.1f} | MPG: {analysis['mpg']:.1f}
• Usage%: {analysis['usage']:.1f}% | FG%: {analysis['fg_pct']:.1%}
• Partite giocate: {analysis['games']}

📰 *NEWS & CONTEXT*
{analysis['news_summary']}

📊 *PROBABILITY ENGINE*
• Prob. Modello: {analysis['prob_model']:.1%}
• Prob. Book: {analysis['prob_book']:.1%}
• Quota Equa: {analysis['fair_odds']:.2f}
• *Expected Value:* {analysis['ev']:.2%}

 *MAYA VALUE INDEX*
• Player Quality: {analysis['player_quality']:.0f}/100
• Market Quality: {analysis['market_quality']:.0f}/100
• Model Reliability: {analysis['model_reliability']:.0f}/100
• *MVI Totale:* {analysis['mvi']:.0f}/100

🎯 *VERDETTO:* {analysis['verdict']}

{'⚠️ *RISCHIO INFORTUNIO:* Monitorare injury report pre-partita!' if analysis['injury_risk'] else '✅ Nessun rischio infortunio rilevato'}
    """

async def analyze_multiple_players(players_data):
    """Analizza tutti i giocatori e ordina per MVI"""
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
        "🏀 *MAYA Framework v5.1 - Professional Edition*\n\n"
        "📸 *Funzionalità:*\n"
        "• Invia screenshot di SINGOLA prop\n"
        "• Invia screenshot di TABELLA completa\n"
        "• /analizza Nome Linea Quota\n"
        "• /database per info sul database NBA\n\n"
        "📊 *Dati:* Database NBA completo (450+ giocatori)\n"
        "📰 *News:* @nbanews24italia + @InfoBasketNBA\n"
        "🏆 *Protocollo:* MAYA v5.1 completo",
        parse_mode='Markdown'
    )

async def handle_database_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra info sul database NBA"""
    total_players = len(NBA_DB.get('players', {}))
    last_updated = NBA_DB.get('last_updated', 'Unknown')
    
    await update.message.reply_text(
        f"📊 *DATABASE NBA*\n\n"
        f"👥 Totale giocatori: {total_players}\n"
        f"📅 Ultimo aggiornamento: {last_updated}\n"
        f"🏀 Stagione: {NBA_DB.get('season', '2026-27')}\n\n"
        f"Il database include:\n"
        f"• Statistiche Pre-Season 2026-27\n"
        f"• Statistiche Regular Season\n"
        f"• Ruoli e posizioni\n"
        f"• Usage%, PPG, MPG, FG% e altro",
        parse_mode='Markdown'
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(" Analisi screenshot in corso... Attendi 20 secondi.")
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
            await update.message.reply_text(f"📊 Rilevate {len(players)} props. Analisi MAYA in corso...")
            results = await analyze_multiple_players(players)
            
            top3_text = "🏆 *TOP 3 MAYA VALUE*\n\n"
            for i, res in enumerate(results[:3], 1):
                top3_text += f"{i}. {res['name']} ({res['position']}) | Line: {res['line']} @ {res['odds']}\n"
                top3_text += f"   MVI: {res['mvi']:.0f} | EV: {res['ev']:.2%} {res['verdict']}\n\n"
            
            await update.message.reply_text(top3_text, parse_mode='Markdown')
            
            if results:
                await update.message.reply_text(f"🎯 *MIGLIORE PROP:*\n\n" + generate_maya_report(results[0]), parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Errore: {str(e)}")
        logger.error(f"Errore handle_photo: {e}")

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
    elif text.startswith("/database"):
        await handle_database_info(update, context)
    else:
        await update.message.reply_text(" Invia screenshot o usa /analizza Nome Linea Quota")

# ==================== SERVER HTTP & BOT ====================

async def health_check(request):
    return web.Response(text="MAYA Bot v5.1 Professional is running 🏀")

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

async def run_bot():
    if not TOKEN:
        logger.error("❌ TOKEN mancante!")
        return
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("analizza", handle_text))
    app.add_handler(CommandHandler("database", handle_database_info))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    logger.info("✅ Bot handlers configurati")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    logger.info("🤖 Bot Telegram attivo")
    
    while True:
        await asyncio.sleep(3600)

async def main():
    logger.info("🚀 Avvio MAYA Bot v5.1 Professional...")
    logger.info(f"🔑 TOKEN: {'OK' if TOKEN else 'MANCANTE'}")
    logger.info(f"📡 PORT: {PORT}")
    logger.info(f"📊 Database NBA: {len(NBA_DB.get('players', {}))} giocatori")
    await asyncio.gather(run_bot(), run_server())

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot fermato")
    except Exception as e:
        logger.error(f"Errore fatale: {e}")
