import os
import logging
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
FEEDBACK_CHANNEL_ID = -1003534656490

logging.basicConfig(level=logging.INFO)

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
    "uae":     {"name": "🇦🇪 UAE",     "cities": ["Dubai", "Abu Dhabi", "Sharjah", "Ajman"]},
    "nigeria": {"name": "🇳🇬 Nigeria", "cities": ["Lagos", "Kano", "Ibadan", "Abuja"]},
}

CURRENCIES = ["AED", "USD", "NGN", "USDT"]
DURATIONS  = {"1day": "1 Day", "3days": "3 Days", "1week": "1 Week"}

user_sessions = {}

# ─── helpers ──────────────────────────────────────────────────────────────────

def get_stars(rating):
    full  = rating // 2
    half  = 1 if rating % 2 else 0
    empty = 5 - full - half
    return "⭐" * full + "✨" * half + "☆" * empty

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 POST AD",   callback_data="post_ad")],
        [InlineKeyboardButton("👤 PROFILE",   callback_data="profile")],
        [InlineKeyboardButton("📋 MY ADS",    callback_data="my_ads")],
        [InlineKeyboardButton("🛡️ SAFETY",   callback_data="safety")],
        [InlineKeyboardButton("💬 FEEDBACK",  callback_data="feedback")],
        [InlineKeyboardButton("💰 DONATION",  callback_data="donation")],
    ])

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

async def send_main_menu(target, edit=False):
    text = "💰 *FIFO.EXCHANGE*\n\nUAE ↔ Nigeria\nFree P2P Exchange"
    if edit:
        await target.edit_message_text(text, parse_mode="Markdown", reply_markup=main_keyboard())
    else:
        await target.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard())

# ─── /start & menu ────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    await send_main_menu(update.message)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await send_main_menu(query, edit=True)

# ─── profile ──────────────────────────────────────────────────────────────────

