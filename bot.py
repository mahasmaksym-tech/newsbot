import os,sqlite3,datetime,asyncio,requests
from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import Application,CommandHandler,CallbackQueryHandler,ContextTypes,PreCheckoutQueryHandler,MessageHandler,filters

TELEGRAM_BOT_TOKEN=os.environ.get("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY=os.environ.get("ANTHROPIC_API_KEY")
SEND_HOUR=int(os.environ.get("SEND_HOUR","8"))
STARS_PRICE=100
ALL_TOPICS={"world":("🌍","Світові новини"),"ukraine":("🇺🇦","Україна"),"usa":("🇺🇸","США та політика"),"tech":("💻","Технології та AI"),"military":("⚔️","Військо та геополітика"),"business":("📈","Бізнес та економіка"),"science":("🔬","Наука"),"sport":("⚽","Спорт")}
FREE_TOPICS_LIMIT=2
DB_PATH=os.environ.get("DB_PATH","newsbot.db")

def get_db():
    conn=sqlite3.connect(DB_PATH);conn.row_factory=sqlite3.Row;return conn

def init_db():
    with get_db() as conn:
        conn.executescript("CREATE TABLE IF NOT EXISTS users(chat_id INTEGER PRIMARY KEY,username TEXT,first_name TEXT,is_premium INTEGER DEFAULT 0,premium_until TEXT,active INTEGER DEFAULT 1,created_at TEXT DEFAULT(datetime('now')));CREATE TABLE IF NOT EXISTS subscriptions(chat_id INTEGER,topic TEXT,PRIMARY KEY(chat_id,topic));")

def get_user(chat_id):
    with get_db() as conn:return conn.execute("SELECT * FROM users WHERE chat_id=?",(chat_id,)).fetchone()

def upsert_user(chat_id,username,first_name):
    with get_db() as conn:conn.execute("INSERT INTO users(chat_id,username,first_name)VALUES(?,?,?)ON CONFLICT(chat_id)DO UPDATE SET username=excluded.username,first_name=excluded.first_name",(chat_id,username,first_name))

def get_topics(chat_id):
    with get_db() as conn:return[r["topic"]for r in conn.execute("SELECT topic FROM subscriptions WHERE chat_id=?",(chat_id,)).fetchall()]

def set_topics(chat_id,topics):
    with get_db() as conn:
        conn.execute("DELETE FROM subscriptions WHERE chat_id=?",(chat_id,))
        conn.executemany("INSERT INTO subscriptions VALUES(?,?)",[(chat_id,t)for t in topics])

def is_premium(chat_id):
    user=get_user(chat_id)
    if not user or not user["is_premium"]:return False
    return not user["premium_until"]or user["premium_until"]>=datetime.date.today().isoformat()

def set_premium(chat_id,days=30):
    until=(datetime.date.today()+datetime.timedelta(days=days)).isoformat()
    with get_db() as conn:conn.execute("UPDATE users SET is_premium=1,premium_until=? WHERE chat_id=?",(until,chat_id))

def get_all_active():
    with get_db() as conn:return conn.execute("SELECT chat_id FROM users WHERE active=1").fetchall()

TOPIC_QUERIES={"world":"top world news today","ukraine":"Ukraine war news today","usa":"US politics news today","tech":"technology AI news today","military":"military geopolitics news today","business":"business economy news today","science":"science news today","sport":"sports results news today"}

def fetch_briefing(topics):
    today=datetime.date.today().strftime("%d %B %Y")
    labels=[f"{ALL_TOPICS[t][0]} {ALL_TOPICS[t][1]}"for t in topics if t in ALL_TOPICS]
    queries=[TOPIC_QUERIES[t]for t in topics if t in TOPIC_QUERIES]
    system=f"""Ти редактор новинного брифінгу для Telegram. Сьогодні {today}.
Склади брифінг за темами: {', '.join(labels)}.
Формат (суворо Telegram MarkdownV2):
- Починай: 📰 *Брифінг {today}*
- Блок теми: *{'{emoji} Назва теми'}*
- Новина: *Заголовок* потім з нового рядка текст 1-2 речення потім [Джерело](url)
- 2-4 новини на тему, між новинами порожній рядок
- В кінці: ─────────────────
- Мова українська
- ВАЖЛИВО: екранируй спецсимволи крапку та дефіс: \. \-"""
    r=requests.post("https://api.anthropic.com/v1/messages",headers={"x-api-key":ANTHROPIC_API_KEY,"anthropic-version":"2023-06-01","anthropic-beta":"web-search-2025-03-05","content-type":"application/json"},json={"model":"claude-sonnet-4-6","max_tokens":3000,"system":system,"tools":[{"type":"web_search_20250305","name":"web_search"}],"messages":[{"role":"user","content":f"Новини за: {', '.join(queries)}"}]},timeout=120)
    r.raise_for_status()
    return"\n".join(b["text"]for b in r.json().get("content",[])if b.get("type")=="text").strip()

def kb(selected,premium):
    btns=[]
    for key,(emoji,label) in ALL_TOPICS.items():
        locked=not premium and key not in selected and len(selected)>=FREE_TOPICS_LIMIT
        text=f"🔒 {emoji} {label}"if locked else(f"✅ {emoji} {label}"if key in selected else f"{emoji} {label}")
        btns.append([InlineKeyboardButton(text,callback_data=f"topic:{key}")])
    btns.append([InlineKeyboardButton("💾 Зберегти",callback_data="save")])
    if not premium:btns.append([InlineKeyboardButton("⭐ Преміум — всі теми",callback_data="buy_premium")])
    return InlineKeyboardMarkup(btns)

async def cmd_start(update,ctx):
    u=update.effective_user;upsert_user(u.id,u.username,u.first_name)
    sel=get_topics(u.id)
    if not sel:set_topics(u.id,["world","ukraine"]);sel=["world","ukraine"]
    prem=is_premium(u.id)
    await update.message.reply_text(f"👋 Привіт, {u.first_name}\!\n\nЯ надсилатиму щоденний *новинний брифінг* з посиланнями\.\n\nОбери теми 👇\n_{'⭐ Преміум активний' if prem else f'Безкоштовно: до {FREE_TOPICS_LIMIT} тем'}_",parse_mode="MarkdownV2",reply_markup=kb(sel,prem))

async def cmd_settings(update,ctx):
    u=update.effective_user;upsert_user(u.id,u.username,u.first_name)
    await update.message.reply_text("⚙️ *Налаштування тем*",parse_mode="MarkdownV2",reply_markup=kb(get_topics(u.id),is_premium(u.id)))

async def cmd_now(update,ctx):
    topics=get_topics(update.effective_user.id)
    if not topics:await update.message.reply_text("Спочатку обери теми: /settings");return
    msg=await update.message.reply_text("🔍 Збираю новини, зачекай ~30 сек\.\.\.",parse_mode="MarkdownV2")
    try:
        b=fetch_briefing(topics);await msg.delete()
        for chunk in[b[i:i+4000]for i in range(0,len(b),4000)]:
            await update.message.reply_text(chunk,parse_mode="MarkdownV2",disable_web_page_preview=False)
    except Exception as e:await msg.edit_text(f"❌ Помилка: {e}")

async def cmd_status(update,ctx):
    uid=update.effective_user.id;topics=get_topics(uid);prem=is_premium(uid);user=get_user(uid)
    tlist="\n".join(f"• {ALL_TOPICS[t][0]} {ALL_TOPICS[t][1]}"for t in topics if t in ALL_TOPICS)or"немає"
    status=f"⭐ Преміум до {user['premium_until']}"if prem else f"Безкоштовний \({FREE_TOPICS_LIMIT} теми\)"
    await update.message.reply_text(f"📊 *Статус*\n\n{status}\n\n*Теми:*\n{tlist}\n\nБрифінг о {SEND_HOUR}:00 UTC \(~{SEND_HOUR+3}:00 Київ\)",parse_mode="MarkdownV2")

async def on_callback(update,ctx):
    q=update.callback_query;await q.answer();uid=q.from_user.id;data=q.data
    if data.startswith("topic:"):
        key=data.split(":")[1];sel=get_topics(uid);prem=is_premium(uid)
        if key in sel:sel.remove(key)
        elif not prem and len(sel)>=FREE_TOPICS_LIMIT:await q.answer("🔒 Безкоштовно лише 2 теми\. Натисни ⭐ Преміум",show_alert=True);return
        else:sel.append(key)
        set_topics(uid,sel);await q.edit_message_reply_markup(reply_markup=kb(sel,prem))
    elif data=="save":
        sel=get_topics(uid)
        if not sel:await q.answer("Обери хоча б одну тему\!",show_alert=True);return
        labels=", ".join(f"{ALL_TOPICS[t][0]} {ALL_TOPICS[t][1]}"for t in sel if t in ALL_TOPICS)
        await q.edit_message_text(f"✅ *Збережено\!*\n\nТеми: {labels}\n\nБрифінг щодня о {SEND_HOUR}:00 UTC\n\nОтримати зараз: /now",parse_mode="MarkdownV2")
    elif data=="buy_premium":
        await ctx.bot.send_invoice(chat_id=uid,title="⭐ News Brief Premium",description="Всі 8 тем без обмежень",payload="premium_1month",currency="XTR",prices=[{"label":"Преміум на місяць","amount":STARS_PRICE}],provider_token="")

async def precheckout(update,ctx):await update.pre_checkout_query.answer(ok=True)

async def paid(update,ctx):
    set_premium(update.effective_user.id,30)
    await update.message.reply_text("⭐ *Преміум активовано на 30 днів\!*\n\nОбирай будь\-які теми: /settings",parse_mode="MarkdownV2")

async def daily(app):
    for row in get_all_active():
        cid=row["chat_id"];topics=get_topics(cid)
        if not topics:continue
        try:
            b=fetch_briefing(topics)
            for chunk in[b[i:i+4000]for i in range(0,len(b),4000)]:
                await app.bot.send_message(chat_id=cid,text=chunk,parse_mode="MarkdownV2",disable_web_page_preview=False)
            await asyncio.sleep(1)
        except Exception as e:print(f"Err {cid}: {e}")

async def scheduler(app):
    while True:
        now=datetime.datetime.utcnow()
        if now.hour==SEND_HOUR and now.minute==0:await daily(app);await asyncio.sleep(61)
        else:await asyncio.sleep(30)

def main():
    init_db();app=Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",cmd_start))
    app.add_handler(CommandHandler("settings",cmd_settings))
    app.add_handler(CommandHandler("now",cmd_now))
    app.add_handler(CommandHandler("status",cmd_status))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT,paid))
    async def post_init(a):asyncio.create_task(scheduler(a))
    app.post_init=post_init
    print("🤖 Бот запущено");app.run_polling()

if __name__=="__main__":main()
