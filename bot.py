import os
import logging
import sqlite3
import urllib.request
import json
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
FEEDBACK_CHANNEL_ID = int(os.getenv("FEEDBACK_CHANNEL_ID", "-1003534656490"))

logging.basicConfig(level=logging.INFO)

EXCHANGE_API_KEY = "a248522f81d454d9e7ed2880"

def get_rate(from_currency: str, to_currency: str) -> str:
    """Get exchange rate between two currencies. Returns formatted string."""
    try:
        # Normalize USDT → USD
        fc = "USD" if from_currency == "USDT" else from_currency
        tc = "USD" if to_currency == "USDT" else to_currency

        if fc == tc:
            return None

        url = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_API_KEY}/pair/{fc}/{tc}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())

        if data.get("result") == "success":
            rate = data["conversion_rate"]
            # Format nicely
            if rate >= 1:
                rate_str = f"{rate:.2f}"
            else:
                rate_str = f"{rate:.6f}"
            return f"1 {from_currency} = {rate_str} {to_currency}"
    except Exception as e:
        logging.warning(f"Rate fetch failed: {e}")
    return None


def init_db():
    conn = sqlite3.connect('fifo.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY,
                  username TEXT,
                  first_name TEXT,
                  registered_at TIMESTAMP,
                  last_active TIMESTAMP,
                  total_ads INTEGER DEFAULT 0,
                  rating_total INTEGER DEFAULT 0,
                  rating_count INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS ads
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  username TEXT,
                  from_country TEXT,
                  from_city TEXT,
                  to_country TEXT,
                  to_city TEXT,
                  give_currency TEXT,
                  get_currency TEXT,
                  amount REAL,
                  contact TEXT,
                  duration TEXT,
                  status TEXT DEFAULT 'active',
                  created_at TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS reviews
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  from_user_id INTEGER,
                  to_user_id INTEGER,
                  rating INTEGER,
                  comment TEXT,
                  created_at TIMESTAMP)''')
    conn.commit()
    conn.close()


init_db()

COUNTRIES = {
    "uae": {"name": "🇦🇪 UAE", "cities": ["Dubai", "Abu Dhabi", "Sharjah", "Ajman"]},
    "nigeria": {"name": "🇳🇬 Nigeria", "cities": ["Lagos", "Kano", "Ibadan", "Abuja"]},
}

CURRENCIES = ["AED", "USD", "NGN", "USDT"]
DURATIONS = {"1day": "1 Day", "3days": "3 Days", "1week": "1 Week"}

user_sessions = {}


def get_stars(rating):
    full = int(rating) // 2
    half = 1 if int(rating) % 2 else 0
    empty = 5 - full - half
    return "⭐" * full + "✨" * half + "☆" * empty


async def register_user(update: Update):
    user = update.effective_user
    conn = sqlite3.connect('fifo.db')
    c = conn.cursor()
    c.execute('''INSERT OR IGNORE INTO users
                 (user_id, username, first_name, registered_at, last_active)
                 VALUES (?, ?, ?, ?, ?)''',
              (user.id, user.username, user.first_name, datetime.now(), datetime.now()))
    c.execute('UPDATE users SET last_active = ? WHERE user_id = ?',
              (datetime.now(), user.id))
    conn.commit()
    conn.close()


def get_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 POST AD", callback_data="post_ad")],
        [InlineKeyboardButton("👤 PROFILE", callback_data="profile")],
        [InlineKeyboardButton("📋 MY ADS", callback_data="my_ads")],
        [InlineKeyboardButton("🛡️ SAFETY", callback_data="safety")],
        [InlineKeyboardButton("💬 FEEDBACK", callback_data="feedback")],
        [InlineKeyboardButton("💰 DONATION", callback_data="donation")],
    ])


# ─────────────────────────────────────────────
# START / MENU
# ─────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    await update.message.reply_text(
        "💰 *FIFO.EXCHANGE*\n\nUAE ↔ Nigeria\nFree P2P Exchange",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "💰 *FIFO.EXCHANGE*\n\nUAE ↔ Nigeria\nFree P2P Exchange",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )


# ─────────────────────────────────────────────
# PROFILE  — FIX: works via callback, no context.args
# ─────────────────────────────────────────────

async def show_profile(send_fn, viewer_id: int, target_id: int):
    """Render profile and send via send_fn (accepts text + kwargs)."""
    conn = sqlite3.connect('fifo.db')
    c = conn.cursor()
    c.execute('''SELECT username, first_name, registered_at, total_ads,
                        rating_total, rating_count
                 FROM users WHERE user_id = ?''', (target_id,))
    user = c.fetchone()
    if not user:
        conn.close()
        await send_fn("❌ Profile not found")
        return

    username, first_name, reg_date, total_ads, rating_total, rating_count = user
    reg_str = datetime.fromisoformat(reg_date).strftime('%d %b %Y')
    avg_rating = (rating_total / rating_count) if rating_count > 0 else 0

    c.execute('''SELECT r.rating, r.comment, r.created_at, u.username
                 FROM reviews r
                 JOIN users u ON r.from_user_id = u.user_id
                 WHERE r.to_user_id = ?
                 ORDER BY r.created_at DESC LIMIT 3''', (target_id,))
    reviews = c.fetchall()
    conn.close()

    text = (
        f"👤 *PROFILE*\n\n"
        f"📛 Name: {first_name}\n"
        f"🆔 @{username or 'no username'}\n"
        f"📅 Registered: {reg_str}\n"
        f"📢 Total ads: {total_ads}\n"
        f"⭐ Rating: {get_stars(int(avg_rating))} {avg_rating:.1f}/10 ({rating_count} reviews)\n\n"
        f"📝 *Recent reviews:*"
    )

    if reviews:
        for r in reviews:
            stars = get_stars(r[0])
            date = datetime.fromisoformat(r[2]).strftime('%d %b')
            comment = r[1][:50] + ('...' if len(r[1]) > 50 else '') if r[1] else '—'
            text += f"\n{stars} from @{r[3]} ({date}): {comment}"
    else:
        text += "\nNo reviews yet"

    keyboard = [[InlineKeyboardButton("◀️ Back", callback_data="menu")]]
    if target_id != viewer_id:
        keyboard.insert(0, [InlineKeyboardButton("⭐ Leave Review", callback_data=f"review_{target_id}")])

    await send_fn(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show own profile via button."""
    query = update.callback_query
    await query.answer()
    await show_profile(query.edit_message_text, query.from_user.id, query.from_user.id)


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show own or another user's profile via /profile or /profile @username."""
    viewer_id = update.effective_user.id
    target_id = viewer_id

    if context.args:
        uname = context.args[0].replace("@", "")
        conn = sqlite3.connect('fifo.db')
        c = conn.cursor()
        c.execute("SELECT user_id FROM users WHERE username = ?", (uname,))
        row = c.fetchone()
        conn.close()
        if row:
            target_id = row[0]
        else:
            await update.message.reply_text(f"❌ User @{uname} not found")
            return

    await show_profile(update.message.reply_text, viewer_id, target_id)


# ─────────────────────────────────────────────
# MY ADS  — FIX: use edit_message_text, not reply
# ─────────────────────────────────────────────

async def my_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    conn = sqlite3.connect('fifo.db')
    c = conn.cursor()
    c.execute('''SELECT id, from_country, from_city, to_country, to_city,
                        give_currency, get_currency, amount, duration, status, created_at
                 FROM ads WHERE user_id = ? ORDER BY created_at DESC LIMIT 10''', (user_id,))
    ads = c.fetchall()
    conn.close()

    if not ads:
        await query.edit_message_text(
            "📭 You have no ads yet.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu")]])
        )
        return

    text = "📋 *YOUR ADS*\n\n"
    for ad in ads:
        ad_id, from_c, from_city, to_c, to_city, give_c, get_c, amount, dur, status, created = ad
        date = datetime.fromisoformat(created).strftime('%d %b')
        status_icon = "✅" if status == "active" else "❌"
        text += f"{status_icon} #{ad_id} {from_city}→{to_city} | {amount} {give_c}➔{get_c} | {dur} | {date}\n"

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu")]])
    )


# ─────────────────────────────────────────────
# SAFETY / DONATION / FEEDBACK
# ─────────────────────────────────────────────

async def safety(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "🛡️ *SAFETY RULES*\n\n"
        "• Meet in PUBLIC places\n"
        "• VERIFY government ID\n"
        "• Use VIDEO CALL for cross-city\n"
        "• Bring a FRIEND for large amounts\n"
        "• Trust your gut — WALK AWAY"
    )
    await query.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu")]])
    )


async def donation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "💰 *Support FIFO.EXCHANGE*\n\n"
        "If you find this bot useful, you can donate to help with server costs and development.\n\n"
        "*USDT (TRC20):*\n"
        "`TS8xd6rtwgabfuQhrd1fHFYrojiUVf981u`\n\n"
        "Thank you! 🙏"
    )
    await query.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu")]])
    )


async def feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_sessions[query.from_user.id] = {"step": "feedback"}
    await query.edit_message_text(
        "💬 *Send your feedback, ideas or bug reports*\n\nJust type your message below:",
        parse_mode="Markdown"
    )


async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    username = update.effective_user.username or "no username"
    first_name = update.effective_user.first_name or "User"

    post = (
        f"💬 *NEW FEEDBACK*\n\n"
        f"👤 From: {first_name} (@{username})\n"
        f"🆔 ID: {uid}\n\n"
        f"📝 Message:\n{text}\n\n"
        f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    try:
        await context.bot.send_message(chat_id=FEEDBACK_CHANNEL_ID, text=post, parse_mode="Markdown")
        await update.message.reply_text("✅ Thank you! Your feedback has been sent.", reply_markup=get_main_keyboard())
    except Exception as e:
        await update.message.reply_text(f"❌ Error sending feedback: {e}")
    user_sessions.pop(uid, None)


# ─────────────────────────────────────────────
# REVIEWS
# ─────────────────────────────────────────────

async def start_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    target_user_id = int(query.data.replace("review_", ""))
    user_sessions[query.from_user.id] = {
        "step": "review_rating",
        "target_user_id": target_user_id
    }
    keyboard = []
    row = []
    for i in range(1, 11):
        row.append(InlineKeyboardButton(str(i), callback_data=f"rating_{i}"))
        if len(row) == 5:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    await query.edit_message_text(
        "⭐ *Rate the user from 1 to 10*\n\nChoose a score:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def review_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rating = int(query.data.replace("rating_", ""))
    uid = query.from_user.id
    if uid in user_sessions and user_sessions[uid].get("step") == "review_rating":
        user_sessions[uid]["rating"] = rating
        user_sessions[uid]["step"] = "review_comment"
        await query.edit_message_text(
            f"⭐ Score: {rating}/10\n\n📝 Write a comment (or /skip to skip):",
            parse_mode="Markdown"
        )


async def review_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    if uid not in user_sessions or user_sessions[uid].get("step") != "review_comment":
        return

    comment = "" if text == "/skip" else text
    data = user_sessions[uid]
    target_id = data["target_user_id"]
    rating = data["rating"]

    conn = sqlite3.connect('fifo.db')
    c = conn.cursor()
    c.execute('''INSERT INTO reviews (from_user_id, to_user_id, rating, comment, created_at)
                 VALUES (?, ?, ?, ?, ?)''',
              (uid, target_id, rating, comment, datetime.now()))
    c.execute('SELECT AVG(rating), COUNT(*) FROM reviews WHERE to_user_id = ?', (target_id,))
    avg, count = c.fetchone()
    c.execute('UPDATE users SET rating_total = ?, rating_count = ? WHERE user_id = ?',
              (avg * count, count, target_id))
    conn.commit()
    conn.close()

    await update.message.reply_text("✅ Review submitted!", reply_markup=get_main_keyboard())
    user_sessions.pop(uid, None)


async def skip_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in user_sessions and user_sessions[uid].get("step") == "review_comment":
        await review_comment(update, context)


# ─────────────────────────────────────────────
# MATCH FINDER  — FIX: contact shown only to matched user (future paywall hook)
# ─────────────────────────────────────────────

async def find_matches(ad_id, context):
    conn = sqlite3.connect('fifo.db')
    c = conn.cursor()
    c.execute("SELECT * FROM ads WHERE id = ?", (ad_id,))
    ad = c.fetchone()
    if not ad:
        conn.close()
        return

    (_, user_id, username, from_country, from_city, to_country, to_city,
     give_curr, get_curr, amount, contact, duration, status, created) = ad

    c.execute('''SELECT * FROM ads
                 WHERE id != ? AND status = 'active'
                   AND from_country = ? AND from_city = ?
                   AND to_country = ? AND to_city = ?
                   AND give_currency = ? AND get_currency = ?''',
              (ad_id, to_country, to_city, from_country, from_city, get_curr, give_curr))
    matches = c.fetchall()
    conn.close()

    for match in matches:
        match_id    = match[0]
        match_user  = match[1]
        match_uname = match[2]
        match_contact = match[10]
        match_amount  = match[9]

        percent = int(min(amount, match_amount) / max(amount, match_amount) * 100) \
            if amount > 0 and match_amount > 0 else 0

        # Get exchange rate
        rate_str = get_rate(give_curr, get_curr)
        rate_line = (
            f"\n💱 Ref. rate: {rate_str} (interbank)\n"
            f"⚠️ Street rate may differ — verify on xe.com"
            if rate_str else ""
        )

        # Notify ad poster about the match
        msg1 = (
            f"🎯 *MATCH FOUND!* ({percent}% match)\n\n"
            f"Your ad #{ad_id} ↔ #{match_id}\n\n"
            f"📍 FROM: {match[3]} / {match[4]}\n"
            f"💰 GIVE: {match_amount} {match[7]}\n"
            f"🎯 TO: {match[5]} / {match[6]}\n"
            f"💵 GET: {match[8]}"
            f"{rate_line}\n\n"
            f"👤 @{match_uname}\n"
            f"📞 Contact: {match_contact}"
        )
        try:
            await context.bot.send_message(chat_id=user_id, text=msg1, parse_mode="Markdown")
        except Exception:
            pass

        # Notify matched user
        rate_str2 = get_rate(match[7], match[8])
        rate_line2 = (
            f"\n💱 Ref. rate: {rate_str2} (interbank)\n"
            f"⚠️ Street rate may differ — verify on xe.com"
            if rate_str2 else ""
        )
        msg2 = (
            f"🎯 *MATCH FOUND!* ({percent}% match)\n\n"
            f"Your ad #{match_id} ↔ #{ad_id}\n\n"
            f"📍 FROM: {from_country} / {from_city}\n"
            f"💰 GIVE: {amount} {give_curr}\n"
            f"🎯 TO: {to_country} / {to_city}\n"
            f"💵 GET: {get_curr}"
            f"{rate_line2}\n\n"
            f"👤 @{username}\n"
            f"📞 Contact: {contact}"
        )
        try:
            await context.bot.send_message(chat_id=match_user, text=msg2, parse_mode="Markdown")
        except Exception:
            pass


# ─────────────────────────────────────────────
# POST AD FLOW
# ─────────────────────────────────────────────

async def post_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    user_sessions[uid] = {}
    kb = [[InlineKeyboardButton(c["name"], callback_data=f"from_{code}")] for code, c in COUNTRIES.items()]
    kb.append([InlineKeyboardButton("◀️ Back", callback_data="menu")])
    await query.edit_message_text(
        "📍 *STEP 1/7* — Your country:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def from_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    code = query.data.replace("from_", "")
    user_sessions[uid]["from_country"] = COUNTRIES[code]["name"]
    user_sessions[uid]["from_code"] = code
    kb = [[InlineKeyboardButton(city, callback_data=f"fromcity_{city}")] for city in COUNTRIES[code]["cities"]]
    kb.append([InlineKeyboardButton("◀️ Back", callback_data="post_ad")])
    await query.edit_message_text(
        f"📍 *STEP 2/7* — Your city in {COUNTRIES[code]['name']}:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def from_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    user_sessions[uid]["from_city"] = query.data.replace("fromcity_", "")
    from_code = user_sessions[uid]["from_code"]
    kb = [[InlineKeyboardButton(c["name"], callback_data=f"to_{code}")]
          for code, c in COUNTRIES.items() if code != from_code]
    kb.append([InlineKeyboardButton("◀️ Back", callback_data=f"from_{from_code}")])
    await query.edit_message_text(
        "🎯 *STEP 3/7* — Destination country:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def to_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    code = query.data.replace("to_", "")
    user_sessions[uid]["to_country"] = COUNTRIES[code]["name"]
    user_sessions[uid]["to_code"] = code
    kb = [[InlineKeyboardButton(city, callback_data=f"tocity_{city}")] for city in COUNTRIES[code]["cities"]]
    kb.append([InlineKeyboardButton("◀️ Back", callback_data=f"from_{user_sessions[uid]['from_code']}")])
    await query.edit_message_text(
        f"🎯 *STEP 4/7* — City in {COUNTRIES[code]['name']}:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def to_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    user_sessions[uid]["to_city"] = query.data.replace("tocity_", "")
    kb = [[InlineKeyboardButton(curr, callback_data=f"give_{curr}")] for curr in CURRENCIES]
    kb.append([InlineKeyboardButton("◀️ Back", callback_data=f"to_{user_sessions[uid]['to_code']}")])
    await query.edit_message_text(
        "💰 *STEP 5/7* — Currency you GIVE:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def give_currency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    user_sessions[uid]["give_currency"] = query.data.replace("give_", "")
    kb = [[InlineKeyboardButton(curr, callback_data=f"get_{curr}")] for curr in CURRENCIES]
    kb.append([InlineKeyboardButton("◀️ Back", callback_data=f"to_{user_sessions[uid]['to_code']}")])
    await query.edit_message_text(
        "💵 *STEP 6/7* — Currency you GET:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def get_currency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    user_sessions[uid]["get_currency"] = query.data.replace("get_", "")
    user_sessions[uid]["step"] = "amount"
    await query.edit_message_text(
        "💲 *STEP 7/7* — Enter amount (numbers only, e.g. 1000):",
        parse_mode="Markdown"
    )


async def duration_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    dur_key = query.data.replace("dur_", "")
    user_sessions[uid]["duration"] = DURATIONS[dur_key]
    data = user_sessions[uid]

    preview = (
        f"📋 *PREVIEW*\n\n"
        f"📍 FROM: {data['from_country']} / {data['from_city']}\n"
        f"💰 GIVE: {data['amount']} {data['give_currency']}\n"
        f"🎯 TO: {data['to_country']} / {data['to_city']}\n"
        f"💵 GET: {data['get_currency']}\n"
        f"📞 CONTACT: {data['contact']}\n"
        f"⏱️ DURATION: {data['duration']}\n\n"
        f"✅ *Confirm?*"
    )
    await query.edit_message_text(
        preview, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ PUBLISH", callback_data="publish")]])
    )


async def publish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if uid not in user_sessions:
        await query.edit_message_text("❌ Session expired. Please start over with /start")
        return

    data = user_sessions[uid]

    conn = sqlite3.connect('fifo.db')
    c = conn.cursor()
    c.execute('''INSERT INTO ads
                 (user_id, username, from_country, from_city, to_country, to_city,
                  give_currency, get_currency, amount, contact, duration, status, created_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              (uid, data["username"],
               data["from_country"], data["from_city"],
               data["to_country"], data["to_city"],
               data["give_currency"], data["get_currency"],
               data["amount"], data["contact"],
               data["duration"], 'active', datetime.now()))
    ad_id = c.lastrowid
    c.execute('UPDATE users SET total_ads = total_ads + 1 WHERE user_id = ?', (uid,))
    conn.commit()
    conn.close()

    # Find matching deals
    await find_matches(ad_id, context)

    # Build hashtags
    give_tag = data['give_currency'].lower()
    get_tag  = data['get_currency'].lower()
    from_city_tag = data['from_city'].lower().replace(' ', '')
    to_city_tag   = data['to_city'].lower().replace(' ', '')

    post = (
        f"#{give_tag} #{get_tag} #{from_city_tag} #{to_city_tag} #fifo\n\n"
        f"🔄 *FIFO DEAL #{ad_id}*\n\n"
        f"📍 FROM: {data['from_country']} / {data['from_city']}\n"
        f"💰 GIVE: {data['amount']} {data['give_currency']}\n"
        f"🎯 TO: {data['to_country']} / {data['to_city']}\n"
        f"💵 GET: {data['get_currency']}\n\n"
        f"👤 @{data['username']}\n"
        f"📱 WhatsApp: {data['contact']}\n"
        f"⏱️ {data['duration']}\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Meet in public • Verify ID • Your risk\n"
        f"💰 Post your deal → @fifoexchange_bot\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )

    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=post)
    except Exception as e:
        logging.error(f"Failed to post to channel: {e}")

    await query.edit_message_text(
        f"✅ *AD POSTED!*\n\nID: #{ad_id}\nChannel: @fifoexchange",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Main Menu", callback_data="menu")]])
    )
    user_sessions.pop(uid, None)