async def _show_profile(target_user_id: int, viewer_id: int, reply_to):
    """Shared logic for /profile command and profile callback."""
    conn = sqlite3.connect('fifo.db')
    c = conn.cursor()
    c.execute('''SELECT username, first_name, registered_at, total_ads,
                        rating_total, rating_count
                 FROM users WHERE user_id = ?''', (target_user_id,))
    row = c.fetchone()
    if not row:
        await reply_to("❌ Profile not found")
        conn.close()
        return

    username, first_name, reg_date, total_ads, rating_total, rating_count = row
    reg_str    = datetime.fromisoformat(reg_date).strftime('%d %b %Y')
    avg_rating = rating_total / rating_count if rating_count > 0 else 0

    c.execute('''SELECT r.rating, r.comment, r.created_at, u.username
                 FROM reviews r
                 JOIN users u ON r.from_user_id = u.user_id
                 WHERE r.to_user_id = ?
                 ORDER BY r.created_at DESC LIMIT 3''', (target_user_id,))
    reviews = c.fetchall()
    conn.close()

    text = (
        f"👤 *PROFILE*\n\n"
        f"📛 Name: {first_name}\n"
        f"🆔 @{username or 'no username'}\n"
        f"📅 Registered: {reg_str}\n"
        f"📢 Total ads: {total_ads}\n"
        f"⭐ Rating: {get_stars(int(avg_rating))} {avg_rating:.1f}/10 ({rating_count} reviews)\n\n"
        f"📝 *Recent reviews:*\n"
    )

    if reviews:
        for r in reviews:
            stars = get_stars(r[0])
            date  = datetime.fromisoformat(r[2]).strftime('%d %b')
            comment = r[1][:50] + ("..." if len(r[1]) > 50 else "") if r[1] else "—"
            text += f"\n{stars} from @{r[3]} ({date}): {comment}"
    else:
        text += "\nNo reviews yet"

    keyboard = []
    if target_user_id != viewer_id:
        keyboard.append([InlineKeyboardButton("⭐ Leave a review", callback_data=f"review_{target_user_id}")])
    keyboard.append([InlineKeyboardButton("◀️ Back", callback_data="menu")])

    await reply_to(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /profile [username]"""
    viewer_id      = update.effective_user.id
    target_user_id = viewer_id

    if context.args:
        target_username = context.args[0].replace("@", "")
        conn = sqlite3.connect('fifo.db')
        c = conn.cursor()
        c.execute("SELECT user_id FROM users WHERE username = ?", (target_username,))
        result = c.fetchone()
        conn.close()
        if result:
            target_user_id = result[0]
        else:
            await update.message.reply_text(f"❌ User @{target_username} not found")
            return

    await _show_profile(target_user_id, viewer_id,
                        reply_to=update.message.reply_text)

async def profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'profile' button — shows own profile."""
    query = update.callback_query
    await query.answer()
    await _show_profile(query.from_user.id, query.from_user.id,
                        reply_to=query.message.reply_text)

# ─── reviews ──────────────────────────────────────────────────────────────────

async def start_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    target_user_id = int(query.data.replace("review_", ""))

    # FIX: prevent self-review
    if target_user_id == query.from_user.id:
        await query.answer("❌ You can't review yourself.", show_alert=True)
        return

    user_sessions[query.from_user.id] = {
        "step": "review_rating",
        "target_user_id": target_user_id,
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
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def review_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    uid    = query.from_user.id
    rating = int(query.data.replace("rating_", ""))

    if uid in user_sessions and user_sessions[uid].get("step") == "review_rating":
        user_sessions[uid]["rating"] = rating
        user_sessions[uid]["step"]   = "review_comment"
        await query.edit_message_text(
            f"⭐ Score: {rating}/10\n\n📝 Write a comment (or send /skip to skip):",
            parse_mode="Markdown",
        )

async def _save_review(from_id: int, target_id: int, rating: int, comment: str, reply_fn):
    conn = sqlite3.connect('fifo.db')
    c = conn.cursor()
    c.execute('''INSERT INTO reviews (from_user_id, to_user_id, rating, comment, created_at)
                 VALUES (?, ?, ?, ?, ?)''',
              (from_id, target_id, rating, comment, datetime.now()))
    c.execute('SELECT AVG(rating), COUNT(*) FROM reviews WHERE to_user_id = ?', (target_id,))
    avg, count = c.fetchone()
    c.execute('UPDATE users SET rating_total = ?, rating_count = ? WHERE user_id = ?',
              (avg * count, count, target_id))
    conn.commit()
    conn.close()
    await reply_fn("✅ Review saved!")

async def review_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    text = update.message.text

    if uid not in user_sessions or user_sessions[uid].get("step") != "review_comment":
        return

    comment = "" if text == "/skip" else text
    data    = user_sessions.pop(uid)

    await _save_review(uid, data["target_user_id"], data["rating"], comment,
                       update.message.reply_text)

async def skip_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in user_sessions and user_sessions[uid].get("step") == "review_comment":
        data    = user_sessions.pop(uid)
        await _save_review(uid, data["target_user_id"], data["rating"], "",
                           update.message.reply_text)

# ─── my ads ───────────────────────────────────────────────────────────────────

async def _show_my_ads(user_id: int, reply_fn):
    conn = sqlite3.connect('fifo.db')
    c = conn.cursor()
    c.execute('''SELECT id, from_country, from_city, to_country, to_city,
                        give_currency, get_currency, amount, duration, status, created_at
                 FROM ads WHERE user_id = ? ORDER BY created_at DESC LIMIT 10''', (user_id,))
    ads = c.fetchall()
    conn.close()

    if not ads:
        await reply_fn("📭 You have no ads yet.")
        return

    text = "📋 *YOUR ADS*\n\n"
    for ad in ads:
        ad_id, from_c, from_city, to_c, to_city, give_c, get_c, amount, dur, status, created = ad
        date  = datetime.fromisoformat(created).strftime('%d %b')
        text += f"#{ad_id} {from_city}→{to_city} | {amount} {give_c}➔{get_c} | {dur} | {status} ({date})\n"

    await reply_fn(text, parse_mode="Markdown")

async def my_ads_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _show_my_ads(update.effective_user.id, update.message.reply_text)

async def my_ads_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _show_my_ads(query.from_user.id, query.message.reply_text)

# ─── static pages ─────────────────────────────────────────────────────────────

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
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu")]]),
    )

async def feedback_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_sessions[query.from_user.id] = {"step": "feedback"}
    await query.edit_message_text(
        "💬 *Send your feedback, ideas or bug reports*\n\nJust type your message below:",
        parse_mode="Markdown",
    )

async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid      = update.effective_user.id
    text     = update.message.text
    username = update.effective_user.username or "no username"
    fname    = update.effective_user.first_name or "User"

    post = (
        f"💬 *NEW FEEDBACK*\n\n"
        f"👤 From: {fname} (@{username})\n"
        f"🆔 ID: {uid}\n\n"
        f"📝 Message:\n{text}\n\n"
        f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    try:
        await context.bot.send_message(chat_id=FEEDBACK_CHANNEL_ID, text=post, parse_mode="Markdown")
        await update.message.reply_text("✅ Thank you! Your feedback has been sent.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error sending feedback: {e}")

    user_sessions.pop(uid, None)

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
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu")]]),
    )

# ─── post ad flow ─────────────────────────────────────────────────────────────

async def post_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    user_sessions[uid] = {}
    kb = [[InlineKeyboardButton(c["name"], callback_data=f"from_{code}")]
          for code, c in COUNTRIES.items()]
    await query.edit_message_text("📍 *STEP 1/7* — Your country:",
                                  parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def from_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    code = query.data.replace("from_", "")
    user_sessions[uid]["from_country"] = COUNTRIES[code]["name"]
    user_sessions[uid]["from_code"]    = code
    kb = [[InlineKeyboardButton(city, callback_data=f"fromcity_{city}")]
          for city in COUNTRIES[code]["cities"]]
    kb.append([InlineKeyboardButton("◀️ Back", callback_data="post_ad")])
    await query.edit_message_text(
        f"📍 *STEP 2/7* — Your city in {COUNTRIES[code]['name']}:",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def from_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    user_sessions[uid]["from_city"] = query.data.replace("fromcity_", "")
    from_code = user_sessions[uid]["from_code"]
    kb = [[InlineKeyboardButton(c["name"], callback_data=f"to_{code}")]
          for code, c in COUNTRIES.items() if code != from_code]
    kb.append([InlineKeyboardButton("◀️ Back", callback_data=f"from_{from_code}")])
    await query.edit_message_text("🎯 *STEP 3/7* — Destination country:",
                                  parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def to_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    code = query.data.replace("to_", "")
    user_sessions[uid]["to_country"] = COUNTRIES[code]["name"]
    user_sessions[uid]["to_code"]    = code
    kb = [[InlineKeyboardButton(city, callback_data=f"tocity_{city}")]
          for city in COUNTRIES[code]["cities"]]
    # FIX: correct Back button — goes to from_city step via from_country handler
    kb.append([InlineKeyboardButton("◀️ Back", callback_data=f"from_{user_sessions[uid]['from_code']}")])
    await query.edit_message_text(
        f"🎯 *STEP 4/7* — City in {COUNTRIES[code]['name']}:",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def to_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    user_sessions[uid]["to_city"] = query.data.replace("tocity_", "")
    kb = [[InlineKeyboardButton(curr, callback_data=f"give_{curr}")] for curr in CURRENCIES]
    kb.append([InlineKeyboardButton("◀️ Back", callback_data=f"to_{user_sessions[uid]['to_code']}")])
    await query.edit_message_text("💰 *STEP 5/7* — Currency you GIVE:",
                                  parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def give_currency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    user_sessions[uid]["give_currency"] = query.data.replace("give_", "")
    kb = [[InlineKeyboardButton(curr, callback_data=f"get_{curr}")] for curr in CURRENCIES]
    kb.append([InlineKeyboardButton("◀️ Back", callback_data=f"tocity_{user_sessions[uid]['to_city']}")])
    await query.edit_message_text("💵 *STEP 6/7* — Currency you GET:",
                                  parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def get_currency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    user_sessions[uid]["get_currency"] = query.data.replace("get_", "")
    user_sessions[uid]["step"] = "amount"
    await query.edit_message_text("💲 *STEP 7/7* — Enter amount:", parse_mode="Markdown")

async def duration_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    user_sessions[uid]["duration"] = DURATIONS[query.data.replace("dur_", "")]
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
    # FIX: track publish lock to prevent double-publish
    user_sessions[uid]["published"] = False
    await query.edit_message_text(
        preview, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ PUBLISH", callback_data="publish")]]),
    )

# ─── matching & publish ───────────────────────────────────────────────────────

async def find_matches(ad_id: int, context):
    conn = sqlite3.connect('fifo.db')
    c = conn.cursor()
    c.execute("SELECT * FROM ads WHERE id = ?", (ad_id,))
    ad = c.fetchone()
    if not ad:
        conn.close()
        return

    (_, user_id, username, from_country, from_city, to_country, to_city,
     give_curr, get_curr, amount, contact, duration, status, created) = ad

    c.execute('''SELECT * FROM ads WHERE id != ? AND status='active'
                 AND from_country = ? AND from_city = ?
                 AND to_country = ? AND to_city = ?
                 AND give_currency = ? AND get_currency = ?''',
              (ad_id, to_country, to_city, from_country, from_city, get_curr, give_curr))
    matches = c.fetchall()
    conn.close()

    for match in matches:
        match_id       = match[0]
        match_user     = match[1]
        match_username = match[2]
        match_contact  = match[10]
        match_amount   = match[9]

        percent = int(min(amount, match_amount) / max(amount, match_amount) * 100) \
                  if amount > 0 and match_amount > 0 else 0

        msg1 = (
            f"🎯 *MATCH FOUND!* ({percent}% match)\n\n"
            f"Your ad #{ad_id} ↔ #{match_id}\n\n"
            f"📍 FROM: {from_country} / {from_city}\n"
            f"💰 GIVE: {amount} {give_curr}\n"
            f"🎯 TO: {to_country} / {to_city}\n"
            f"💵 GET: {get_curr}\n\n"
            f"📞 CONTACT: {match_contact}\n"
            f"👤 @{match_username}"
        )
        msg2 = (
            f"🎯 *MATCH FOUND!* ({percent}% match)\n\n"
            f"Your ad #{match_id} ↔ #{ad_id}\n\n"
            f"📍 FROM: {match[3]} / {match[4]}\n"
            f"💰 GIVE: {match_amount} {match[7]}\n"
            f"🎯 TO: {match[5]} / {match[6]}\n"
            f"💵 GET: {match[8]}\n\n"
            f"📞 CONTACT: {contact}\n"
            f"👤 @{username}"
        )
        try:
            await context.bot.send_message(chat_id=user_id,    text=msg1, parse_mode="Markdown")
        except Exception:
            pass
        try:
            await context.bot.send_message(chat_id=match_user, text=msg2, parse_mode="Markdown")
        except Exception:
            pass

async def publish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    # FIX: prevent double-publish on rapid taps
    if uid not in user_sessions:
        return
    if user_sessions[uid].get("published"):
        await query.answer("⏳ Already publishing…", show_alert=True)
        return
    user_sessions[uid]["published"] = True

    data = user_sessions[uid]

    conn = sqlite3.connect('fifo.db')
    c = conn.cursor()
    c.execute('''INSERT INTO ads
                 (user_id, username, from_country, from_city, to_country, to_city,
                  give_currency, get_currency, amount, contact, duration, status, created_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              (uid, data["username"],
               data["from_country"], data["from_city"],
               data["to_country"],   data["to_city"],
               data["give_currency"], data["get_currency"],
               data["amount"], data["contact"],
               data["duration"], 'active', datetime.now()))
    ad_id = c.lastrowid
    conn.commit()
    c.execute('UPDATE users SET total_ads = total_ads + 1 WHERE user_id = ?', (uid,))
    conn.commit()
    conn.close()

    await find_matches(ad_id, context)

    post = (
        f"#fifo #{data['from_city'].lower()} #{data['to_city'].lower()}\n\n"
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
        await context.bot.send_message(chat_id=CHANNEL_ID, text=post, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Channel post error: {e}")

    await query.edit_message_text(
        f"✅ *AD POSTED!*\n\nID: #{ad_id}\nChannel: @fifoexchange",
        parse_mode="Markdown",
    )
    user_sessions.pop(uid, None)

# ─── text handler ─────────────────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
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
            amount = float(text.replace(",", ""))
            user_sessions[uid]["amount"] = amount
            user_sessions[uid]["step"]   = "contact"
            await update.message.reply_text(
                "📞 *WhatsApp number?* (e.g., +971501234567)", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ Invalid number, please try again")

    elif step == "contact":
        user_sessions[uid]["contact"]  = text
        user_sessions[uid]["username"] = update.effective_user.username or "user"
        user_sessions[uid]["step"]     = "duration"
        kb = [[InlineKeyboardButton(dur, callback_data=f"dur_{code}")]
              for code, dur in DURATIONS.items()]
        await update.message.reply_text(
            "⏱️ *Select duration:*", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb))

# ─── central button router ────────────────────────────────────────────────────

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data  = query.data

    if   data == "post_ad":            await post_ad(update, context)
    elif data == "profile":            await profile_callback(update, context)
    elif data == "my_ads":             await my_ads_callback(update, context)
    elif data == "safety":             await safety(update, context)
    elif data == "feedback":           await feedback_start(update, context)
    elif data == "donation":           await donation(update, context)
    elif data == "menu":               await menu(update, context)
    elif data == "publish":            await publish(update, context)
    elif data.startswith("review_"):   await start_review(update, context)
    elif data.startswith("rating_"):   await review_rating(update, context)
    elif data.startswith("from_"):     await from_country(update, context)
    elif data.startswith("fromcity_"): await from_city(update, context)
    elif data.startswith("to_"):       await to_country(update, context)
    elif data.startswith("tocity_"):   await to_city(update, context)
    elif data.startswith("give_"):     await give_currency(update, context)
    elif data.startswith("get_"):      await get_currency(update, context)
    elif data.startswith("dur_"):      await duration_handler(update, context)
    else:
        await query.answer()

# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("myads",   my_ads_command))
    app.add_handler(CommandHandler("skip",    skip_comment))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("🚀 FIFO.EXCHANGE bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
