"""
Atestlar-style Telegram test bot
================================
aiogram v3 + SQLite (aiosqlite) + aiohttp (Mini App & JSON API).

Bitta jarayon ichida ikkita narsa ishlaydi:
  1. Telegram bot (long polling)
  2. aiohttp veb-server: webapp/index.html va /api/* (Render PORT ga ulanadi)

Muhit o'zgaruvchilari:
  BOT_TOKEN     - BotFather tokeni (majburiy)
  WEBAPP_URL    - Mini App manzili (https://...). Bo'sh bo'lsa Render'dagi
                  RENDER_EXTERNAL_URL ishlatiladi.
  ADMIN_IDS     - vergul bilan ajratilgan admin ID lar (/addtest uchun)
  DB_PATH       - SQLite fayl yo'li (standart: data/bot.db)
  PORT          - veb-server porti (Render o'zi beradi)
"""
import asyncio
import json
import logging
import os
import random
from datetime import datetime, timezone
from html import escape as esc
from pathlib import Path
from urllib.parse import quote

import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MenuButtonWebApp,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
    BotCommand,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --------------------------------------------------------------------------- #
# Sozlamalar
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = (os.getenv("WEBAPP_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "bot.db"))
PORT = int(os.getenv("PORT", "8080"))

REFERRALS_REQUIRED = 4 
CHANNEL_USERNAME = "@MilliyTestlarUz" 
# Premium fanlarni ochish uchun kerakli takliflar
LETTERS = ["A", "B", "C", "D"]
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # chalkash harflarsiz

CATEGORIES = [
    ("Matematika", "🧮"), ("Fizika", "⚛️"), ("Kimyo", "🧪"),
    ("Biologiya", "🧬"), ("Informatika", "💻"), ("Ona tili", "📖"),
    ("Tarix", "🏛"), ("Ingliz tili", "🇬🇧"), ("Geografiya", "🌍"),
]
CAT_ICON = dict(CATEGORIES)

# Pastki menyu tugmalari
BTN_TESTS = "📝 Testlar"
BTN_DAILY = "📅 Kunlik test"
BTN_REF = "🎁 Taklif"
BTN_STATUS = "📊 Holat"
BTN_GROUP = "➕ Guruhga qo'shish"
BTN_HELP = "ℹ️ Yordam"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("atestlar")

router = Router()
db: aiosqlite.Connection = None  # type: ignore
BOT_USERNAME = ""


# --------------------------------------------------------------------------- #
# Ma'lumotlar bazasi
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id        INTEGER PRIMARY KEY,
    full_name      TEXT NOT NULL,
    username       TEXT,
    referrer_id    INTEGER,
    referral_count INTEGER NOT NULL DEFAULT 0,
    joined_date    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS tests (
    test_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT NOT NULL UNIQUE,
    title      TEXT NOT NULL,
    category   TEXT NOT NULL,
    creator_id INTEGER NOT NULL DEFAULT 0,
    is_premium INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS questions (
    question_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id        INTEGER NOT NULL REFERENCES tests(test_id) ON DELETE CASCADE,
    question_text  TEXT NOT NULL,
    options        TEXT NOT NULL,            -- JSON ro'yxat
    correct_option INTEGER NOT NULL          -- 0..3
);
CREATE TABLE IF NOT EXISTS results (
    result_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    test_id         INTEGER NOT NULL,
    score           INTEGER NOT NULL,
    total_questions INTEGER NOT NULL,
    is_daily        INTEGER NOT NULL DEFAULT 0,
    completed_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sessions (        -- davom etayotgan test
    user_id  INTEGER PRIMARY KEY,
    test_id  INTEGER NOT NULL,
    q_index  INTEGER NOT NULL DEFAULT 0,
    score    INTEGER NOT NULL DEFAULT 0,
    is_daily INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_q_test ON questions(test_id);
CREATE INDEX IF NOT EXISTS idx_r_user ON results(user_id);
CREATE INDEX IF NOT EXISTS idx_r_test ON results(test_id);
"""

# (sarlavha, kategoriya, premium, [(savol, [variantlar], to'g'ri_indeks)])
SEED = [
    ("Asosiy amallar", "Matematika", 0, [
        ("12 × 12 = ?", ["124", "144", "132", "154"], 1),
        ("x² − 9 = 0 tenglama ildizlari?", ["±3", "3", "9", "±9"], 0),
        ("Uchburchak ichki burchaklari yig'indisi?", ["90°", "180°", "270°", "360°"], 1),
        ("√169 = ?", ["11", "12", "13", "14"], 2)]),
    ("Murakkab matematika (Premium)", "Matematika", 1, [
        ("2¹⁰ = ?", ["512", "1000", "1024", "2048"], 2),
        ("log₂ 8 = ?", ["2", "3", "4", "8"], 1),
        ("sin 90° = ?", ["0", "1", "0,5", "−1"], 1)]),
    ("Mexanika asoslari", "Fizika", 0, [
        ("Tezlikning SI dagi birligi?", ["km/soat", "m/s", "m/s²", "N"], 1),
        ("Kuch birligi?", ["Joul", "Vatt", "Nyuton", "Paskal"], 2),
        ("Yorug'lik tezligi taxminan?", ["3·10⁸ m/s", "3·10⁶ m/s", "3·10⁵ m/s", "3·10³ m/s"], 0),
        ("Erkin tushish tezlanishi g ≈ ?", ["1,6 m/s²", "9,8 m/s²", "12 m/s²", "3,7 m/s²"], 1)]),
    ("Kimyo asoslari", "Kimyo", 0, [
        ("Suvning kimyoviy formulasi?", ["CO₂", "H₂O", "O₂", "NaCl"], 1),
        ("Natriy elementining belgisi?", ["N", "Na", "Ni", "K"], 1),
        ("pH = 7 muhit qanday?", ["Kislotali", "Ishqoriy", "Neytral", "Tuzli"], 2),
        ("Eng yengil element?", ["Geliy", "Vodorod", "Litiy", "Kislorod"], 1)]),
    ("Hujayra va organizmlar", "Biologiya", 0, [
        ("Hujayraning energiya stansiyasi?", ["Yadro", "Ribosoma", "Mitoxondriya", "Vakuola"], 2),
        ("Fotosintez qaysi organoidda kechadi?", ["Xloroplast", "Lizosoma", "Yadro", "Golji"], 0),
        ("Odamda xromosomalar soni?", ["23", "44", "46", "48"], 2),
        ("DNK ning to'liq nomi?", ["Dezoksiribonuklein kislota", "Ribonuklein kislota",
                                   "Aminokislota", "Dezoksiriboza"], 0)]),
    ("Informatika asoslari", "Informatika", 0, [
        ("1 bayt necha bit?", ["4", "8", "16", "32"], 1),
        ("Qaysi biri dasturlash tili?", ["HTML", "Python", "JPEG", "HTTP"], 1),
        ("CPU nima?", ["Xotira", "Protsessor", "Monitor", "Klaviatura"], 1),
        ("Veb-sahifa belgilash tili?", ["SQL", "CSS", "HTML", "PHP"], 2)]),
    ("Imlo va grammatika", "Ona tili", 0, [
        ("O'zbek lotin alifbosida nechta harf bor?", ["26", "29", "32", "35"], 1),
        ("Gapning bosh bo'laklari?", ["Ega va kesim", "Aniqlovchi va hol",
                                      "To'ldiruvchi va hol", "Ega va to'ldiruvchi"], 0),
        ("Sifat nimani bildiradi?", ["Harakat", "Narsa", "Belgi", "Son"], 2),
        ("«Kitob» so'zida nechta bo'g'in bor?", ["1", "2", "3", "4"], 1)]),
]


async def fetchone(sql: str, args=()):
    async with db.execute(sql, args) as cur:
        return await cur.fetchone()


async def fetchall(sql: str, args=()):
    async with db.execute(sql, args) as cur:
        return await cur.fetchall()


async def execute(sql: str, args=()):
    cur = await db.execute(sql, args)
    await db.commit()
    return cur.lastrowid


async def unique_code() -> str:
    """6 belgili noyob test kodi (masalan AB12CD)."""
    while True:
        code = "".join(random.choices(CODE_ALPHABET, k=6))
        if not await fetchone("SELECT 1 FROM tests WHERE code=?", (code,)):
            return code


async def create_test(title, category, creator_id, premium, questions) -> str:
    code = await unique_code()
    test_id = await execute(
        "INSERT INTO tests(code,title,category,creator_id,is_premium) VALUES(?,?,?,?,?)",
        (code, title, category, creator_id, premium))
    await db.executemany(
        "INSERT INTO questions(test_id,question_text,options,correct_option) VALUES(?,?,?,?)",
        [(test_id, q, json.dumps(o, ensure_ascii=False), c) for q, o, c in questions])
    await db.commit()
    return code


async def init_db():
    global db
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.executescript(SCHEMA)
    await db.commit()
    if not await fetchone("SELECT 1 FROM tests LIMIT 1"):
        for title, cat, prem, qs in SEED:
            await create_test(title, cat, 0, prem, qs)
        log.info("Namuna testlar qo'shildi")


# --------------------------------------------------------------------------- #
# Klaviaturalar va matnlar
# --------------------------------------------------------------------------- #
def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_TESTS), KeyboardButton(text=BTN_DAILY)],
            [KeyboardButton(text=BTN_REF), KeyboardButton(text=BTN_STATUS)],
            [KeyboardButton(text=BTN_GROUP), KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True, is_persistent=True,
        input_field_placeholder="Test kodini yuboring: #AB12CD")


WELCOME = (
    "👋 Salom, <b>{name}</b>!\n\n"
    "Bu bot orqali maktab va abituriyent fanlaridan testlar yechishingiz, "
    "natijangizni kuzatishingiz va do'stlaringiz bilan bellashishingiz mumkin.\n\n"
    "📝 <b>Testlar</b> — katalog yoki kod orqali test topish\n"
    "📅 <b>Kunlik test</b> — har kuni yangi test\n"
    "🎁 <b>Taklif</b> — do'stlarni taklif qilib Premium fanlarni oching\n\n"
    "Boshlash uchun pastdagi menyudan foydalaning 👇")

HELP = (
    "ℹ️ <b>Yordam</b>\n\n"
    "<b>Bot qanday ishlaydi?</b>\n"
    "1. «📝 Testlar» orqali katalogni oching va fan tanlang.\n"
    "2. Yoki test kodini yuboring, masalan <code>#AB12CD</code>.\n"
    "3. Savollarga A, B, C, D tugmalari bilan javob bering.\n"
    "4. Oxirida natija: ball, foiz va xatolar soni ko'rsatiladi.\n\n"
    "<b>Buyruqlar</b>\n"
    "/start — botni qayta ishga tushirish\n"
    "/help — shu yordam oynasi\n\n"
    "<b>Qoidalar</b>\n"
    f"• Premium fanlar {REFERRALS_REQUIRED} ta do'st taklif qilgach ochiladi.\n"
    "• Kunlik test kuniga bir marta yechiladi.\n"
    "• Bitta o'zingiz bilan taklif hisoblanmaydi.")


async def tests_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if WEBAPP_URL.startswith("https://"):
        kb.row(InlineKeyboardButton(text="🌐 Testlar katalogi (Web App)",
                                    web_app=WebAppInfo(url=WEBAPP_URL)))
    kb.row(InlineKeyboardButton(text="📚 Fanlar bo'yicha", callback_data="cats"))
    kb.row(InlineKeyboardButton(text="🔎 Kod bilan qidirish", callback_data="findcode"))
    return kb.as_markup()


# --------------------------------------------------------------------------- #
# Foydalanuvchi va referal tizimi
# --------------------------------------------------------------------------- #
async def register_user(bot: Bot, user, arg: str) -> bool:
    """Foydalanuvchini saqlaydi. Yangi bo'lsa va ref_ bo'lsa, taklif qilganga ball beradi."""
    existing = await fetchone("SELECT user_id FROM users WHERE user_id=?", (user.id,))
    if existing:
        await execute("UPDATE users SET full_name=?, username=? WHERE user_id=?",
                      (user.full_name, user.username, user.id))
        return False

    referrer_id = None
    if arg.startswith("ref_") and arg[4:].isdigit():
        rid = int(arg[4:])
        if rid != user.id and await fetchone("SELECT 1 FROM users WHERE user_id=?", (rid,)):
            referrer_id = rid

    await execute("INSERT INTO users(user_id,full_name,username,referrer_id) VALUES(?,?,?,?)",
                  (user.id, user.full_name, user.username, referrer_id))
    if referrer_id:
        await execute("UPDATE users SET referral_count=referral_count+1 WHERE user_id=?",
                      (referrer_id,))
        row = await fetchone("SELECT referral_count FROM users WHERE user_id=?", (referrer_id,))
        try:
            await bot.send_message(
                referrer_id,
                f"🎉 <b>{esc(user.full_name)}</b> sizning havolangiz orqali qo'shildi!\n"
                f"Takliflar: <b>{row['referral_count']}/{REFERRALS_REQUIRED}</b>")
        except Exception:
            pass  # referrer botni bloklagan bo'lishi mumkin
    return True


async def referral_count(user_id: int) -> int:
    row = await fetchone("SELECT referral_count FROM users WHERE user_id=?", (user_id,))
    return row["referral_count"] if row else 0


async def can_access(user_id: int, test) -> bool:
    return (not test["is_premium"]) or await referral_count(user_id) >= REFERRALS_REQUIRED


# --------------------------------------------------------------------------- #
# Test kartochkasi
# --------------------------------------------------------------------------- #
async def build_card(test) -> tuple[str, InlineKeyboardMarkup]:
    qn = (await fetchone("SELECT COUNT(*) n FROM questions WHERE test_id=?", (test["test_id"],)))["n"]
    st = await fetchone(
        "SELECT COUNT(*) n, AVG(score*100.0/total_questions) a FROM results WHERE test_id=?",
        (test["test_id"],))
    avg = f"{st['a']:.0f}%" if st["a"] is not None else "—"
    lock = "🔒 Premium (taklif orqali ochiladi)\n" if test["is_premium"] else ""
    text = (f"{CAT_ICON.get(test['category'], '📚')} <b>{esc(test['title'])}</b>\n"
            f"{lock}"
            f"Fan: {esc(test['category'])}\n"
            f"Savollar: <b>{qn}</b>\n"
            f"Yechganlar: <b>{st['n']}</b> · O'rtacha: <b>{avg}</b>\n"
            f"Kod: <code>#{test['code']}</code>")
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="▶️ Boshlash", callback_data=f"begin:{test['test_id']}"))
    kb.row(InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"cat:{test['category']}"))
    return text, kb.as_markup()


async def open_test_by_code(message: Message, code: str):
    code = code.strip().lstrip("#").upper()
    test = await fetchone("SELECT * FROM tests WHERE code=?", (code,))
    if not test:
        await message.answer("❌ Bunday kodli test topilmadi.\nKodni tekshirib, qayta yuboring.")
        return
    text, kb = await build_card(test)
    await message.answer(text, reply_markup=kb)


# --------------------------------------------------------------------------- #
# Quiz mexanikasi
# --------------------------------------------------------------------------- #
async def render_question(test, q, idx: int, total: int):
    opts = json.loads(q["options"])
    lines = "\n".join(f"<b>{LETTERS[i]})</b> {esc(o)}" for i, o in enumerate(opts))
    text = (f"📝 <b>{esc(test['title'])}</b>\n"
            f"Savol <b>{idx + 1}/{total}</b>\n\n"
            f"{esc(q['question_text'])}\n\n{lines}")
    kb = InlineKeyboardBuilder()
    for i in range(len(opts)):
        kb.button(text=LETTERS[i], callback_data=f"ans:{q['question_id']}:{i}")
    kb.adjust(len(opts))
    kb.row(InlineKeyboardButton(text="🛑 To'xtatish", callback_data="quit"))
    return text, kb.as_markup()


async def start_quiz(bot: Bot, chat_id: int, user_id: int, test_id: int, is_daily: bool = False):
    test = await fetchone("SELECT * FROM tests WHERE test_id=?", (test_id,))
    qs = await fetchall("SELECT * FROM questions WHERE test_id=? ORDER BY question_id", (test_id,))
    if not test or not qs:
        await bot.send_message(chat_id, "⚠️ Bu testda savollar yo'q.")
        return
    await execute("INSERT OR REPLACE INTO sessions(user_id,test_id,q_index,score,is_daily) "
                  "VALUES(?,?,0,0,?)", (user_id, test_id, int(is_daily)))
    text, kb = await render_question(test, qs[0], 0, len(qs))
    await bot.send_message(chat_id, text, reply_markup=kb)


async def safe_edit(message: Message, text: str, kb: InlineKeyboardMarkup | None = None):
    try:
        await message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            raise


async def check_subscription(user_id: int, bot: Bot) -> bool:
  try:
    member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
    if member.status in ["member", "administrator", "creator"]:
      return True
  except Exception:
    pass
  return False
# Handlerlar: /start va asosiy menyu
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, bot: Bot):
  is_subscribed = await check_subscription(message.from_user.id, bot)

  if not is_subscribed:
    sub_markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📢 Kanalga a'zo bo'lish",
                    url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ A'zo bo'ldim", callback_data="check_sub"
                )
            ],
        ]
    )
    await message.answer(
        "❌ **Botdan foydalanish uchun avval kanalimizga a'zo bo'ling:**\n\n"
        f"👉 {CHANNEL_USERNAME}\n\n"
        "A'zo bo'lgach, **'✅ A'zo bo'ldim'** tugmasini bosing.",
        reply_markup=sub_markup,
    )
    return

  arg = command.args or ""
  await register_user(bot, message.from_user, arg)

  # Web App tugmasi va chiroyli matn
  web_app_btn = InlineKeyboardButton(
      text="📝 Testlarni boshlash",
      web_app=WebAppInfo(url="https://milliytest.onrender.com"),
  )
  markup = InlineKeyboardMarkup(inline_keyboard=[[web_app_btn]])

  welcome_text = (
      f"👋 **Assalomu alaykum, {message.from_user.first_name} ustoz!**\n\n"
      "🎯 **BEPUL Testlar**\n"
      "Fan, pedagogik mahorat va kasb standarti testlari – har bir "
      "savolning yechilish usuli bilan.\n\n"
      "✅ Xatoingizni darrov tushunasiz\n"
      "✅ Imtihondagidek vaqt va ball\n"
      "✅ Har kuni 15 ta pedagogik mahorat testi\n"
      "🔒 Karta yoki telefon raqam so'ralmaydi\n\n"
      "🚀 Hoziroq 1-testni sinab ko'ring – atigi 1 daqiqa"
  )

  try:
    await message.answer_animation(
        animation="CgACAgIAAxkBAAE...",
        caption=welcome_text,
        reply_markup=markup,
    )
  except Exception:
    await message.answer(welcome_text, reply_markup=markup)

  if arg.startswith("test_"):
    await open_test_by_code(message, arg[5:])



@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def on_help(message: Message):
    await message.answer(HELP)


@router.message(F.text == BTN_TESTS)
async def on_tests(message: Message):
    await message.answer(
        "📝 <b>Testlar</b>\n\nKatalogni oching, fan tanlang yoki test kodini "
        "yuboring (masalan <code>#AB12CD</code>).",
        reply_markup=await tests_menu_kb())


@router.message(F.text == BTN_GROUP)
async def on_group(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="➕ Guruhni tanlash", url=f"https://t.me/{BOT_USERNAME}?startgroup=true")]])
    await message.answer("👥 Botni guruhingizga qo'shing va a'zolar bilan birga test yeching.",
                         reply_markup=kb)


@router.message(F.text == BTN_REF)
async def on_referral(message: Message):
    uid = message.from_user.id
    cnt = await referral_count(uid)
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
    done = min(cnt, REFERRALS_REQUIRED)
    bar = "🟩" * done + "⬜" * (REFERRALS_REQUIRED - done)
    status = ("✅ Premium fanlar <b>ochilgan</b>!" if cnt >= REFERRALS_REQUIRED else
              f"🔒 Premium fan ochish uchun yana <b>{REFERRALS_REQUIRED - cnt}</b> ta do'st kerak.")
    text = (f"🎁 <b>Do'stlarni taklif qilish</b>\n\n"
            f"Sizning havolangiz:\n<code>{link}</code>\n\n"
            f"Taklif qilinganlar: <b>{cnt}</b>\n"
            f"Jarayon: {bar} <b>{cnt}/{REFERRALS_REQUIRED}</b>\n\n{status}")
    share = f"https://t.me/share/url?url={quote(link)}&text={quote('Testlar yechamiz! 📝')}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📤 Do'stlarga yuborish", url=share)]])
    await message.answer(text, reply_markup=kb, disable_web_page_preview=True)


@router.message(F.text == BTN_STATUS)
async def on_status(message: Message):
    uid = message.from_user.id
    r = await fetchone(
        "SELECT COUNT(*) n, COALESCE(SUM(score),0) c, COALESCE(SUM(total_questions),0) t "
        "FROM results WHERE user_id=?", (uid,))
    pct = f"{r['c'] * 100 / r['t']:.0f}%" if r["t"] else "—"
    cnt = await referral_count(uid)
    await message.answer(
        f"📊 <b>Sizning holatingiz</b>\n\n"
        f"👤 {esc(message.from_user.full_name)}\n"
        f"✅ Yechilgan testlar: <b>{r['n']}</b>\n"
        f"🎯 To'g'ri javoblar: <b>{r['c']}</b> / {r['t']}\n"
        f"📈 O'rtacha natija: <b>{pct}</b>\n"
        f"👥 Takliflar: <b>{cnt}</b> / {REFERRALS_REQUIRED}")


@router.message(F.text == BTN_DAILY)
async def on_daily(message: Message, bot: Bot):
    uid = message.from_user.id
    tests = await fetchall("SELECT test_id FROM tests WHERE is_premium=0 ORDER BY test_id")
    if not tests:
        await message.answer("Hozircha testlar yo'q.")
        return
    today = datetime.now(timezone.utc).date()
    test_id = tests[today.toordinal() % len(tests)]["test_id"]
    done = await fetchone("SELECT 1 FROM results WHERE user_id=? AND is_daily=1 "
                          "AND date(completed_at)=date('now')", (uid,))
    if done:
        await message.answer("✅ Bugungi testni allaqachon yechdingiz.\nYangi test ertaga! 🌅")
        return
    test = await fetchone("SELECT title, category FROM tests WHERE test_id=?", (test_id,))
    await message.answer(f"📅 <b>Bugungi test</b>: {esc(test['title'])} ({esc(test['category'])})")
    await start_quiz(bot, message.chat.id, uid, test_id, is_daily=True)


# Test kodi: "#AB12CD" yoki "AB12CD" (faqat shaxsiy chatda)
@router.message(F.chat.type == "private", F.text.regexp(r"^#?[A-Za-z0-9]{6}$"))
async def on_code(message: Message):
    await open_test_by_code(message, message.text)


# --------------------------------------------------------------------------- #
# Handlerlar: inline navigatsiya
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "tests_menu")
async def cb_tests_menu(call: CallbackQuery):
    await safe_edit(call.message,
                    "📝 <b>Testlar</b>\n\nKatalogni oching, fan tanlang yoki test kodini yuboring.",
                    await tests_menu_kb())
    await call.answer()


@router.callback_query(F.data == "findcode")
async def cb_findcode(call: CallbackQuery):
    await call.message.answer("🔎 Test kodini yuboring, masalan: <code>#AB12CD</code>")
    await call.answer()


@router.callback_query(F.data == "cats")
async def cb_cats(call: CallbackQuery):
    counts = {r["category"]: r["n"] for r in await fetchall(
        "SELECT category, COUNT(*) n FROM tests GROUP BY category")}
    kb = InlineKeyboardBuilder()
    for name, icon in CATEGORIES:
        if counts.get(name):
            kb.button(text=f"{icon} {name} ({counts[name]})", callback_data=f"cat:{name}")
    kb.adjust(2)
    kb.row(InlineKeyboardButton(text="⬅️ Orqaga", callback_data="tests_menu"))
    await safe_edit(call.message, "📚 <b>Fanni tanlang:</b>", kb.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("cat:"))
async def cb_category(call: CallbackQuery):
    cat = call.data[4:]
    tests = await fetchall("SELECT * FROM tests WHERE category=? ORDER BY test_id DESC LIMIT 30", (cat,))
    kb = InlineKeyboardBuilder()
    for t in tests:
        kb.row(InlineKeyboardButton(
            text=f"{'🔒 ' if t['is_premium'] else ''}{t['title']}  #{t['code']}",
            callback_data=f"open:{t['code']}"))
    kb.row(InlineKeyboardButton(text="⬅️ Orqaga", callback_data="cats"))
    await safe_edit(call.message, f"{CAT_ICON.get(cat, '📚')} <b>{esc(cat)}</b> testlari:", kb.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("open:"))
async def cb_open(call: CallbackQuery):
    test = await fetchone("SELECT * FROM tests WHERE code=?", (call.data[5:],))
    if not test:
        await call.answer("Test topilmadi", show_alert=True)
        return
    text, kb = await build_card(test)
    await safe_edit(call.message, text, kb)
    await call.answer()


@router.callback_query(F.data.startswith("begin:"))
async def cb_begin(call: CallbackQuery, bot: Bot):
    test = await fetchone("SELECT * FROM tests WHERE test_id=?", (int(call.data[6:]),))
    if not test:
        await call.answer("Test topilmadi", show_alert=True)
        return
    if not await can_access(call.from_user.id, test):
        await call.answer(f"🔒 Premium test. {REFERRALS_REQUIRED} ta do'st taklif qiling "
                          f"(🎁 Taklif menyusi).", show_alert=True)
        return
    await call.answer()
    await start_quiz(bot, call.message.chat.id, call.from_user.id, test["test_id"])


@router.callback_query(F.data == "quit")
async def cb_quit(call: CallbackQuery):
    await execute("DELETE FROM sessions WHERE user_id=?", (call.from_user.id,))
    await safe_edit(call.message, "🛑 Test to'xtatildi. Natija saqlanmadi.")
    await call.answer()


@router.callback_query(F.data.startswith("ans:"))
async def cb_answer(call: CallbackQuery):
    _, qid, chosen = call.data.split(":")
    qid, chosen = int(qid), int(chosen)
    uid = call.from_user.id

    sess = await fetchone("SELECT * FROM sessions WHERE user_id=?", (uid,))
    if not sess:
        await call.answer("Test faol emas. Qaytadan boshlang.", show_alert=True)
        return
    qs = await fetchall("SELECT * FROM questions WHERE test_id=? ORDER BY question_id", (sess["test_id"],))
    idx = sess["q_index"]
    if idx >= len(qs) or qs[idx]["question_id"] != qid:
        await call.answer("Bu savolga allaqachon javob bergansiz.")
        return

    q = qs[idx]
    correct = chosen == q["correct_option"]
    score = sess["score"] + int(correct)
    idx += 1
    if correct:
        await call.answer("✅ To'g'ri!")
    else:
        await call.answer(f"❌ Noto'g'ri! To'g'ri javob: {LETTERS[q['correct_option']]}")

    test = await fetchone("SELECT * FROM tests WHERE test_id=?", (sess["test_id"],))
    total = len(qs)

    if idx < total:                                            # keyingi savol
        await execute("UPDATE sessions SET q_index=?, score=? WHERE user_id=?", (idx, score, uid))
        text, kb = await render_question(test, qs[idx], idx, total)
        await safe_edit(call.message, text, kb)
        return

    # --- Test tugadi: natijani saqlash va xulosa ---
    await execute("INSERT INTO results(user_id,test_id,score,total_questions,is_daily) VALUES(?,?,?,?,?)",
                  (uid, sess["test_id"], score, total, sess["is_daily"]))
    await execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    pct = round(score * 100 / total)
    grade = "🏆 Ajoyib natija!" if pct >= 86 else "👍 Yaxshi!" if pct >= 60 else "📚 Yana mashq qiling!"
    text = (f"🏁 <b>Test yakunlandi</b>\n{esc(test['title'])}\n\n"
            f"🎯 Ball: <b>{score}/{total}</b>\n"
            f"📈 Foiz: <b>{pct}%</b>\n"
            f"✅ To'g'ri: <b>{score}</b>\n"
            f"❌ Noto'g'ri: <b>{total - score}</b>\n\n{grade}")
    kb = InlineKeyboardBuilder()
    if not sess["is_daily"]:
        kb.row(InlineKeyboardButton(text="🔁 Qayta yechish", callback_data=f"begin:{test['test_id']}"))
    kb.row(InlineKeyboardButton(text="📚 Boshqa testlar", callback_data="cats"))
    await safe_edit(call.message, text, kb.as_markup())


# --------------------------------------------------------------------------- #
# Admin: /addtest
# --------------------------------------------------------------------------- #
ADDTEST_HELP = (
    "Format:\n<pre>/addtest\nSarlavha | Fan | premium(0/1)\n"
    "Savol | Variant A | Variant B | Variant C | Variant D | to'g'ri(1-4)\n"
    "Savol | ...</pre>")


@router.message(Command("addtest"))
async def cmd_addtest(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Bu buyruq faqat adminlar uchun.")
        return
    lines = [l.strip() for l in (message.text or "").split("\n")[1:] if l.strip()]
    try:
        title, cat, prem = [p.strip() for p in lines[0].split("|")]
        questions = []
        for line in lines[1:]:
            p = [x.strip() for x in line.split("|")]
            opts, correct = p[1:-1], int(p[-1]) - 1
            if not (2 <= len(opts) <= 4 and 0 <= correct < len(opts)):
                raise ValueError
            questions.append((p[0], opts, correct))
        if not questions:
            raise ValueError
    except Exception:
        await message.answer("❌ Format xato.\n" + ADDTEST_HELP)
        return
    code = await create_test(title, cat, message.from_user.id, int(prem == "1"), questions)
    await message.answer(f"✅ Test yaratildi. Kod: <code>#{code}</code> ({len(questions)} savol)")


# --------------------------------------------------------------------------- #
# Veb-server: Mini App + JSON API
# --------------------------------------------------------------------------- #
async def index(_):
    return web.FileResponse(BASE_DIR / "webapp" / "index.html")


async def health(_):
    return web.Response(text="ok")


async def api_config(_):
    return web.json_response({"bot_username": BOT_USERNAME,
                              "referrals_required": REFERRALS_REQUIRED})


async def api_categories(_):
    counts = {r["category"]: r["n"] for r in await fetchall(
        "SELECT category, COUNT(*) n FROM tests GROUP BY category")}
    return web.json_response([{"name": n, "icon": i, "count": counts.get(n, 0)} for n, i in CATEGORIES])


async def api_tests(request: web.Request):
    cat = request.query.get("category", "").strip()
    q = request.query.get("q", "").strip().lstrip("#")[:40]
    sql = ("SELECT t.code, t.title, t.category, t.is_premium, "
           "(SELECT COUNT(*) FROM questions WHERE test_id=t.test_id) AS questions, "
           "(SELECT COUNT(*) FROM results WHERE test_id=t.test_id) AS attempts "
           "FROM tests t WHERE 1=1")
    args: list = []
    if cat:
        sql += " AND t.category=?"
        args.append(cat)
    if q:
        sql += " AND (t.title LIKE ? OR t.code LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY attempts DESC, t.test_id DESC LIMIT 100"
    rows = [dict(r) for r in await fetchall(sql, args)]
    return web.json_response([r for r in rows if r["questions"] > 0])


async def api_leaderboard(_):
    rows = await fetchall(
        "SELECT u.full_name AS name, SUM(r.score) AS correct, COUNT(r.result_id) AS tests "
        "FROM results r JOIN users u ON u.user_id=r.user_id "
        "GROUP BY u.user_id ORDER BY correct DESC, tests DESC LIMIT 20")
    return web.json_response([dict(r) for r in rows])


def build_web_app() -> web.Application:
    app = web.Application()
    app.add_routes([
        web.get("/", index), web.get("/health", health),
        web.get("/api/config", api_config), web.get("/api/categories", api_categories),
        web.get("/api/tests", api_tests), web.get("/api/leaderboard", api_leaderboard),
    ])
    return app


# --------------------------------------------------------------------------- #
# Ishga tushirish
# --------------------------------------------------------------------------- #
async def main():
    global BOT_USERNAME
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN o'rnatilmagan!")
    await init_db()

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    BOT_USERNAME = (await bot.get_me()).username

    runner = web.AppRunner(build_web_app())
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("Veb-server :%s portda, bot @%s", PORT, BOT_USERNAME)

    await bot.set_my_commands([BotCommand(command="start", description="Botni ishga tushirish"),
                               BotCommand(command="help", description="Yordam")])
    if WEBAPP_URL.startswith("https://"):
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(
            text="Testlar", web_app=WebAppInfo(url=WEBAPP_URL)))

    dp = Dispatcher()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await db.close()
        await bot.session.close()

@router.callback_query(F.data == "check_sub")
async def process_check_sub(callback: CallbackQuery, bot: Bot):
  is_subscribed = await check_subscription(callback.from_user.id, bot)

  if is_subscribed:
    await callback.message.answer(
        "Rahmat! Obunangiz tasdiqlandi. Endi /start buyrug'ini yuboring."
    )
    await callback.message.delete()
  else:
    await callback.answer(
        "❌ Siz hali kanalga a'zo bo'lmadingiz!", show_alert=True
    )
if __name__ == "__main__":
  asyncio.run(main())