# ─────────────────────────────────────────────
# TEXT HANDLER
# ─────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text

    if uid not in user_sessions:
        return

    step = user_sessions[uid].get("step")

    if step == "feedback":
        await handle_feedback(update, context)

    elif step == "review_comment":
        await review_comment(update, context)

    elif step == "amount":
        try:
            amount = float(text.replace(",", "").replace(" ", ""))
            if amount <= 0:
                raise ValueError
            user_sessions[uid]["amount"] = amount
            user_sessions[uid]["step"] = "contact"
            await update.message.reply_text(
                "📞 *WhatsApp number?* (e.g. +971501234567)",
                parse_mode="Markdown"
            )
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid positive number")

    elif step == "contact":
        user_sessions[uid]["contact"] = text
        user_sessions[uid]["username"] = update.effective_user.username or "user"
        user_sessions[uid]["step"] = "duration"
        kb = [[InlineKeyboardButton(dur, callback_data=f"dur_{code}")] for code, dur in DURATIONS.items()]
        await update.message.reply_text(
            "⏱️ *Select duration:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )


# ─────────────────────────────────────────────
# BUTTON ROUTER
# ─────────────────────────────────────────────

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "post_ad":          await post_ad(update, context)
    elif data == "profile":        await profile(update, context)
    elif data == "my_ads":         await my_ads(update, context)
    elif data == "safety":         await safety(update, context)
    elif data == "feedback":       await feedback(update, context)
    elif data == "donation":       await donation(update, context)
    elif data == "menu":           await menu(update, context)
    elif data == "publish":        await publish(update, context)
    elif data.startswith("review_"):    await start_review(update, context)
    elif data.startswith("rating_"):    await review_rating(update, context)
    elif data.startswith("from_"):      await from_country(update, context)
    elif data.startswith("fromcity_"):  await from_city(update, context)
    elif data.startswith("to_"):        await to_country(update, context)
    elif data.startswith("tocity_"):    await to_city(update, context)
    elif data.startswith("give_"):      await give_currency(update, context)
    elif data.startswith("get_"):       await get_currency(update, context)
    elif data.startswith("dur_"):       await duration_handler(update, context)
    else:
        await query.answer("Unknown action", show_alert=False)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("myads", my_ads))
    app.add_handler(CommandHandler("skip", skip_comment))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("🚀 FIFO.EXCHANGE BOT STARTED")
    app.run_polling()


if __name__ == "__main__":
    main()
