import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    Message,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo
)
from aiohttp import web

# Tokeningiz
TOKEN = "7910123659:AAGkS615fQ4uKz7Z9o9V9O8p6mZ8xQ2v1nQ"

bot = Bot(token=TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# --- Veb-server (Render va UptimeRobot uchun 404 chiqmasligi uchun) ---
async def handle_root(request):
    return web.Response(text="Milliy Test Web App is running successfully!", content_type="text/html")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_root)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 10000)
    await site.start()

# --- Asosiy menyu klaviaturasi ---
def main_menu_kb():
    keyboard = [
        [KeyboardButton(text="📚 Testlar"), KeyboardButton(text="📅 Kunlik test")],
        [KeyboardButton(text="🎁 Taklif"), KeyboardButton(text="📊 Holat")],
        [KeyboardButton(text="➕ Guruhga qo'shish"), KeyboardButton(text="ℹ️ Yordam")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

# /start buyrug'i
@router.message(Command("start"))
async def cmd_start(message: Message):
    welcome_text = f"👋 Assalom alaykum, {message.from_user.first_name}!\n\n🤖 <b>MILLIY TESTLAR</b> botiga xush kelibsiz!"
    await message.answer(welcome_text, reply_markup=main_menu_kb())

# 📚 Testlar tugmasi
@router.message(F.text == "📚 Testlar")
async def on_tests_menu(message: Message):
    web_app_btn = InlineKeyboardButton(
        text="🌐 Katalog ochish (Web App)",
        web_app=WebAppInfo(url="https://milliytest.onrender.com")
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[[web_app_btn]])
    await message.answer(
        "<b>📚 Fanlar katalogi</b>\n\n"
        "Test ishlamoqchi bo'lgan fanni tanlang yoki test kodini yuboring.",
        reply_markup=markup
    )

# 📅 Kunlik test tugmasi
@router.message(F.text == "📅 Kunlik test")
async def on_daily_test(message: Message):
    await message.answer("📅 Bugungi kunlik test yuklanmoqda... Tayyor turing!")

# 🎁 Taklif tugmasi
@router.message(F.text == "🎁 Taklif")
async def on_referral(message: Message):
    bot_info = await message.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{message.from_user.id}"
    await message.answer(
        f"<b>🎁 Do'stlaringizni taklif qiling!</b>\n\n"
        f"Maxsus havolangiz orqali do'stlaringizni botga taklif qiling va ballar jamg'aring:\n\n"
        f"<code>{ref_link}</code>"
    )

# 📊 Holat tugmasi
@router.message(F.text == "📊 Holat")
async def on_status(message: Message):
    await message.answer(
        f"<b>📊 Sizning statistikangiz:</b>\n\n"
        f"👤 Ism: {message.from_user.first_name}\n"
        f"🏆 To'plangan ballar: 0 ball\n"
        f"📝 Ishlangan testlar: 0 ta"
    )

# ➕ Guruhga qo'shish tugmasi
@router.message(F.text == "➕ Guruhga qo'shish")
async def on_add_to_group(message: Message):
    bot_info = await message.bot.get_me()
    group_link = f"https://t.me/{bot_info.username}?startgroup=true"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Guruhga qo'shish", url=group_link)]
    ])
    await message.answer(
        "🤖 Botni guruhga qo'shing va do'stlaringiz bilan birgalikda test yeching!",
        reply_markup=kb
    )

# ℹ️ Yordam tugmasi
@router.message(F.text == "ℹ️ Yordam")
async def on_help_menu(message: Message):
    await message.answer(
        "<b>ℹ️ Yordam markazi</b>\n\n"
        "Agar botdan foydalanishda savollar yoki xatoliklar yuzaga kelsa, adminga murojaat qiling."
    )

async def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
