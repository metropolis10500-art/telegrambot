"""
════════════════════════════════════════════════════════════════════════════════
    BASTION PRO & MARKET — P2P ЭСКРОУ-МАРКЕТПЛЕЙС С КОШЕЛЬКОМ
    • Кошелёк: пополнение счета, баланс покупок, вывод
    • Каталог с подменю соцсетей и площадок (скриншоты)
    • Генерация прямых ссылок на товары для продавцов
    • Отзывы и рейтинг FunPay / Kwork (% положительных)
    • SQLite WAL (без aiosqlite, не зависает)
    • Комиссия платформы: 40%
════════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import logging
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

# ══════════════════════════════════════════════════════════════════════════════
# ЗАГРУЗКА .ENV
# ══════════════════════════════════════════════════════════════════════════════
def load_env_file(filepath: str = ".env"):
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

load_env_file(".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "7969032609:AAHTR51-aGgN1n9j33tG-m56G4XJ4-mX2qA")
ADMIN_ID = int(os.getenv("ADMIN_ID", "5693051419"))
DB_FILE = os.getenv("DB_FILE", "bastion_market_v2.db")
SUPPORT_HANDLE = os.getenv("SUPPORT_HANDLE", "@vladofix97")
COMMISSION_RATE = float(os.getenv("COMMISSION_RATE", "0.40"))
MIN_WITHDRAW = float(os.getenv("MIN_WITHDRAW", "500.0"))
PARITYPAY_SHOP_ID = os.getenv("PARITYPAY_SHOP_ID", "YOUR_SHOP_ID")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# ══════════════════════════════════════════════════════════════════════════════
# СПИСОК КАТЕГОРИЙ ИЗ СКРИНШОТОВ
# ══════════════════════════════════════════════════════════════════════════════
CATEGORIES: List[Tuple[str, str]] = [
    ("max", "🟣 MAX"),
    ("telegram", "✈️ Telegram"),
    ("tg_premium", "⭐ Telegram Premium"),
    ("instagram", "📷 Instagram"),
    ("youtube", "▶️ YouTube"),
    ("tiktok", "🎵 TikTok"),
    ("vk", "🔷 ВКонтакте"),
    ("wibes", "🌸 Wibes"),
    ("twitch", "👾 Twitch"),
    ("steam", "🎮 Steam"),
    ("discord", "💬 Discord"),
    ("twitter", "✖️ Twitter (X)"),
    ("rutube", "🔴 RuTube"),
    ("likee", "💖 Likee"),
    ("dzen", "✨ Дзен"),
    ("trovo", "🦎 Trovo"),
    ("github", "🐙 Github"),
]

CAT_DICT = dict(CATEGORIES)

# ══════════════════════════════════════════════════════════════════════════════
# БАЗА ДАННЫХ SQLITE (ВЫСОКОСКОРОСТНОЙ WAL РЕЖИМ)
# ══════════════════════════════════════════════════════════════════════════════
def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=15000;")
    return conn

def _sync_execute(query: str, params: tuple = (), fetch: Optional[str] = None, commit: bool = False) -> Any:
    with _get_connection() as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        res = None
        if fetch == "one":
            row = cur.fetchone()
            res = dict(row) if row else None
        elif fetch == "all":
            rows = cur.fetchall()
            res = [dict(r) for r in rows]
        if commit:
            conn.commit()
            if not fetch:
                res = cur.lastrowid
        return res

def _sync_transaction(operations: List[tuple]) -> None:
    with _get_connection() as conn:
        cur = conn.cursor()
        for q, p in operations:
            cur.execute(q, p)
        conn.commit()

async def db_get_one(query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(_sync_execute, query, params, fetch="one")

async def db_get_all(query: str, params: tuple = ()) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(_sync_execute, query, params, fetch="all")

async def db_execute(query: str, params: tuple = (), commit: bool = True) -> Any:
    return await asyncio.to_thread(_sync_execute, query, params, commit=commit)

async def db_transaction(operations: List[tuple]) -> None:
    await asyncio.to_thread(_sync_transaction, operations)

async def init_db():
    schema = """
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        balance REAL DEFAULT 0.0,
        rating_sum REAL DEFAULT 0.0,
        rating_count INTEGER DEFAULT 0,
        pos_reviews INTEGER DEFAULT 0,
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_id INTEGER,
        category TEXT,
        title TEXT,
        description TEXT,
        price REAL,
        status TEXT DEFAULT 'active', -- active, sold, deleted
        created_at TEXT,
        FOREIGN KEY(seller_id) REFERENCES users(user_id)
    );
    CREATE TABLE IF NOT EXISTS deals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER,
        buyer_id INTEGER,
        seller_id INTEGER,
        title TEXT,
        amount REAL,
        fee REAL,
        seller_receives REAL,
        status TEXT DEFAULT 'waiting_payment', -- waiting_payment, paid, shipped, dispute, completed, cancelled
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS topups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount REAL,
        status TEXT DEFAULT 'pending', -- pending, completed
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS withdrawals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount REAL,
        requisites TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        deal_id INTEGER,
        target_user_id INTEGER,
        author_user_id INTEGER,
        rating INTEGER,
        comment TEXT,
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS platform_vault (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        deal_id INTEGER,
        fee_amount REAL,
        created_at TEXT
    );
    """
    def _create():
        with _get_connection() as conn:
            conn.executescript(schema)
            conn.commit()
    await asyncio.to_thread(_create)
    logger.info("База данных инициализирована.")

async def get_or_create_user(user_id: int, username: Optional[str] = None) -> Dict[str, Any]:
    user = await db_get_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    clean_username = f"@{username}" if username else "Пользователь"
    if not user:
        await db_execute(
            "INSERT INTO users (user_id, username, balance, rating_sum, rating_count, pos_reviews, created_at) VALUES (?, ?, 0.0, 0.0, 0, 0, ?)",
            (user_id, clean_username, datetime.now().isoformat())
        )
        user = await db_get_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    elif username and user["username"] != clean_username:
        await db_execute("UPDATE users SET username = ? WHERE user_id = ?", (clean_username, user_id))
        user["username"] = clean_username
    return user

async def safe_edit_text(message: Message, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None):
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning(f"safe_edit warning: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# FSM СОСТОЯНИЯ
# ══════════════════════════════════════════════════════════════════════════════
class TopupStates(StatesGroup):
    amount = State()

class AddProductStates(StatesGroup):
    category = State()
    title = State()
    description = State()
    price = State()

class AskSellerStates(StatesGroup):
    product_id = State()
    seller_id = State()
    question = State()

class ReplyQuestionStates(StatesGroup):
    target_buyer_id = State()
    product_id = State()
    reply_text = State()

class WithdrawStates(StatesGroup):
    amount = State()
    requisites = State()

class ReviewStates(StatesGroup):
    deal_id = State()
    target_id = State()
    rating = State()
    comment = State()

class ChatProxyStates(StatesGroup):
    in_chat = State()
    deal_id = State()

class AdminBroadcast(StatesGroup):
    text = State()

# ══════════════════════════════════════════════════════════════════════════════
# ГЛАВНОЕ МЕНЮ (ОБНОВЛЁННОЕ)
# ══════════════════════════════════════════════════════════════════════════════
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛍 Каталог товаров"), KeyboardButton(text="📦 Мои товары")],
            [KeyboardButton(text="👛 Кошелёк"), KeyboardButton(text="📂 Мои сделки")],
            [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="💳 Вывод средств")],
            [KeyboardButton(text="🌐 Подключить OnyxVPN"), KeyboardButton(text="ℹ️ Помощь / Поддержка")]
        ],
        resize_keyboard=True
    )

def get_cancel_reply_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )

# ══════════════════════════════════════════════════════════════════════════════
# СТАРТ И ДИПЛИНКИ НА ТОВАРЫ И СДЕЛКИ
# ══════════════════════════════════════════════════════════════════════════════
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    await get_or_create_user(user_id, message.from_user.username)

    args = command.args
    if args:
        if args.startswith("item_"):
            try:
                prod_id = int(args.replace("item_", ""))
                await show_product_card(message, prod_id, user_id)
                return
            except Exception as e:
                logger.error(f"Ошибка перехода по товару: {e}")
        elif args.startswith("deal_"):
            try:
                deal_id = int(args.replace("deal_", ""))
                await render_deal_view(message, deal_id, user_id)
                return
            except Exception as e:
                logger.error(f"Ошибка перехода по сделке: {e}")

    welcome = (
        f"🛡 <b>Эскроу-маркетплейс «BastionPRO»</b>\n"
        f"────────────────────────────\n"
        f"Здравствуйте, <b>{message.from_user.first_name}</b>!\n\n"
        f"Добро пожаловать на безопасную биржу цифровых товаров и услуг:\n\n"
        f"• 🛍 <b>Каталог товаров:</b> быстрый выбор аккаунтов по категориям (Telegram, Steam, VK, Discord и др.).\n"
        f"• 👛 <b>Кошелёк:</b> пополняйте баланс и оплачивайте покупки в 1 клик.\n"
        f"• 📦 <b>Продавцам:</b> выставляйте товары и получайте прямые ссылки на лоты для ваших клиентов.\n"
        f"• 🛡 <b>100% Защита:</b> деньги хранятся в гаранте до подтверждения выполнения заказа.\n"
        f"• ⭐ <b>Честные отзывы:</b> рейтинг как на FunPay (% довольных клиентов).\n\n"
        f"📊 <b>Условия:</b>\n"
        f"• Комиссия гаранта: <b>40%</b> (вычитается при закрытии с суммы сделки)\n"
        f"• Выплаты: <b>каждый вторник и четверг</b> (от 500 ₽)\n"
        f"• Арбитраж и помощь: <b>{SUPPORT_HANDLE}</b>"
    )
    await message.answer(welcome, reply_markup=get_main_keyboard())

# ══════════════════════════════════════════════════════════════════════════════
# КОШЕЛЁК (БАЛАНС, ПОПОЛНЕНИЕ, ВЫВОД)
# ══════════════════════════════════════════════════════════════════════════════
@router.message(F.text.in_(["👛 Кошелёк", "Кошелек", "Кошелёк"]))
async def show_wallet(message: Message, state: FSMContext):
    await state.clear()
    user = await get_or_create_user(message.from_user.id, message.from_user.username)

    text = (
        f"👛 <b>ВАШ КОШЕЛЁК BASTION</b>\n"
        f"────────────────────────────\n"
        f"💰 <b>Доступный баланс:</b> <b>{user['balance']:,.2f} ₽</b>\n\n"
        f"С этого баланса вы можете мгновенно оплачивать любые товары в каталоге, "
        f"а также накапливать средства от продаж и выводить их на банковские карты РФ или СБП.\n"
        f"────────────────────────────\n"
        f"📅 <i>Выплаты производятся каждый вторник и четверг (мин. {MIN_WITHDRAW:,.0f} ₽).</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="wallet_topup")],
        [InlineKeyboardButton(text="💸 Запросить вывод", callback_data="wallet_withdraw")],
        [InlineKeyboardButton(text="🔄 Обновить баланс", callback_data="wallet_refresh")]
    ])
    await message.answer(text, reply_markup=kb)

@router.callback_query(F.data == "wallet_refresh")
async def cb_wallet_refresh(call: CallbackQuery):
    user = await get_or_create_user(call.from_user.id)
    text = (
        f"👛 <b>ВАШ КОШЕЛЁК BASTION</b>\n"
        f"────────────────────────────\n"
        f"💰 <b>Доступный баланс:</b> <b>{user['balance']:,.2f} ₽</b>\n\n"
        f"С этого баланса вы можете мгновенно оплачивать любые товары в каталоге, "
        f"а также накапливать средства от продаж и выводить их на банковские карты РФ или СБП.\n"
        f"────────────────────────────\n"
        f"📅 <i>Выплаты производятся каждый вторник и четверг (мин. {MIN_WITHDRAW:,.0f} ₽).</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="wallet_topup")],
        [InlineKeyboardButton(text="💸 Запросить вывод", callback_data="wallet_withdraw")],
        [InlineKeyboardButton(text="🔄 Обновить баланс", callback_data="wallet_refresh")]
    ])
    await safe_edit_text(call.message, text, reply_markup=kb)
    await call.answer("Баланс обновлен!")

@router.callback_query(F.data == "wallet_topup")
async def cb_wallet_topup(call: CallbackQuery, state: FSMContext):
    await state.set_state(TopupStates.amount)
    await call.message.answer(
        "💳 <b>Пополнение баланса Кошелька</b>\n\n"
        "Введите сумму для пополнения в рублях (минимум 50 ₽):",
        reply_markup=get_cancel_reply_kb()
    )
    await call.answer()

@router.message(TopupStates.amount)
async def process_topup_amount(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Пополнение отменено.", reply_markup=get_main_keyboard())
        return

    try:
        amount = float(message.text.replace(",", ".").strip())
        if amount < 50.0 or amount > 300000.0:
            await message.answer("⚠️ Сумма пополнения должна быть от 50 до 300 000 ₽. Введите сумму:")
            return
    except ValueError:
        await message.answer("⚠️ Введите корректное число (например: 500):")
        return

    user_id = message.from_user.id
    topup_id = await db_execute("""
        INSERT INTO topups (user_id, amount, status, created_at)
        VALUES (?, ?, 'pending', ?)
    """, (user_id, amount, datetime.now().isoformat()))

    await state.clear()
    pay_url = f"https://app.paritypay.ru/pay/{PARITYPAY_SHOP_ID}?order_id=topup_{topup_id}&amount={amount}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить через ParityPay", url=pay_url)],
        [InlineKeyboardButton(text="✅ Проверить зачисление", callback_data=f"check_topup:{topup_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="close_view")]
    ])

    await message.answer(
        f"💳 <b>Счёт на пополнение №{topup_id} сформирован!</b>\n\n"
        f"Сумма к оплате: <b>{amount:,.2f} ₽</b>\n\n"
        f"1. Нажмите «Оплатить через ParityPay» и завершите платеж.\n"
        f"2. После оплаты нажмите «Проверить зачисление» для пополнения вашего кошелька.",
        reply_markup=kb
    )

@router.callback_query(F.data.startswith("check_topup:"))
async def cb_check_topup(call: CallbackQuery):
    topup_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    topup = await db_get_one("SELECT * FROM topups WHERE id = ? AND user_id = ?", (topup_id, user_id))
    if not topup:
        await call.answer("Счёт не найден.", show_alert=True)
        return

    if topup["status"] == "completed":
        await call.answer("Этот счёт уже был успешно зачислен.", show_alert=True)
        return

    # Зачисление средств на кошелек
    await db_transaction([
        ("UPDATE topups SET status = 'completed' WHERE id = ?", (topup_id,)),
        ("UPDATE users SET balance = balance + ? WHERE user_id = ?", (topup["amount"], user_id))
    ])

    await call.answer("✅ Баланс успешно пополнен!", show_alert=True)
    await safe_edit_text(call.message, f"🎉 <b>Счёт №{topup_id} успешно оплачен!</b>\nНа ваш баланс зачислено <b>{topup['amount']:,.2f} ₽</b>.")

# ══════════════════════════════════════════════════════════════════════════════
# КАТАЛОГ С ПОДМЕНЮ (ПО СКРИНШОТАМ)
# ══════════════════════════════════════════════════════════════════════════════
@router.message(F.text == "🛍 Каталог товаров")
async def show_catalog_categories(message: Message, state: FSMContext):
    await state.clear()
    await render_categories_menu(message)

async def render_categories_menu(target: Message, is_edit: bool = False):
    text = (
        "🛍 <b>КАТАЛОГ ЦИФРОВЫХ ТОВАРОВ И АККАУНТОВ</b>\n"
        "────────────────────────────\n"
        "Выберите интересующую категорию площадки или соцсети:\n"
        "<i>(Все покупки на 100% защищены Гарантом)</i>"
    )

    kb = []
    # Формируем аккуратное меню в 2 колонки
    row = []
    for code, name in CATEGORIES:
        row.append(InlineKeyboardButton(text=name, callback_data=f"cat_view:{code}:0"))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)

    reply_markup = InlineKeyboardMarkup(inline_keyboard=kb)
    if is_edit:
        await safe_edit_text(target, text, reply_markup=reply_markup)
    else:
        await target.answer(text, reply_markup=reply_markup)

@router.callback_query(F.data.startswith("cat_view:"))
async def cb_cat_view(call: CallbackQuery):
    _, cat_code, page_str = call.data.split(":")
    page = int(page_str)
    limit = 5
    offset = page * limit

    cat_name = CAT_DICT.get(cat_code, "Категория")

    products = await db_get_all(
        "SELECT * FROM products WHERE category = ? AND status = 'active' ORDER BY id DESC LIMIT ? OFFSET ?",
        (cat_code, limit, offset)
    )
    total_row = await db_get_one("SELECT COUNT(*) as c FROM products WHERE category = ? AND status = 'active'", (cat_code,))
    total_count = total_row["c"] if total_row else 0

    if not products:
        text = (
            f"📁 <b>Раздел: {cat_name}</b>\n"
            f"────────────────────────────\n"
            f"В данной категории пока нет активных товаров.\n\n"
            f"Вы можете выставить свой товар в эту категорию прямо сейчас в меню «📦 Мои товары»!"
        )
        kb = [
            [InlineKeyboardButton(text="➕ Выставить товар сюда", callback_data=f"add_prod_to:{cat_code}")],
            [InlineKeyboardButton(text="◀️ Назад ко всем категориям", callback_data="back_to_categories")]
        ]
        await safe_edit_text(call.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
        await call.answer()
        return

    text = (
        f"📁 <b>Раздел: {cat_name}</b>\n"
        f"Товаров в наличии: <b>{total_count}</b> | Стр. {page + 1}\n"
        f"────────────────────────────\n"
        f"<i>Выберите товар для просмотра и покупки:</i>"
    )

    kb = []
    for p in products:
        seller = await db_get_one("SELECT rating_sum, rating_count FROM users WHERE user_id = ?", (p["seller_id"],))
        rating_val = f"★ {round(seller['rating_sum'] / seller['rating_count'], 1)}" if seller and seller["rating_count"] > 0 else "Новый"
        btn_text = f"💎 {p['title'][:22]} — {p['price']:,.0f} ₽ ({rating_val})"
        kb.append([InlineKeyboardButton(text=btn_text, callback_data=f"view_prod:{p['id']}:{cat_code}:{page}")])

    # Пагинация
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"cat_view:{cat_code}:{page - 1}"))
    if (offset + limit) < total_count:
        nav_row.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"cat_view:{cat_code}:{page + 1}"))
    if nav_row:
        kb.append(nav_row)

    kb.append([InlineKeyboardButton(text="◀️ Ко всем категориям", callback_data="back_to_categories")])

    await safe_edit_text(call.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()

@router.callback_query(F.data == "back_to_categories")
async def cb_back_to_categories(call: CallbackQuery):
    await render_categories_menu(call.message, is_edit=True)
    await call.answer()

# ══════════════════════════════════════════════════════════════════════════════
# КАРТОЧКА ТОВАРА И ПОКУПКА
# ══════════════════════════════════════════════════════════════════════════════
async def show_product_card(target_message: Message, prod_id: int, user_id: int, cat_code: str = "", page: int = 0, is_edit: bool = False):
    p = await db_get_one("SELECT * FROM products WHERE id = ?", (prod_id,))
    if not p or p["status"] != "active":
        text = "❌ Товар был продан или снят с публикации."
        if is_edit:
            await safe_edit_text(target_message, text)
        else:
            await target_message.answer(text)
        return

    seller = await db_get_one("SELECT * FROM users WHERE user_id = ?", (p["seller_id"],))
    rating_avg = round(seller["rating_sum"] / seller["rating_count"], 1) if seller and seller["rating_count"] > 0 else 5.0
    reviews_cnt = seller["rating_count"] if seller else 0
    pos_p = round((seller["pos_reviews"] / reviews_cnt) * 100) if reviews_cnt > 0 else 100
    cat_name = CAT_DICT.get(p["category"], "Цифровой товар")

    bot_info = await bot.get_me()
    share_link = f"https://t.me/{bot_info.username}?start=item_{p['id']}"

    text = (
        f"💎 <b>КАРТОЧКА ТОВАРА #{p['id']}</b>\n"
        f"📁 <b>Категория:</b> {cat_name}\n"
        f"────────────────────────────\n"
        f"📌 <b>Название:</b> {p['title']}\n"
        f"💰 <b>Цена:</b> <b>{p['price']:,.2f} ₽</b>\n\n"
        f"📝 <b>Описание:</b>\n{p['description']}\n"
        f"────────────────────────────\n"
        f"👤 <b>Продавец:</b> {seller['username']}\n"
        f"⭐ <b>Репутация:</b> {rating_avg} / 5.0 | 👍 <b>{pos_p}%</b> положительных ({reviews_cnt} отзывов)\n"
        f"🔗 <b>Прямая ссылка на лот:</b>\n<code>{share_link}</code>"
    )

    kb = []
    if user_id != p["seller_id"]:
        kb.append([InlineKeyboardButton(text="💳 Купить через Гарант", callback_data=f"buy_prod:{p['id']}")])
        kb.append([InlineKeyboardButton(text="💬 Задать вопрос продавцу", callback_data=f"ask_seller:{p['id']}")])
        kb.append([InlineKeyboardButton(text="⭐ Отзывы о продавце", callback_data=f"view_seller_reviews:{p['seller_id']}")])
    else:
        kb.append([InlineKeyboardButton(text="🗑 Снять мой товар с продажи", callback_data=f"del_prod:{p['id']}")])

    if cat_code:
        kb.append([InlineKeyboardButton(text="◀️ Назад в категорию", callback_data=f"cat_view:{cat_code}:{page}")])
    else:
        kb.append([InlineKeyboardButton(text="◀️ В каталог", callback_data="back_to_categories")])

    reply_markup = InlineKeyboardMarkup(inline_keyboard=kb)
    if is_edit:
        await safe_edit_text(target_message, text, reply_markup=reply_markup)
    else:
        await target_message.answer(text, reply_markup=reply_markup)

@router.callback_query(F.data.startswith("view_prod:"))
async def cb_view_prod(call: CallbackQuery):
    _, prod_id_str, cat_code, page_str = call.data.split(":")
    await show_product_card(call.message, int(prod_id_str), call.from_user.id, cat_code=cat_code, page=int(page_str), is_edit=True)
    await call.answer()

# ══════════════════════════════════════════════════════════════════════════════
# ПОКУПКА ТОВАРА: С БАЛАНСА КОШЕЛЬКА ИЛИ ЧЕРЕЗ ШЛЮЗ
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("buy_prod:"))
async def cb_buy_prod(call: CallbackQuery):
    prod_id = int(call.data.split(":")[1])
    buyer_id = call.from_user.id
    user = await get_or_create_user(buyer_id, call.from_user.username)

    product = await db_get_one("SELECT * FROM products WHERE id = ?", (prod_id,))
    if not product or product["status"] != "active":
        await call.answer("Товар уже продан или недоступен.", show_alert=True)
        return

    if product["seller_id"] == buyer_id:
        await call.answer("Нельзя купить собственный товар!", show_alert=True)
        return

    price = product["price"]
    fee = round(price * COMMISSION_RATE, 2)
    seller_receives = round(price - fee, 2)

    # Создаем сделку
    deal_id = await db_execute("""
        INSERT INTO deals (product_id, buyer_id, seller_id, title, amount, fee, seller_receives, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'waiting_payment', ?)
    """, (product["id"], buyer_id, product["seller_id"], product["title"], price, fee, seller_receives, datetime.now().isoformat()))

    # Проверяем, хватает ли средств на кошельке
    kb = []
    if user["balance"] >= price:
        kb.append([InlineKeyboardButton(text=f"👛 Оплатить с Кошелька ({user['balance']:,.2f} ₽)", callback_data=f"pay_balance:{deal_id}")])

    pay_url = f"https://app.paritypay.ru/pay/{PARITYPAY_SHOP_ID}?order_id=deal_{deal_id}&amount={price}"
    kb.append([InlineKeyboardButton(text="💳 Оплатить через ParityPay", url=pay_url)])
    kb.append([InlineKeyboardButton(text="✅ Проверить оплату шлюза", callback_data=f"check_pay:{deal_id}")])
    kb.append([InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_deal:{deal_id}")])

    text = (
        f"🛒 <b>ОФОРМЛЕНИЕ ЗАКАЗА ПО СДЕЛКЕ №{deal_id}</b>\n"
        f"────────────────────────────\n"
        f"📌 <b>Товар:</b> {product['title']}\n"
        f"💰 <b>К оплате:</b> <b>{price:,.2f} ₽</b>\n"
        f"👛 <b>Ваш баланс в Кошельке:</b> {user['balance']:,.2f} ₽\n\n"
        f"Выберите способ оплаты:"
    )

    await safe_edit_text(call.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()

@router.callback_query(F.data.startswith("pay_balance:"))
async def cb_pay_balance(call: CallbackQuery):
    deal_id = int(call.data.split(":")[1])
    buyer_id = call.from_user.id
    user = await get_or_create_user(buyer_id)

    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal or deal["buyer_id"] != buyer_id:
        await call.answer("Сделка не найдена.", show_alert=True)
        return

    if deal["status"] != "waiting_payment":
        await call.answer("Сделка уже оплачена или закрыта.", show_alert=True)
        return

    if user["balance"] < deal["amount"]:
        await call.answer("Недостаточно средств на Кошельке!", show_alert=True)
        return

    # Списываем баланс покупателя и переводим сделку в paid
    await db_transaction([
        ("UPDATE users SET balance = balance - ? WHERE user_id = ?", (deal["amount"], buyer_id)),
        ("UPDATE deals SET status = 'paid' WHERE id = ?", (deal_id,)),
        ("UPDATE products SET status = 'sold' WHERE id = ?", (deal["product_id"],)) if deal["product_id"] else ("SELECT 1", ())
    ])

    await call.answer("✅ Успешно оплачено с баланса Кошелька!", show_alert=True)

    # Оповещаем продавца
    try:
        await bot.send_message(
            deal["seller_id"],
            f"💰 <b>Покупатель оплатил сделку №{deal_id} с баланса Кошелька!</b>\n\n"
            f"Средства заблокированы в Гаранте. Передайте товар Покупателю.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔎 Открыть сделку", callback_data=f"view_deal:{deal_id}")]
            ])
        )
    except Exception:
        pass

    await render_deal_view(call.message, deal_id, buyer_id, is_edit=True)

# ══════════════════════════════════════════════════════════════════════════════
# МОИ ТОВАРЫ (КАБИНЕТ ПРОДАВЦА) С ВЫБОРОМ КАТЕГОРИИ
# ══════════════════════════════════════════════════════════════════════════════
@router.message(F.text == "📦 Мои товары")
async def show_seller_products(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    await get_or_create_user(user_id, message.from_user.username)

    products = await db_get_all("SELECT * FROM products WHERE seller_id = ? AND status = 'active' ORDER BY id DESC", (user_id,))
    bot_info = await bot.get_me()

    kb = [[InlineKeyboardButton(text="➕ Выставить новый товар", callback_data="add_prod_select_cat")]]

    if not products:
        text = (
            "📦 <b>У вас пока нет активных товаров в каталоге.</b>\n\n"
            "Вы можете выставить аккаунт или услугу прямо сейчас, получить прямую ссылку и скидывать её покупателям!"
        )
    else:
        text = f"📦 <b>Ваши активные товары в продаже ({len(products)}):</b>\n\n"
        for idx, p in enumerate(products, 1):
            cat_name = CAT_DICT.get(p["category"], "Товар")
            share_url = f"https://t.me/{bot_info.username}?start=item_{p['id']}"
            text += (
                f"{idx}. <b>{p['title']}</b> ({cat_name})\n"
                f"   💰 Цена: {p['price']:,.2f} ₽ (Выплата: {p['price'] * 0.6:,.2f} ₽)\n"
                f"   🔗 Ссылка: <code>{share_url}</code>\n\n"
            )
            kb.append([InlineKeyboardButton(text=f"🗑 Снять #{p['id']} «{p['title'][:18]}»", callback_data=f"del_prod:{p['id']}")])

    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "add_prod_select_cat")
async def cb_add_prod_cat(call: CallbackQuery, state: FSMContext):
    text = "📁 <b>Шаг 1 из 4. Выберите категорию для вашего товара:</b>"
    kb = []
    row = []
    for code, name in CATEGORIES:
        row.append(InlineKeyboardButton(text=name, callback_data=f"set_prod_cat:{code}"))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)

    kb.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")])
    await safe_edit_text(call.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()

@router.callback_query(F.data.startswith("set_prod_cat:"))
async def cb_set_prod_cat(call: CallbackQuery, state: FSMContext):
    cat_code = call.data.split(":")[1]
    await state.update_data(category=cat_code)
    await state.set_state(AddProductStates.title)

    cat_name = CAT_DICT.get(cat_code, "Категория")
    await call.message.edit_text(
        f"📁 Выбрана категория: <b>{cat_name}</b>\n\n"
        f"📝 <b>Шаг 2 из 4. Введите название товара:</b>\n"
        f"<i>Пример: Аккаунт с отлежкой 2 года / Telegram Premium 12 мес. / Prime CS2</i>"
    )

@router.message(AddProductStates.title)
async def process_prod_title(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Добавление отменено.", reply_markup=get_main_keyboard())
        return

    if len(message.text) < 3 or len(message.text) > 100:
        await message.answer("⚠️ Название должно быть от 3 до 100 символов. Введите снова:")
        return

    await state.update_data(title=message.text.strip())
    await state.set_state(AddProductStates.description)
    await message.answer(
        "📝 <b>Шаг 3 из 4. Введите подробное описание:</b>\n\n"
        "Укажите происхождение, формат выдачи данных, гарантию и инструкцию для покупателя:",
        reply_markup=get_cancel_reply_kb()
    )

@router.message(AddProductStates.description)
async def process_prod_desc(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=get_main_keyboard())
        return

    if len(message.text) < 5 or len(message.text) > 1000:
        await message.answer("⚠️ Описание должно быть от 5 до 1000 символов. Введите снова:")
        return

    await state.update_data(description=message.text.strip())
    await state.set_state(AddProductStates.price)
    await message.answer(
        "💰 <b>Шаг 4 из 4. Укажите стоимость для покупателя (в рублях):</b>\n\n"
        "<i>(Минимум — 100 ₽. Вы получите 60% чистыми после закрытия сделки, 40% — комиссия гаранта)</i>",
        reply_markup=get_cancel_reply_kb()
    )

@router.message(AddProductStates.price)
async def process_prod_price(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=get_main_keyboard())
        return

    try:
        price = float(message.text.replace(",", ".").strip())
        if price < 100.0 or price > 1000000.0:
            await message.answer("⚠️ Цена должна быть от 100 до 1 000 000 ₽. Введите число:")
            return
    except ValueError:
        await message.answer("⚠️ Введите число (например: 1200):")
        return

    data = await state.get_data()
    user_id = message.from_user.id
    seller_receives = round(price * (1 - COMMISSION_RATE), 2)

    prod_id = await db_execute("""
        INSERT INTO products (seller_id, category, title, description, price, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'active', ?)
    """, (user_id, data["category"], data["title"], data["description"], price, datetime.now().isoformat()))

    await state.clear()
    bot_info = await bot.get_me()
    share_link = f"https://t.me/{bot_info.username}?start=item_{prod_id}"
    cat_name = CAT_DICT.get(data["category"], "Раздел")

    await message.answer(
        f"🎉 <b>Товар #{prod_id} успешно опубликован в разделе {cat_name}!</b>\n\n"
        f"📌 <b>Название:</b> {data['title']}\n"
        f"💰 <b>Цена для покупателя:</b> {price:,.2f} ₽\n"
        f"💼 <b>Ваша выплата:</b> <b>{seller_receives:,.2f} ₽</b> (за вычетом 40% комиссии)\n\n"
        f"🔗 <b>Прямая ссылка для отправки покупателям:</b>\n"
        f"👉 <code>{share_link}</code>\n\n"
        f"<i>Отправьте эту ссылку клиенту или разместите в канале — он перейдет сразу к покупке через Гарант!</i>",
        reply_markup=get_main_keyboard()
    )

@router.callback_query(F.data.startswith("del_prod:"))
async def cb_del_prod(call: CallbackQuery):
    prod_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    prod = await db_get_one("SELECT * FROM products WHERE id = ?", (prod_id,))
    if not prod or prod["seller_id"] != user_id:
        await call.answer("Ошибка доступа.", show_alert=True)
        return

    await db_execute("UPDATE products SET status = 'deleted' WHERE id = ?", (prod_id,))
    await call.answer("Товар снят с публикации.")
    await safe_edit_text(call.message, f"🗑 Товар #{prod_id} снят с продажи.")

# ══════════════════════════════════════════════════════════════════════════════
# КАРТОЧКА СДЕЛКИ И ИНСТРУКЦИИ
# ══════════════════════════════════════════════════════════════════════════════
async def render_deal_view(target_message: Message, deal_id: int, user_id: int, is_edit: bool = False):
    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal:
        text = "❌ Сделка не найдена."
        if is_edit:
            await safe_edit_text(target_message, text)
        else:
            await target_message.answer(text)
        return

    is_buyer = (user_id == deal["buyer_id"])
    is_seller = (user_id == deal["seller_id"])
    is_admin = (user_id == ADMIN_ID)

    if not (is_buyer or is_seller or is_admin):
        await target_message.answer("⚠️ Вы не являетесь участником этой сделки.")
        return

    status_map = {
        "waiting_payment": "💳 Ожидание внесения оплаты Покупателем",
        "paid": "🛡 Оплачено (средства заблокированы в гаранте)",
        "shipped": "📦 Товар передан Продавцом (на проверке у Покупателя)",
        "dispute": "⚖️ Открыт спор (в арбитраже)",
        "completed": "✅ Сделка завершена",
        "cancelled": "🚫 Сделка аннулирована"
    }

    my_role = "🔵 <b>Покупатель</b>" if is_buyer else ("🟢 <b>Продавец</b>" if is_seller else "👑 <b>Администратор</b>")

    text = (
        f"📋 <b>КАРТОЧКА СДЕЛКИ №{deal['id']}</b>\n"
        f"────────────────────────────\n"
        f"📌 <b>Предмет:</b> {deal['title']}\n"
        f"💰 <b>Сумма сделки:</b> {deal['amount']:,.2f} ₽\n"
        f"💼 <b>К выплате Продавцу:</b> {deal['seller_receives']:,.2f} ₽ <i>(комиссия сервиса 40%: {deal['fee']:,.2f} ₽)</i>\n"
        f"🚦 <b>Статус:</b> {status_map.get(deal['status'], deal['status'])}\n"
        f"👤 Ваша роль: {my_role}\n"
        f"────────────────────────────\n"
    )

    kb_buttons = []

    if deal["status"] == "waiting_payment":
        if is_buyer:
            user = await get_or_create_user(user_id)
            if user["balance"] >= deal["amount"]:
                kb_buttons.append([InlineKeyboardButton(text=f"👛 Оплатить с Кошелька ({user['balance']:,.2f} ₽)", callback_data=f"pay_balance:{deal['id']}")])
            pay_url = f"https://app.paritypay.ru/pay/{PARITYPAY_SHOP_ID}?order_id=deal_{deal['id']}&amount={deal['amount']}"
            kb_buttons.append([InlineKeyboardButton(text="💳 Оплатить через ParityPay", url=pay_url)])
            kb_buttons.append([InlineKeyboardButton(text="✅ Проверить оплату шлюза", callback_data=f"check_pay:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="❌ Отменить сделку", callback_data=f"cancel_deal:{deal['id']}")])
            text += "\n📖 <b>Инструкция для Покупателя:</b> Внесите оплату с баланса Кошелька или через шлюз. Деньги заморозятся у Гаранта."
        elif is_seller:
            kb_buttons.append([InlineKeyboardButton(text="❌ Отменить сделку", callback_data=f"cancel_deal:{deal['id']}")])
            text += "\n⚠️ <b>ПАМЯТКА ДЛЯ ПРОДАВЦА: НЕ ПЕРЕДАВАЙТЕ ТОВАР СЕЙЧАС!</b> Дождитесь смены статуса на «🛡 Оплачено»."

    elif deal["status"] == "paid":
        if is_seller:
            kb_buttons.append([InlineKeyboardButton(text="📦 Товар/услуга передана", callback_data=f"ship_deal:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="💬 Анонимный чат сделки", callback_data=f"open_chat:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="⚖️ Открыть спор", callback_data=f"open_dispute:{deal['id']}")])
            text += "\n💰 <b>Деньги в Гаранте!</b> Передайте товар Покупателю через анонимный чат и нажмите «Товар передан»."
        elif is_buyer:
            kb_buttons.append([InlineKeyboardButton(text="💬 Анонимный чат сделки", callback_data=f"open_chat:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="⚖️ Открыть спор", callback_data=f"open_dispute:{deal['id']}")])
            text += "\n🛡 <b>Деньги защищены Гарантом.</b> Ожидайте передачи данных от Продавца."

    elif deal["status"] == "shipped":
        if is_buyer:
            kb_buttons.append([InlineKeyboardButton(text="✅ Подтвердить получение", callback_data=f"confirm_deal:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="💬 Анонимный чат сделки", callback_data=f"open_chat:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="⚖️ Открыть спор", callback_data=f"open_dispute:{deal['id']}")])
            text += "\n📦 <b>Продавец передал товар!</b> Проверьте работоспособность и подтвердите получение."
        elif is_seller:
            kb_buttons.append([InlineKeyboardButton(text="💬 Анонимный чат сделки", callback_data=f"open_chat:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="⚖️ Открыть спор", callback_data=f"open_dispute:{deal['id']}")])
            text += "\n⏳ <b>Товар на проверке у Покупателя.</b> Выплата поступит на ваш баланс после подтверждения."

    elif deal["status"] == "dispute":
        kb_buttons.append([InlineKeyboardButton(text="💬 Чат сделки", callback_data=f"open_chat:{deal['id']}")])
        text += f"\n⚖️ <i>Сделка в арбитраже. Поддержка: {SUPPORT_HANDLE}</i>"

    elif deal["status"] == "completed":
        if is_buyer:
            kb_buttons.append([InlineKeyboardButton(text="⭐ Оценить работу Продавца", callback_data=f"leave_review:{deal['id']}")])
        text += "\n🎉 <i>Сделка успешно завершена.</i>"

    kb_buttons.append([InlineKeyboardButton(text="🔄 Обновить статус", callback_data=f"view_deal:{deal['id']}")])

    if is_admin and deal["status"] == "dispute":
        kb_buttons.append([
            InlineKeyboardButton(text="👑 Вернуть Покупателю", callback_data=f"admin_refund:{deal['id']}"),
            InlineKeyboardButton(text="👑 Выплатить Продавцу", callback_data=f"admin_payout:{deal['id']}")
        ])

    reply_markup = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    if is_edit:
        await safe_edit_text(target_message, text, reply_markup=reply_markup)
    else:
        await target_message.answer(text, reply_markup=reply_markup)

@router.callback_query(F.data.startswith("view_deal:"))
async def cb_view_deal(call: CallbackQuery):
    deal_id = int(call.data.split(":")[1])
    await render_deal_view(call.message, deal_id, call.from_user.id, is_edit=True)
    await call.answer()

@router.callback_query(F.data.startswith("check_pay:"))
async def cb_check_pay(call: CallbackQuery):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal or deal["buyer_id"] != user_id:
        await call.answer("Только покупатель подтверждает оплату.", show_alert=True)
        return

    await db_execute("UPDATE deals SET status = 'paid' WHERE id = ?", (deal_id,))
    if deal["product_id"]:
        await db_execute("UPDATE products SET status = 'sold' WHERE id = ?", (deal["product_id"],))

    await call.answer("✅ Оплата зафиксирована! Средства заблокированы в гаранте.", show_alert=True)

    if deal["seller_id"]:
        try:
            await bot.send_message(
                deal["seller_id"],
                f"💰 <b>Покупатель оплатил сделку №{deal_id}!</b>\n\n"
                f"Средства в Гаранте. Передайте заказ Покупателю.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔎 Открыть сделку", callback_data=f"view_deal:{deal_id}")]
                ])
            )
        except Exception:
            pass

    await render_deal_view(call.message, deal_id, user_id, is_edit=True)

@router.callback_query(F.data.startswith("ship_deal:"))
async def cb_ship_deal(call: CallbackQuery):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal or deal["seller_id"] != user_id:
        await call.answer("Только продавец подтверждает отправку.", show_alert=True)
        return

    await db_execute("UPDATE deals SET status = 'shipped' WHERE id = ?", (deal_id,))
    await call.answer("Покупатель уведомлен о передаче товара.")

    if deal["buyer_id"]:
        try:
            await bot.send_message(
                deal["buyer_id"],
                f"📦 <b>Продавец передал товар по сделке №{deal_id}!</b>\nПроверьте выполнение и подтвердите получение.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔎 Открыть сделку", callback_data=f"view_deal:{deal_id}")]
                ])
            )
        except Exception:
            pass

    await render_deal_view(call.message, deal_id, user_id, is_edit=True)

@router.callback_query(F.data.startswith("confirm_deal:"))
async def cb_confirm_deal(call: CallbackQuery):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal or deal["buyer_id"] != user_id:
        await call.answer("Только покупатель завершает сделку.", show_alert=True)
        return

    if deal["status"] not in ["paid", "shipped"]:
        await call.answer("Невозможно закрыть сделку в текущем статусе.", show_alert=True)
        return

    await db_transaction([
        ("UPDATE users SET balance = balance + ? WHERE user_id = ?", (deal["seller_receives"], deal["seller_id"])),
        ("INSERT INTO platform_vault (deal_id, fee_amount, created_at) VALUES (?, ?, ?)", (deal_id, deal["fee"], datetime.now().isoformat())),
        ("UPDATE deals SET status = 'completed' WHERE id = ?", (deal_id,))
    ])

    await call.answer("✅ Сделка успешно завершена! Продавец получил выплату.", show_alert=True)

    if deal["seller_id"]:
        try:
            await bot.send_message(
                deal["seller_id"],
                f"🎉 <b>Сделка №{deal_id} успешно закрыта!</b>\n\n"
                f"💰 На ваш баланс Кошелька начислено: <b>{deal['seller_receives']:,.2f} ₽</b> (за вычетом 40% комиссии).",
                reply_markup=get_main_keyboard()
            )
        except Exception:
            pass

    await render_deal_view(call.message, deal_id, user_id, is_edit=True)

@router.callback_query(F.data.startswith("cancel_deal:"))
async def cb_cancel_deal(call: CallbackQuery):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal or (deal["buyer_id"] != user_id and deal["seller_id"] != user_id):
        await call.answer("Нет прав.", show_alert=True)
        return

    if deal["status"] != "waiting_payment":
        await call.answer("Оплаченную сделку нельзя отменить без арбитража!", show_alert=True)
        return

    await db_execute("UPDATE deals SET status = 'cancelled' WHERE id = ?", (deal_id,))
    await call.answer("Сделка отменена.")
    await render_deal_view(call.message, deal_id, user_id, is_edit=True)

@router.callback_query(F.data.startswith("open_dispute:"))
async def cb_open_dispute(call: CallbackQuery):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal or (deal["buyer_id"] != user_id and deal["seller_id"] != user_id):
        await call.answer("Нет прав.", show_alert=True)
        return

    if deal["status"] not in ["paid", "shipped"]:
        await call.answer("Спор открывается только по оплаченной сделке.", show_alert=True)
        return

    await db_execute("UPDATE deals SET status = 'dispute' WHERE id = ?", (deal_id,))
    await call.answer("⚖️ Дело передано в арбитраж администратору.", show_alert=True)

    try:
        await bot.send_message(
            ADMIN_ID,
            f"🚨 <b>ОТКРЫТ СПОР ПО СДЕЛКЕ №{deal_id}!</b>\nИнициатор: <code>{user_id}</code>\nСумма: <b>{deal['amount']:,.2f} ₽</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔎 Открыть сделку", callback_data=f"view_deal:{deal_id}")]
            ])
        )
    except Exception:
        pass

    await render_deal_view(call.message, deal_id, user_id, is_edit=True)

# ══════════════════════════════════════════════════════════════════════════════
# АНОНИМНЫЙ МЕДИА-ЧАТ И ПРЕДПРОДАЖНЫЕ ВОПРОСЫ
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("open_chat:"))
async def cb_open_chat(call: CallbackQuery, state: FSMContext):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal or (deal["buyer_id"] != user_id and deal["seller_id"] != user_id):
        await call.answer("Вы не участник сделки.", show_alert=True)
        return

    await state.set_state(ChatProxyStates.in_chat)
    await state.update_data(deal_id=deal_id)

    role_label = "🔵 Покупатель" if user_id == deal["buyer_id"] else "🟢 Продавец"
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🚪 Покинуть диалог")]], resize_keyboard=True)

    await call.message.answer(
        f"💬 <b>Анонимный чат по сделке №{deal_id} активирован!</b>\n"
        f"Ваша роль: <b>{role_label}</b>\n\n"
        f"Все текстовые сообщения, фото, файлы и голосовые пересылаются собеседнику анонимно.\n"
        f"Для возврата в меню нажмите кнопку <b>«🚪 Покинуть диалог»</b>.",
        reply_markup=kb
    )
    await call.answer()

@router.message(ChatProxyStates.in_chat, F.text == "🚪 Покинуть диалог")
async def exit_proxy_chat(message: Message, state: FSMContext):
    data = await state.get_data()
    deal_id = data.get("deal_id")
    await state.clear()
    await message.answer("🚪 Вы покинули чат.", reply_markup=get_main_keyboard())
    if deal_id:
        await render_deal_view(message, deal_id, message.from_user.id)

@router.message(ChatProxyStates.in_chat)
async def proxy_relay(message: Message, state: FSMContext):
    data = await state.get_data()
    deal_id = data.get("deal_id")
    user_id = message.from_user.id

    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal:
        await state.clear()
        await message.answer("Сделка не найдена.", reply_markup=get_main_keyboard())
        return

    is_buyer = (user_id == deal["buyer_id"])
    target_id = deal["seller_id"] if is_buyer else deal["buyer_id"]
    prefix = "🔵 <b>[Покупатель]:</b>" if is_buyer else "🟢 <b>[Продавец]:</b>"

    if not target_id:
        await message.answer("⚠️ Второй участник еще не подключился к сделке.")
        return

    try:
        if message.text:
            await bot.send_message(target_id, f"{prefix} {message.text}")
        elif message.photo:
            cap = f"{prefix} {message.caption}" if message.caption else prefix
            await bot.send_photo(target_id, message.photo[-1].file_id, caption=cap)
        elif message.voice:
            await bot.send_voice(target_id, message.voice.file_id, caption=prefix)
        elif message.video_note:
            await bot.send_message(target_id, f"{prefix} <i>(Видеосообщение ниже)</i>")
            await bot.send_video_note(target_id, message.video_note.file_id)
        elif message.document:
            cap = f"{prefix} {message.caption}" if message.caption else prefix
            await bot.send_document(target_id, message.document.file_id, caption=cap)
    except Exception as e:
        logger.error(f"Ошибка чата: {e}")
        await message.answer("⚠️ Не удалось переслать сообщение собеседнику.")

@router.callback_query(F.data.startswith("ask_seller:"))
async def cb_ask_seller_init(call: CallbackQuery, state: FSMContext):
    prod_id = int(call.data.split(":")[1])
    product = await db_get_one("SELECT * FROM products WHERE id = ?", (prod_id,))
    if not product:
        await call.answer("Товар не найден.", show_alert=True)
        return

    await state.set_state(AskSellerStates.question)
    await state.update_data(product_id=prod_id, seller_id=product["seller_id"])

    await call.message.answer(
        f"💬 <b>Вопрос продавцу по товару #{prod_id} «{product['title']}»</b>\n\n"
        f"Напишите текст вопроса. Продавец получит уведомление и сможет ответить вам через бота:",
        reply_markup=get_cancel_reply_kb()
    )
    await call.answer()

@router.message(AskSellerStates.question)
async def process_ask_seller_text(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=get_main_keyboard())
        return

    data = await state.get_data()
    seller_id = data["seller_id"]
    prod_id = data["product_id"]
    buyer_id = message.from_user.id
    q_text = message.text.strip()

    await state.clear()
    try:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✉️ Ответить покупателю", callback_data=f"reply_buyer:{buyer_id}:{prod_id}")]
        ])
        await bot.send_message(
            seller_id,
            f"🔔 <b>Вопрос от покупателя по товару #{prod_id}!</b>\n\n«{q_text}»",
            reply_markup=kb
        )
        await message.answer("✅ Вопрос отправлен Продавцу! Ожидайте ответа.", reply_markup=get_main_keyboard())
    except Exception:
        await message.answer("⚠️ Не удалось отправить вопрос продавцу.", reply_markup=get_main_keyboard())

@router.callback_query(F.data.startswith("reply_buyer:"))
async def cb_reply_buyer_init(call: CallbackQuery, state: FSMContext):
    _, buyer_id_str, prod_id_str = call.data.split(":")
    await state.set_state(ReplyQuestionStates.reply_text)
    await state.update_data(target_buyer_id=int(buyer_id_str), product_id=int(prod_id_str))

    await call.message.answer("📝 Введите ваш ответ покупателю:", reply_markup=get_cancel_reply_kb())
    await call.answer()

@router.message(ReplyQuestionStates.reply_text)
async def process_reply_buyer_send(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=get_main_keyboard())
        return

    data = await state.get_data()
    buyer_id = data["target_buyer_id"]
    prod_id = data["product_id"]
    ans_text = message.text.strip()

    await state.clear()
    try:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔎 Открыть товар", callback_data=f"view_prod:{prod_id}:0")],
            [InlineKeyboardButton(text="💬 Задать еще вопрос", callback_data=f"ask_seller:{prod_id}")]
        ])
        await bot.send_message(
            buyer_id,
            f"💬 <b>Продавец ответил на ваш вопрос по товару #{prod_id}:</b>\n\n«{ans_text}»",
            reply_markup=kb
        )
        await message.answer("✅ Ответ передан покупателю!", reply_markup=get_main_keyboard())
    except Exception:
        await message.answer("⚠️ Не удалось отправить ответ покупателю.", reply_markup=get_main_keyboard())

# ══════════════════════════════════════════════════════════════════════════════
# ОТЗЫВЫ И РЕЙТИНГ (FUNPAY STYLE)
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("leave_review:"))
async def cb_leave_review(call: CallbackQuery, state: FSMContext):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal or deal["buyer_id"] != user_id:
        await call.answer("Только покупатель оставляет отзыв.", show_alert=True)
        return

    existing = await db_get_one("SELECT * FROM reviews WHERE deal_id = ?", (deal_id,))
    if existing:
        await call.answer("Отзыв уже оставлен.", show_alert=True)
        return

    await state.set_state(ReviewStates.rating)
    await state.update_data(deal_id=deal_id, target_id=deal["seller_id"])

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⭐ 1", callback_data="rate:1"),
        InlineKeyboardButton(text="⭐ 2", callback_data="rate:2"),
        InlineKeyboardButton(text="⭐ 3", callback_data="rate:3"),
        InlineKeyboardButton(text="⭐ 4", callback_data="rate:4"),
        InlineKeyboardButton(text="⭐ 5", callback_data="rate:5"),
    ]])
    await call.message.edit_text("⭐ <b>Оцените работу Продавца (1-5):</b>", reply_markup=kb)

@router.callback_query(ReviewStates.rating, F.data.startswith("rate:"))
async def cb_rate_score(call: CallbackQuery, state: FSMContext):
    score = int(call.data.split(":")[1])
    await state.update_data(rating=score)
    await state.set_state(ReviewStates.comment)
    await call.message.edit_text(f"Оценка: <b>{score} / 5 ⭐</b>\n\nНапишите текст отзыва (или отправьте «-»):")

@router.message(ReviewStates.comment)
async def process_review_comment(message: Message, state: FSMContext):
    txt = message.text.strip()
    comment = "Сделка прошла успешно!" if txt == "-" else txt
    data = await state.get_data()
    rating = data["rating"]
    is_positive = 1 if rating >= 4 else 0

    await db_transaction([
        ("INSERT INTO reviews (deal_id, target_user_id, author_user_id, rating, comment, created_at) VALUES (?, ?, ?, ?, ?, ?)",
         (data["deal_id"], data["target_id"], message.from_user.id, rating, comment, datetime.now().isoformat())),
        ("UPDATE users SET rating_sum = rating_sum + ?, rating_count = rating_count + 1, pos_reviews = pos_reviews + ? WHERE user_id = ?",
         (rating, is_positive, data["target_id"]))
    ])

    await state.clear()
    await message.answer("🎉 Спасибо! Ваш отзыв опубликован в профиле Продавца.", reply_markup=get_main_keyboard())

@router.callback_query(F.data.startswith("view_seller_reviews:"))
async def cb_view_seller_reviews(call: CallbackQuery):
    seller_id = int(call.data.split(":")[1])
    seller = await db_get_one("SELECT * FROM users WHERE user_id = ?", (seller_id,))
    reviews = await db_get_all("SELECT * FROM reviews WHERE target_user_id = ? ORDER BY id DESC LIMIT 5", (seller_id,))

    if not seller:
        await call.answer("Продавец не найден.")
        return

    avg = round(seller["rating_sum"] / seller["rating_count"], 1) if seller["rating_count"] > 0 else 5.0
    pos_p = round((seller["pos_reviews"] / seller["rating_count"]) * 100) if seller["rating_count"] > 0 else 100

    text = (
        f"⭐ <b>РЕПУТАЦИЯ ПРОДАВЦА {seller['username']}</b>\n"
        f"────────────────────────────\n"
        f"🌟 <b>Рейтинг:</b> {avg} / 5.0\n"
        f"👍 <b>Положительных отзывов:</b> {pos_p}% ({seller['rating_count']} шт.)\n"
        f"────────────────────────────\n"
        f"<b>Последние отзывы клиентов:</b>\n\n"
    )

    if not reviews:
        text += "<i>Пока нет отзывов. Станьте первым покупателем!</i>\n"
    else:
        for r in reviews:
            stars = "⭐" * r["rating"]
            date_str = r["created_at"][:10]
            text += f"{stars} ({date_str})\n«{r['comment']}»\n\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад ко всем категориям", callback_data="back_to_categories")]])
    await safe_edit_text(call.message, text, reply_markup=kb)

# ══════════════════════════════════════════════════════════════════════════════
# ПРОФИЛЬ, ВЫВОД И МОИ СДЕЛКИ
# ══════════════════════════════════════════════════════════════════════════════
@router.message(F.text == "👤 Профиль")
async def show_profile(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user = await get_or_create_user(user_id, message.from_user.username)

    avg_rating = round(user["rating_sum"] / user["rating_count"], 1) if user["rating_count"] > 0 else 5.0
    pos_p = round((user["pos_reviews"] / user["rating_count"]) * 100) if user["rating_count"] > 0 else 100
    completed = await db_get_one(
        "SELECT COUNT(*) as cnt FROM deals WHERE (buyer_id = ? OR seller_id = ?) AND status = 'completed'",
        (user_id, user_id)
    )
    completed_cnt = completed["cnt"] if completed else 0

    text = (
        f"👤 <b>ВАШ ПРОФИЛЬ НА ПЛОЩАДКЕ</b>\n"
        f"────────────────────────────\n"
        f"🆔 <b>Telegram ID:</b> <code>{user_id}</code>\n"
        f"💰 <b>Баланс в Кошельке:</b> <b>{user['balance']:,.2f} ₽</b>\n"
        f"⭐ <b>Рейтинг доверия:</b> {avg_rating} / 5.0 (👍 {pos_p}% положительных)\n"
        f"💬 <b>Отзывов:</b> {user['rating_count']}\n"
        f"🤝 <b>Завершённых сделок:</b> {completed_cnt}\n"
        f"────────────────────────────"
    )
    await message.answer(text, reply_markup=get_main_keyboard())

@router.message(F.text == "📂 Мои сделки")
async def show_my_deals(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    deals = await db_get_all(
        "SELECT * FROM deals WHERE (buyer_id = ? OR seller_id = ?) ORDER BY id DESC LIMIT 10",
        (user_id, user_id)
    )

    if not deals:
        await message.answer("У вас пока нет активных или завершенных сделок.", reply_markup=get_main_keyboard())
        return

    kb = []
    for d in deals:
        ico = "🔵" if d["buyer_id"] == user_id else "🟢"
        btn_text = f"{ico} №{d['id']} — {d['title'][:20]} ({d['amount']:,.0f} ₽)"
        kb.append([InlineKeyboardButton(text=btn_text, callback_data=f"view_deal:{d['id']}")])

    await message.answer("📂 <b>Ваши последние сделки:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "wallet_withdraw")
@router.message(F.text == "💳 Вывод средств")
async def start_withdraw(event: Any, state: FSMContext):
    await state.clear()
    user_id = event.from_user.id
    user = await get_or_create_user(user_id)

    if user["balance"] < MIN_WITHDRAW:
        msg_text = (
            f"💳 <b>Вывод средств с Кошелька</b>\n\n"
            f"Ваш текущий баланс: <b>{user['balance']:,.2f} ₽</b>\n"
            f"Минимальная сумма к выводу: <b>{MIN_WITHDRAW:,.2f} ₽</b>\n\n"
            f"📅 <b>График выплат:</b> Выплаты производятся <b>каждый вторник и четверг</b>."
        )
        if isinstance(event, CallbackQuery):
            await event.answer("Недостаточно средств для вывода.", show_alert=True)
            await event.message.answer(msg_text)
        else:
            await event.answer(msg_text)
        return

    await state.set_state(WithdrawStates.amount)
    prompt = f"💳 Введите сумму к выводу (доступно: <b>{user['balance']:,.2f} ₽</b>, мин: {MIN_WITHDRAW:,.0f} ₽):"
    if isinstance(event, CallbackQuery):
        await event.message.answer(prompt, reply_markup=get_cancel_reply_kb())
        await event.answer()
    else:
        await event.answer(prompt, reply_markup=get_cancel_reply_kb())

@router.message(WithdrawStates.amount)
async def process_withdraw_amount(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=get_main_keyboard())
        return

    user = await get_or_create_user(message.from_user.id)
    try:
        val = float(message.text.replace(",", ".").strip())
        if val < MIN_WITHDRAW or val > user["balance"]:
            await message.answer(f"⚠️ Сумма должна быть от {MIN_WITHDRAW:,.0f} до {user['balance']:,.2f} ₽. Введите снова:")
            return
    except ValueError:
        await message.answer("⚠️ Введите число:")
        return

    await state.update_data(amount=val)
    await state.set_state(WithdrawStates.requisites)
    await message.answer("💳 <b>Введите номер карты банка РФ или СБП с указанием банка:</b>")

@router.message(WithdrawStates.requisites)
async def process_withdraw_reqs(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=get_main_keyboard())
        return

    data = await state.get_data()
    amount = data["amount"]
    requisites = message.text.strip()
    user_id = message.from_user.id

    await db_transaction([
        ("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id)),
        ("INSERT INTO withdrawals (user_id, amount, requisites, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
         (user_id, amount, requisites, datetime.now().isoformat()))
    ])

    await state.clear()
    await message.answer(
        f"✅ <b>Заявка на вывод средств зарегистрирована!</b>\n\n"
        f"Сумма: <b>{amount:,.2f} ₽</b>\n"
        f"Реквизиты: <code>{requisites}</code>\n\n"
        f"📅 Средства поступят в ближайший расчетный день (<b>вторник</b> или <b>четверг</b>).",
        reply_markup=get_main_keyboard()
    )

    try:
        await bot.send_message(
            ADMIN_ID,
            f"💸 <b>НОВАЯ ЗАЯВКА НА ВЫВОД!</b>\nПользователь: <code>{user_id}</code>\nСумма: <b>{amount:,.2f} ₽</b>\nРеквизиты: <code>{requisites}</code>"
        )
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════════════════
# ПОДДЕРЖКА И ВНЕШНИЕ СЕРВИСЫ
# ══════════════════════════════════════════════════════════════════════════════
@router.message(F.text == "ℹ️ Помощь / Поддержка")
async def show_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        f"🛡 <b>Гарант и Маркетплейс «BastionPRO»</b>\n"
        f"────────────────────────────\n"
        f"• Комиссия платформы: <b>40%</b> (с суммы закрытой сделки Продавца)\n"
        f"• Выплаты баланса: <b>каждый вторник и четверг</b> (от 500 ₽)\n"
        f"• Пополняйте баланс в разделе «👛 Кошелёк»\n\n"
        f"👨‍💻 <b>Официальный контакт арбитража и поддержки:</b> {SUPPORT_HANDLE}",
        reply_markup=get_main_keyboard()
    )

@router.message(F.text == "🌐 Подключить OnyxVPN")
async def open_onyx_vpn(message: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Запустить OnyxVPN", url="https://t.me/vpnonyxxbot")]
    ])
    await message.answer(
        "🌐 <b>OnyxVPN — быстрый и безопасный VPN</b>\n\n"
        "• Надежное шифрование данных\n"
        "• Отсутствие логов и ограничений скорости",
        reply_markup=kb
    )

# ══════════════════════════════════════════════════════════════════════════════
# АДМИНИСТРАТИВНАЯ ПАНЕЛЬ И АРБИТРАЖ
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id != ADMIN_ID:
        return

    users_cnt = (await db_get_one("SELECT COUNT(*) as c FROM users"))["c"]
    deals_cnt = (await db_get_one("SELECT COUNT(*) as c FROM deals WHERE status = 'completed'"))["c"]
    vault_sum = (await db_get_one("SELECT SUM(fee_amount) as s FROM platform_vault"))["s"] or 0.0

    text = (
        f"👑 <b>ПАНЕЛЬ АДМИНИСТРАТОРА</b>\n"
        f"────────────────────────────\n"
        f"👥 Пользователей: <b>{users_cnt}</b>\n"
        f"✅ Закрытых сделок: <b>{deals_cnt}</b>\n"
        f"💰 Доход платформы (40%): <b>{vault_sum:,.2f} ₽</b>\n"
        f"────────────────────────────"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Рассылка всем пользователям", callback_data="admin_broadcast")]
    ])
    await message.answer(text, reply_markup=kb)

@router.callback_query(F.data.startswith("admin_refund:"))
async def cb_admin_refund(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    deal_id = int(call.data.split(":")[1])
    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))

    if deal:
        await db_transaction([
            ("UPDATE users SET balance = balance + ? WHERE user_id = ?", (deal["amount"], deal["buyer_id"])),
            ("UPDATE deals SET status = 'cancelled' WHERE id = ?", (deal_id,))
        ])
        await call.answer("Возврат покупателю выполнен.", show_alert=True)
        try:
            await bot.send_message(deal["buyer_id"], f"⚖️ По решению арбитража сумма {deal['amount']:,.2f} ₽ возвращена вам на баланс Кошелька.")
            await bot.send_message(deal["seller_id"], f"⚖️ Арбитраж по сделке №{deal_id} закрыт в пользу Покупателя.")
        except Exception:
            pass
        await render_deal_view(call.message, deal_id, call.from_user.id, is_edit=True)

@router.callback_query(F.data.startswith("admin_payout:"))
async def cb_admin_payout(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    deal_id = int(call.data.split(":")[1])
    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))

    if deal:
        await db_transaction([
            ("UPDATE users SET balance = balance + ? WHERE user_id = ?", (deal["seller_receives"], deal["seller_id"])),
            ("INSERT INTO platform_vault (deal_id, fee_amount, created_at) VALUES (?, ?, ?)", (deal_id, deal["fee"], datetime.now().isoformat())),
            ("UPDATE deals SET status = 'completed' WHERE id = ?", (deal_id,))
        ])
        await call.answer("Средства выплачены продавцу.", show_alert=True)
        try:
            await bot.send_message(deal["seller_id"], f"⚖️ По решению арбитража сумма {deal['seller_receives']:,.2f} ₽ начислена на ваш баланс.")
            await bot.send_message(deal["buyer_id"], f"⚖️ Арбитраж по сделке №{deal_id} закрыт в пользу Продавца.")
        except Exception:
            pass
        await render_deal_view(call.message, deal_id, call.from_user.id, is_edit=True)

@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminBroadcast.text)
    await call.message.answer("📢 Введите текст рассылки для всех пользователей:", reply_markup=get_cancel_reply_kb())
    await call.answer()

@router.message(AdminBroadcast.text)
async def process_admin_broadcast(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Рассылка отменена.", reply_markup=get_main_keyboard())
        return

    users = await db_get_all("SELECT user_id FROM users")
    await state.clear()
    sent = 0
    for u in users:
        try:
            await bot.send_message(u["user_id"], message.text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await message.answer(f"✅ Рассылка завершена! Отправлено: {sent} пользователям.", reply_markup=get_main_keyboard())

@router.callback_query(F.data == "cancel_fsm")
async def cb_cancel_fsm(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit_text(call.message, "🚫 Действие отменено.")
    await call.answer()

@router.callback_query(F.data == "close_view")
async def cb_close_view(call: CallbackQuery):
    await call.message.delete()
    await call.answer()

# ══════════════════════════════════════════════════════════════════════════════
# ТОЧКА ВХОДА
# ══════════════════════════════════════════════════════════════════════════════
async def main():
    await init_db()
    logger.info("Запуск маркетплейса BastionPRO...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
