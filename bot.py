"""
════════════════════════════════════════════════════════════════════════════════
    BASTION PRO & MARKET — P2P ЭСКРОУ-МАРКЕТПЛЕЙС (FUNPAY / KWORK STYLE)
    • Свободная публикация товаров и услуг продавцами
    • Каталог товаров с покупкой через гарант
    • Система рейтингов и отзывов FunPay (% положительных, звезды, комментарии)
    • Анонимный медиа-чат и предпродажные вопросы
    • Потокобезопасная БД (встроенный SQLite WAL, не требует aiosqlite)
    • Комиссия сервиса 40%, выплаты Вт/Чт от 500 ₽
════════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import logging
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

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
# ЗАГРУЗКА .ENV БЕЗ СТОРОННИХ МОДУЛЕЙ
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
DB_FILE = os.getenv("DB_FILE", "bastion_market_v1.db")
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
# БАЗА ДАННЫХ (SQLITE WAL С ЗАЩИТОЙ ОТ ЗАВИСАНИЙ)
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
        status TEXT DEFAULT 'created', -- created, waiting_payment, paid, shipped, dispute, completed, cancelled
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
    logger.info("База данных маркета и гаранта инициализирована.")

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
class AddProductStates(StatesGroup):
    title = State()
    description = State()
    price = State()

class CreateDealStates(StatesGroup):
    role = State()
    title = State()
    amount = State()
    partner_mode = State()
    partner_id = State()

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
# ГЛАВНОЕ МЕНЮ
# ══════════════════════════════════════════════════════════════════════════════
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛍 Каталог товаров"), KeyboardButton(text="📦 Мои товары")],
            [KeyboardButton(text="➕ Создать сделку"), KeyboardButton(text="📂 Мои сделки")],
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
# СТАРТ И ДИПЛИНКИ
# ══════════════════════════════════════════════════════════════════════════════
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    await get_or_create_user(user_id, message.from_user.username)

    args = command.args
    if args:
        if args.startswith("deal_"):
            try:
                deal_id = int(args.replace("deal_", ""))
                await process_deal_join(message, deal_id, user_id)
                return
            except Exception as e:
                logger.error(f"Ошибка диплинка сделки: {e}")
        elif args.startswith("item_"):
            try:
                prod_id = int(args.replace("item_", ""))
                await show_product_card(message, prod_id, user_id)
                return
            except Exception as e:
                logger.error(f"Ошибка диплинка товара: {e}")

    welcome = (
        f"🛡 <b>Добро пожаловать в P2P-маркетплейс и Гарант «BastionPRO»!</b>\n"
        f"────────────────────────────\n"
        f"Здравствуйте, <b>{message.from_user.first_name}</b>!\n\n"
        f"Здесь вы можете свободно <b>продавать и покупать</b> цифровые товары, "
        f"услуги, аккаунты, ключи и каналы с полной защитой эскроу-сервиса (как на FunPay):\n\n"
        f"• 🛍 <b>Каталог:</b> выберите проверенный товар и оформите сделку в 1 клик.\n"
        f"• 📦 <b>Продавцам:</b> выставляйте лоты бесплатно — получайте мгновенные выплаты.\n"
        f"• 🛡 <b>Гарант 100%:</b> деньги замораживаются до полной проверки товара покупателем.\n"
        f"• ⭐ <b>Честный рейтинг:</b> система оценок и реальных отзывов после каждой сделки.\n"
        f"• 💬 <b>Анонимный чат:</b> общение и передача файлов инкогнито прямо в боте.\n\n"
        f"📊 <b>Условия сервиса:</b>\n"
        f"• Комиссия гаранта: <b>40%</b> (вычитается при закрытии с суммы сделки)\n"
        f"• График выплат: <b>каждый вторник и четверг</b> (от 500 ₽)\n"
        f"• Арбитраж и поддержка: <b>{SUPPORT_HANDLE}</b>"
    )
    await message.answer(welcome, reply_markup=get_main_keyboard())

# ══════════════════════════════════════════════════════════════════════════════
# КАТАЛОГ ТОВАРОВ (МАРКЕТПЛЕЙС ДЛЯ ПОКУПАТЕЛЕЙ)
# ══════════════════════════════════════════════════════════════════════════════
PAGE_SIZE = 5

@router.message(F.text == "🛍 Каталог товаров")
async def show_catalog(message: Message, state: FSMContext):
    await state.clear()
    await render_catalog_page(message, page=0)

async def render_catalog_page(target: Message, page: int, is_edit: bool = False):
    offset = page * PAGE_SIZE
    products = await db_get_all(
        "SELECT * FROM products WHERE status = 'active' ORDER BY id DESC LIMIT ? OFFSET ?",
        (PAGE_SIZE, offset)
    )
    total_row = await db_get_one("SELECT COUNT(*) as c FROM products WHERE status = 'active'")
    total_count = total_row["c"] if total_row else 0

    if not products:
        text = "🛍 <b>В каталоге пока нет доступных товаров.</b>\n\nВы можете стать первым продавцом и выставить лот прямо сейчас через меню «📦 Мои товары»!"
        if is_edit:
            await safe_edit_text(target, text)
        else:
            await target.answer(text, reply_markup=get_main_keyboard())
        return

    text = (
        f"🛍 <b>КАТАЛОГ ТОВАРОВ И УСЛУГ</b>\n"
        f"<i>Все покупки защищены эскроу-гарантом</i>\n"
        f"────────────────────────────\n"
    )

    kb = []
    for p in products:
        seller = await db_get_one("SELECT rating_sum, rating_count, pos_reviews FROM users WHERE user_id = ?", (p["seller_id"],))
        stars = "★ " + str(round(seller["rating_sum"] / seller["rating_count"], 1)) if seller and seller["rating_count"] > 0 else "Новый"
        btn_text = f"💎 {p['title'][:22]} — {p['price']:,.0f} ₽ ({stars})"
        kb.append([InlineKeyboardButton(text=btn_text, callback_data=f"view_prod:{p['id']}:{page}")])

    # Пагинация
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"cat_page:{page - 1}"))
    if (offset + PAGE_SIZE) < total_count:
        nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"cat_page:{page + 1}"))

    if nav_buttons:
        kb.append(nav_buttons)

    text += f"Всего товаров в наличии: <b>{total_count}</b> | Страница: <b>{page + 1}</b>"

    reply_markup = InlineKeyboardMarkup(inline_keyboard=kb)
    if is_edit:
        await safe_edit_text(target, text, reply_markup=reply_markup)
    else:
        await target.answer(text, reply_markup=reply_markup)

@router.callback_query(F.data.startswith("cat_page:"))
async def cb_cat_page(call: CallbackQuery):
    page = int(call.data.split(":")[1])
    await render_catalog_page(call.message, page, is_edit=True)
    await call.answer()

async def show_product_card(target_message: Message, prod_id: int, user_id: int, page: int = 0, is_edit: bool = False):
    p = await db_get_one("SELECT * FROM products WHERE id = ?", (prod_id,))
    if not p or p["status"] != "active":
        text = "❌ Товар был продан или снят с продажи продавцом."
        if is_edit:
            await safe_edit_text(target_message, text)
        else:
            await target_message.answer(text)
        return

    seller = await db_get_one("SELECT * FROM users WHERE user_id = ?", (p["seller_id"],))
    rating_avg = round(seller["rating_sum"] / seller["rating_count"], 1) if seller and seller["rating_count"] > 0 else 5.0
    reviews_cnt = seller["rating_count"] if seller else 0
    pos_percent = round((seller["pos_reviews"] / reviews_cnt) * 100) if reviews_cnt > 0 else 100

    text = (
        f"💎 <b>КАРТОЧКА ТОВАРА #{p['id']}</b>\n"
        f"────────────────────────────\n"
        f"📌 <b>Название:</b> {p['title']}\n"
        f"💰 <b>Стоимость:</b> <b>{p['price']:,.2f} ₽</b>\n\n"
        f"📝 <b>Описание:</b>\n{p['description']}\n"
        f"────────────────────────────\n"
        f"👤 <b>Продавец:</b> {seller['username']}\n"
        f"⭐ <b>Рейтинг:</b> {rating_avg} / 5.0 | 👍 <b>{pos_percent}%</b> довольных клиентов ({reviews_cnt} отзывов)\n"
    )

    kb = []
    # Если покупатель не сам продавец
    if user_id != p["seller_id"]:
        kb.append([InlineKeyboardButton(text="💳 Купить через Гарант", callback_data=f"buy_prod:{p['id']}")])
        kb.append([InlineKeyboardButton(text="💬 Задать вопрос продавцу", callback_data=f"ask_seller:{p['id']}")])
        kb.append([InlineKeyboardButton(text="⭐ Отзывы о продавце", callback_data=f"view_seller_reviews:{p['seller_id']}")])
    else:
        kb.append([InlineKeyboardButton(text="🗑 Удалить мой товар", callback_data=f"del_prod:{p['id']}")])

    kb.append([InlineKeyboardButton(text="◀️ Вернуться в каталог", callback_data=f"cat_page:{page}")])

    reply_markup = InlineKeyboardMarkup(inline_keyboard=kb)
    if is_edit:
        await safe_edit_text(target_message, text, reply_markup=reply_markup)
    else:
        await target_message.answer(text, reply_markup=reply_markup)

@router.callback_query(F.data.startswith("view_prod:"))
async def cb_view_prod(call: CallbackQuery):
    _, prod_id_str, page_str = call.data.split(":")
    await show_product_card(call.message, int(prod_id_str), call.from_user.id, page=int(page_str), is_edit=True)
    await call.answer()

# ══════════════════════════════════════════════════════════════════════════════
# ПРЕДПРОДАЖНЫЕ ВОПРОСЫ (АНОНИМНЫЙ ДИАЛОГ ДО ПОКУПКИ)
# ══════════════════════════════════════════════════════════════════════════════
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
        f"💬 <b>Вопрос по товару «{product['title']}»</b>\n\n"
        f"Напишите текст вашего вопроса продавцу. Он будет передан полностью анонимно.\n"
        f"<i>(Продавец получит уведомление и сможет ответить вам через бота)</i>",
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
            f"🔔 <b>Новый вопрос по вашему товару #{prod_id}!</b>\n\n"
            f"👤 <b>Покупатель спрашивает:</b>\n«{q_text}»\n\n"
            f"<i>Нажмите кнопку ниже, чтобы ответить.</i>",
            reply_markup=kb
        )
        await message.answer("✅ <b>Ваш вопрос отправлен Продавцу!</b> Ожидайте ответа в этом диалоге.", reply_markup=get_main_keyboard())
    except Exception:
        await message.answer("⚠️ Не удалось связаться с продавцом.", reply_markup=get_main_keyboard())

@router.callback_query(F.data.startswith("reply_buyer:"))
async def cb_reply_buyer_init(call: CallbackQuery, state: FSMContext):
    _, buyer_id_str, prod_id_str = call.data.split(":")
    await state.set_state(ReplyQuestionStates.reply_text)
    await state.update_data(target_buyer_id=int(buyer_id_str), product_id=int(prod_id_str))

    await call.message.answer(
        "📝 <b>Введите ваш ответ покупателю:</b>",
        reply_markup=get_cancel_reply_kb()
    )
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
            f"💬 <b>Продавец ответил на ваш вопрос по товару #{prod_id}!</b>\n\n"
            f"«{ans_text}»",
            reply_markup=kb
        )
        await message.answer("✅ Ответ успешно передан покупателю!", reply_markup=get_main_keyboard())
    except Exception:
        await message.answer("⚠️ Не удалось доставить ответ покупателю.", reply_markup=get_main_keyboard())

# ══════════════════════════════════════════════════════════════════════════════
# ПОКУПКА ТОВАРА ИЗ КАТАЛОГА (АВТОМАТИЧЕСКАЯ СДЕЛКА)
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("buy_prod:"))
async def cb_buy_product(call: CallbackQuery):
    prod_id = int(call.data.split(":")[1])
    buyer_id = call.from_user.id
    await get_or_create_user(buyer_id, call.from_user.username)

    product = await db_get_one("SELECT * FROM products WHERE id = ?", (prod_id,))
    if not product or product["status"] != "active":
        await call.answer("Товар уже недоступен для покупки.", show_alert=True)
        return

    if product["seller_id"] == buyer_id:
        await call.answer("Вы не можете купить собственный товар!", show_alert=True)
        return

    amount = product["price"]
    fee = round(amount * COMMISSION_RATE, 2)
    seller_receives = round(amount - fee, 2)

    # Создаем сделку
    deal_id = await db_execute("""
        INSERT INTO deals (product_id, buyer_id, seller_id, title, amount, fee, seller_receives, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'waiting_payment', ?)
    """, (product["id"], buyer_id, product["seller_id"], product["title"], amount, fee, seller_receives, datetime.now().isoformat()))

    await call.answer("Сделка сформирована!")

    # Уведомляем продавца
    try:
        await bot.send_message(
            product["seller_id"],
            f"🔔 <b>Покупатель оформляет заказ на товар #{product['id']} «{product['title']}»!</b>\n\n"
            f"Сформирована сделка №{deal_id}.\n"
            f"⏳ Ожидайте подтверждения оплаты от покупателя.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔎 Открыть сделку", callback_data=f"view_deal:{deal_id}")]
            ])
        )
    except Exception:
        pass

    await render_deal_view(call.message, deal_id, buyer_id, is_edit=True)

# ══════════════════════════════════════════════════════════════════════════════
# МОИ ТОВАРЫ (КАБИНЕТ ПРОДАВЦА)
# ══════════════════════════════════════════════════════════════════════════════
@router.message(F.text == "📦 Мои товары")
async def show_seller_products(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    await get_or_create_user(user_id, message.from_user.username)

    products = await db_get_all("SELECT * FROM products WHERE seller_id = ? AND status = 'active' ORDER BY id DESC", (user_id,))

    kb = [[InlineKeyboardButton(text="➕ Выставить новый товар / услугу", callback_data="add_new_product")]]

    if not products:
        text = (
            "📦 <b>У вас пока нет активных товаров в каталоге.</b>\n\n"
            "Вы можете выставить любой цифровой товар или услугу на продажу прямо сейчас!"
        )
    else:
        text = f"📦 <b>Ваши активные лоты в каталоге ({len(products)}):</b>\n\n"
        for idx, p in enumerate(products, 1):
            text += f"{idx}. <b>{p['title']}</b> — {p['price']:,.2f} ₽ (Вы получите: {p['price'] * 0.6:,.2f} ₽)\n"
            kb.append([InlineKeyboardButton(text=f"🗑 Снять с продажи «{p['title'][:20]}»", callback_data=f"del_prod:{p['id']}")])

    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "add_new_product")
async def cb_add_new_prod(call: CallbackQuery, state: FSMContext):
    await state.set_state(AddProductStates.title)
    await call.message.answer(
        "💎 <b>Шаг 1 из 3. Введите название товара или услуги:</b>\n\n"
        "<i>Пример: Telegram Premium на 1 год / Исходный код криптобота / Steam Аккаунт с CS2</i>",
        reply_markup=get_cancel_reply_kb()
    )
    await call.answer()

@router.message(AddProductStates.title)
async def process_prod_title(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Добавление товара отменено.", reply_markup=get_main_keyboard())
        return

    if len(message.text) < 3 or len(message.text) > 100:
        await message.answer("⚠️ Название должно быть от 3 до 100 символов. Введите снова:")
        return

    await state.update_data(title=message.text.strip())
    await state.set_state(AddProductStates.description)
    await message.answer(
        "📝 <b>Шаг 2 из 3. Введите подробное описание:</b>\n\n"
        "Укажите характеристики, условия передачи, гарантию и важные детали для покупателя:",
        reply_markup=get_cancel_reply_kb()
    )

@router.message(AddProductStates.description)
async def process_prod_desc(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Добавление товара отменено.", reply_markup=get_main_keyboard())
        return

    if len(message.text) < 5 or len(message.text) > 800:
        await message.answer("⚠️ Описание должно быть от 5 до 800 символов. Введите снова:")
        return

    await state.update_data(description=message.text.strip())
    await state.set_state(AddProductStates.price)
    await message.answer(
        "💰 <b>Шаг 3 из 3. Укажите цену для покупателя (в рублях):</b>\n\n"
        "<i>Покупатель оплатит указанную сумму. Комиссия гаранта составит 40%, вы получите 60% чистыми.</i>\n"
        "<i>(Минимум — 100 ₽)</i>",
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
            await message.answer("⚠️ Цена должна быть от 100 до 1 000 000 ₽. Введите корректную сумму:")
            return
    except ValueError:
        await message.answer("⚠️ Введите число (например: 1500):")
        return

    data = await state.get_data()
    seller_receives = round(price * (1 - COMMISSION_RATE), 2)
    user_id = message.from_user.id

    prod_id = await db_execute("""
        INSERT INTO products (seller_id, title, description, price, status, created_at)
        VALUES (?, ?, ?, ?, 'active', ?)
    """, (user_id, data["title"], data["description"], price, datetime.now().isoformat()))

    await state.clear()
    await message.answer(
        f"🎉 <b>Товар #{prod_id} успешно опубликован в общем каталоге!</b>\n\n"
        f"📌 <b>Название:</b> {data['title']}\n"
        f"💰 <b>Цена для покупателя:</b> {price:,.2f} ₽\n"
        f"💼 <b>Вы получите при продаже:</b> <b>{seller_receives:,.2f} ₽</b> (за вычетом 40%)\n\n"
        f"Теперь покупатели могут приобрести ваш товар через каталог или связаться с вами.",
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
    await call.answer("Товар успешно снят с продажи.")
    await safe_edit_text(call.message, f"🗑 Товар #{prod_id} снят с публикации.")

# ══════════════════════════════════════════════════════════════════════════════
# СОЗДАНИЕ ИНДИВИДУАЛЬНОЙ СДЕЛКИ
# ══════════════════════════════════════════════════════════════════════════════
@router.message(F.text == "➕ Создать сделку")
async def start_create_deal(message: Message, state: FSMContext):
    await state.clear()
    await get_or_create_user(message.from_user.id, message.from_user.username)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔵 Я Покупатель (буду оплачивать)", callback_data="create_as:buyer")],
        [InlineKeyboardButton(text="🟢 Я Продавец (буду передавать товар)", callback_data="create_as:seller")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")]
    ])
    await message.answer(
        "🎯 <b>Инициализация сделки (Шаг 1 из 4):</b>\n\n"
        "• <b>🔵 Покупатель:</b> Вы вносите оплату в гарант. Деньги замораживаются до полной проверки товара.\n"
        "• <b>🟢 Продавец:</b> Вы передаете товар только <b>ПОСЛЕ</b> подтверждения оплаты гарантом.\n\n"
        "<i>Выберите вашу роль:</i>",
        reply_markup=kb
    )

@router.callback_query(F.data.startswith("create_as:"))
async def cb_select_creator_role(call: CallbackQuery, state: FSMContext):
    role = call.data.split(":")[1]
    await state.update_data(creator_role=role)
    await state.set_state(CreateDealStates.title)

    role_str = "🔵 Покупатель" if role == "buyer" else "🟢 Продавец"
    await call.message.edit_text(
        f"Выбрана роль: <b>{role_str}</b>\n\n"
        f"📝 <b>Шаг 2 из 4. Введите предмет или условия сделки:</b>\n"
        f"<i>(Пример: Покупка Telegram-канала / Разработка скрипта)</i>"
    )

@router.message(CreateDealStates.title)
async def process_deal_title(message: Message, state: FSMContext):
    if len(message.text) < 3 or len(message.text) > 200:
        await message.answer("⚠️ Описание должно быть от 3 до 200 символов. Введите снова:")
        return

    await state.update_data(title=message.text.strip())
    await state.set_state(CreateDealStates.amount)
    data = await state.get_data()

    hint = "сумму, которую вы готовы внести в гарант (в рублях):" if data.get("creator_role") == "buyer" else "полную стоимость товара/услуги (в рублях):"
    await message.answer(f"💰 <b>Шаг 3 из 4. Введите {hint}</b>\n<i>(Минимум — 100 ₽)</i>", reply_markup=get_cancel_reply_kb())

@router.message(CreateDealStates.amount)
async def process_deal_amount(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Создание отменено.", reply_markup=get_main_keyboard())
        return

    try:
        amount = float(message.text.replace(",", ".").strip())
        if amount < 100.0 or amount > 500000.0:
            await message.answer("⚠️ Сумма должна быть от 100 до 500 000 ₽. Попробуйте еще раз:")
            return
    except ValueError:
        await message.answer("⚠️ Введите число (например: 1500):")
        return

    fee = round(amount * COMMISSION_RATE, 2)
    seller_receives = round(amount - fee, 2)

    await state.update_data(amount=amount, fee=fee, seller_receives=seller_receives)
    await state.set_state(CreateDealStates.partner_mode)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Сформировать ссылку-инвайт", callback_data="partner_mode:link")],
        [InlineKeyboardButton(text="🆔 Указать Telegram ID партнера", callback_data="partner_mode:id")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")]
    ])

    await message.answer(
        f"📊 <b>Финансовые условия:</b>\n"
        f"• Полная сумма сделки: <b>{amount:,.2f} ₽</b>\n"
        f"• Комиссия гаранта (40%): <b>{fee:,.2f} ₽</b>\n"
        f"• К выплате продавцу: <b>{seller_receives:,.2f} ₽</b>\n\n"
        f"👥 <b>Шаг 4 из 4. Выберите способ приглашения второго участника:</b>",
        reply_markup=kb
    )

@router.callback_query(F.data.startswith("partner_mode:"))
async def cb_select_partner_mode(call: CallbackQuery, state: FSMContext):
    mode = call.data.split(":")[1]
    data = await state.get_data()
    user_id = call.from_user.id
    creator_role = data["creator_role"]

    if mode == "id":
        await state.set_state(CreateDealStates.partner_id)
        other_str = "Продавца" if creator_role == "buyer" else "Покупателя"
        await call.message.edit_text(
            f"🆔 <b>Введите цифровой Telegram ID {other_str}:</b>\n\n"
            f"<i>(Партнер может узнать свой ID в боте @userinfobot)</i>"
        )
        return

    buyer_id = user_id if creator_role == "buyer" else None
    seller_id = user_id if creator_role == "seller" else None

    deal_id = await db_execute("""
        INSERT INTO deals (buyer_id, seller_id, title, amount, fee, seller_receives, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'created', ?)
    """, (buyer_id, seller_id, data["title"], data["amount"], data["fee"], data["seller_receives"], datetime.now().isoformat()))

    await state.clear()
    bot_info = await bot.get_me()
    invite_url = f"https://t.me/{bot_info.username}?start=deal_{deal_id}"
    other_role = "Продавцу" if creator_role == "buyer" else "Покупателю"

    await call.message.edit_text(
        f"✅ <b>Сделка №{deal_id} успешно создана!</b>\n\n"
        f"Отправьте эту ссылку {other_role}:\n"
        f"👉 <code>{invite_url}</code>\n\n"
        f"При переходе партнер автоматически займет правильную роль "
        f"<b>{'🟢 Продавца' if creator_role == 'buyer' else '🔵 Покупателя'}</b>.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔎 Открыть сделку", callback_data=f"view_deal:{deal_id}")]
        ])
    )

@router.message(CreateDealStates.partner_id)
async def process_partner_id_input(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Создание отменено.", reply_markup=get_main_keyboard())
        return

    try:
        partner_id = int(message.text.strip())
        if partner_id == message.from_user.id:
            await message.answer("⚠️ Нельзя указывать свой ID. Введите ID второго участника:")
            return
    except ValueError:
        await message.answer("⚠️ Введите числовой ID:")
        return

    data = await state.get_data()
    creator_id = message.from_user.id
    creator_role = data["creator_role"]

    if creator_role == "buyer":
        buyer_id, seller_id = creator_id, partner_id
    else:
        buyer_id, seller_id = partner_id, creator_id

    deal_id = await db_execute("""
        INSERT INTO deals (buyer_id, seller_id, title, amount, fee, seller_receives, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'waiting_payment', ?)
    """, (buyer_id, seller_id, data["title"], data["amount"], data["fee"], data["seller_receives"], datetime.now().isoformat()))

    await state.clear()

    try:
        partner_role = "🟢 Продавцом" if creator_role == "buyer" else "🔵 Покупателем"
        await bot.send_message(
            partner_id,
            f"🔔 <b>Вам предложена сделка №{deal_id}!</b>\n\n"
            f"📌 Условия: <b>{data['title']}</b>\n"
            f"💰 Сумма: <b>{data['amount']:,.2f} ₽</b>\n"
            f"Вы назначены: <b>{partner_role}</b>.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔎 Открыть сделку", callback_data=f"view_deal:{deal_id}")]
            ])
        )
    except Exception:
        pass

    await message.answer(f"✅ <b>Сделка №{deal_id} успешно создана!</b>", reply_markup=get_main_keyboard())
    await render_deal_view(message, deal_id, creator_id)

async def process_deal_join(message: Message, deal_id: int, user_id: int):
    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal:
        await message.answer("❌ Сделка не найдена.", reply_markup=get_main_keyboard())
        return

    if deal["buyer_id"] == user_id or deal["seller_id"] == user_id:
        await render_deal_view(message, deal_id, user_id)
        return

    if deal["buyer_id"] is None and deal["seller_id"] != user_id:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔵 Принять роль Покупателя", callback_data=f"join_role:{deal_id}:buyer")],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data="close_view")]
        ])
        await message.answer(
            f"🤝 <b>Приглашение в сделку №{deal['id']}!</b>\n\n"
            f"📌 Условия: <b>{deal['title']}</b>\n"
            f"💰 Сумма: <b>{deal['amount']:,.2f} ₽</b>\n\n"
            f"Вы вступаете как <b>🔵 Покупатель</b>.",
            reply_markup=kb
        )
    elif deal["seller_id"] is None and deal["buyer_id"] != user_id:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Принять роль Продавца", callback_data=f"join_role:{deal_id}:seller")],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data="close_view")]
        ])
        await message.answer(
            f"🤝 <b>Приглашение в сделку №{deal['id']}!</b>\n\n"
            f"📌 Условия: <b>{deal['title']}</b>\n"
            f"💰 Стоимость: <b>{deal['amount']:,.2f} ₽</b>\n"
            f"💼 Ваша выплата: <b>{deal['seller_receives']:,.2f} ₽</b> (за вычетом 40% комиссии).\n\n"
            f"Вы вступаете как <b>🟢 Продавец</b>.",
            reply_markup=kb
        )
    else:
        await message.answer("⚠️ Все места в сделке заняты.", reply_markup=get_main_keyboard())

@router.callback_query(F.data.startswith("join_role:"))
async def cb_join_role(call: CallbackQuery):
    _, deal_id_str, role = call.data.split(":")
    deal_id = int(deal_id_str)
    user_id = call.from_user.id
    await get_or_create_user(user_id, call.from_user.username)

    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal:
        await call.answer("Сделка не найдена.", show_alert=True)
        return

    creator_id = deal["buyer_id"] if deal["buyer_id"] else deal["seller_id"]

    if role == "buyer" and deal["buyer_id"] is None:
        await db_execute("UPDATE deals SET buyer_id = ?, status = 'waiting_payment' WHERE id = ?", (user_id, deal_id))
        await call.answer("Вы вступили как Покупатель!")
        try:
            await bot.send_message(creator_id, f"🔔 Покупатель подключился к сделке №{deal_id}!")
        except Exception:
            pass
        await render_deal_view(call.message, deal_id, user_id, is_edit=True)

    elif role == "seller" and deal["seller_id"] is None:
        await db_execute("UPDATE deals SET seller_id = ?, status = 'waiting_payment' WHERE id = ?", (user_id, deal_id))
        await call.answer("Вы вступили как Продавец!")
        try:
            await bot.send_message(creator_id, f"🔔 Продавец подключился к сделке №{deal_id}!")
        except Exception:
            pass
        await render_deal_view(call.message, deal_id, user_id, is_edit=True)
    else:
        await call.answer("Роль уже занята.", show_alert=True)

# ══════════════════════════════════════════════════════════════════════════════
# КАРТОЧКА СДЕЛКИ: ИДЕАЛЬНЫЕ ПАМЯТКИ И РАЗГРАНИЧЕНИЕ РОЛЕЙ
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
        "created": "⏳ Ожидание подключения партнера",
        "waiting_payment": "💳 Ожидание внесения оплаты Покупателем",
        "paid": "🛡 Оплачено (средства заблокированы на гаранте)",
        "shipped": "📦 Товар передан Продавцом (на проверке у Покупателя)",
        "dispute": "⚖️ Открыт спор (дело передано в арбитраж)",
        "completed": "✅ Сделка успешно завершена",
        "cancelled": "🚫 Сделка аннулирована"
    }

    my_role = "🔵 <b>Покупатель</b>" if is_buyer else ("🟢 <b>Продавец</b>" if is_seller else "👑 <b>Администратор</b>")

    text = (
        f"📋 <b>КАРТОЧКА СДЕЛКИ №{deal['id']}</b>\n"
        f"────────────────────────────\n"
        f"📌 <b>Предмет:</b> {deal['title']}\n"
        f"💰 <b>Сумма к оплате:</b> {deal['amount']:,.2f} ₽\n"
        f"💼 <b>К выплате Продавцу:</b> {deal['seller_receives']:,.2f} ₽ <i>(комиссия гаранта 40%: {deal['fee']:,.2f} ₽)</i>\n"
        f"🚦 <b>Статус:</b> {status_map.get(deal['status'], deal['status'])}\n"
        f"👤 Ваша роль: {my_role}\n"
        f"────────────────────────────\n"
    )

    kb_buttons = []

    # 1. Ожидание оплаты
    if deal["status"] in ["created", "waiting_payment"]:
        if is_buyer:
            payment_url = f"https://app.paritypay.ru/pay/{PARITYPAY_SHOP_ID}?order_id={deal['id']}&amount={deal['amount']}"
            kb_buttons.append([InlineKeyboardButton(text="💳 Оплатить через ParityPay", url=payment_url)])
            kb_buttons.append([InlineKeyboardButton(text="✅ Проверить поступление оплаты", callback_data=f"check_pay:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="❌ Отменить сделку", callback_data=f"cancel_deal:{deal['id']}")])
            text += (
                "📖 <b>Инструкция для Покупателя:</b>\n"
                "1. Нажмите «Оплатить через ParityPay» и переведите сумму.\n"
                "2. Нажмите «Проверить поступление оплаты».\n"
                "3. Средства заморозятся на счете Гаранта, и Продавец начнет передачу товара."
            )
        elif is_seller:
            kb_buttons.append([InlineKeyboardButton(text="❌ Отменить сделку", callback_data=f"cancel_deal:{deal['id']}")])
            text += (
                "⚠️ <b>ПАМЯТКА ДЛЯ ПРОДАВЦА:</b>\n"
                "• <b>НИ В КОЕМ СЛУЧАЕ НЕ ПЕРЕДАВАЙТЕ ТОВАР СЕЙЧАС!</b>\n"
                "• Ожидайте, пока Покупатель внесет средства.\n"
                "• Вы получите уведомление, когда статус сменится на «🛡 Оплачено»."
            )

    # 2. Оплачено
    elif deal["status"] == "paid":
        if is_seller:
            kb_buttons.append([InlineKeyboardButton(text="📦 Товар/услуга передана", callback_data=f"ship_deal:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="💬 Анонимный чат сделки", callback_data=f"open_chat:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="⚖️ Открыть спор", callback_data=f"open_dispute:{deal['id']}")])
            text += (
                "💰 <b>Деньги зачислены в Гарант!</b>\n"
                "📖 <b>Инструкция для Продавца:</b>\n"
                "1. Отправьте товар/данные Покупателю через «💬 Анонимный чат сделки».\n"
                "2. После отправки обязательно нажмите кнопку <b>«📦 Товар/услуга передана»</b>."
            )
        elif is_buyer:
            kb_buttons.append([InlineKeyboardButton(text="💬 Анонимный чат сделки", callback_data=f"open_chat:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="⚖️ Открыть спор", callback_data=f"open_dispute:{deal['id']}")])
            text += (
                "🛡 <b>Ваши деньги надежно заблокированы в гарант-сервисе!</b>\n"
                "📖 <b>Инструкция для Покупателя:</b>\n"
                "Продавец готовит и передает заказ. Вы можете общаться с ним в анонимном чате."
            )

    # 3. Передано (на проверке)
    elif deal["status"] == "shipped":
        if is_buyer:
            kb_buttons.append([InlineKeyboardButton(text="✅ Подтвердить получение (Закрыть)", callback_data=f"confirm_deal:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="💬 Анонимный чат сделки", callback_data=f"open_chat:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="⚖️ Открыть спор", callback_data=f"open_dispute:{deal['id']}")])
            text += (
                "📦 <b>Продавец сообщил о передаче товара!</b>\n"
                "📖 <b>Инструкция для Покупателя:</b>\n"
                "1. Тщательно проверьте полученный товар/услугу.\n"
                "2. Если всё верно — нажмите <b>«✅ Подтвердить получение»</b> (деньги будут переведены Продавцу).\n"
                "3. Если есть проблемы — нажмите <b>«⚖️ Открыть спор»</b>."
            )
        elif is_seller:
            kb_buttons.append([InlineKeyboardButton(text="💬 Анонимный чат сделки", callback_data=f"open_chat:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="⚖️ Открыть спор", callback_data=f"open_dispute:{deal['id']}")])
            text += (
                "⏳ <b>Заказ находится на проверке у Покупателя.</b>\n"
                "Как только Покупатель нажмет подтверждение, средства (60%) мгновенно поступят на ваш баланс."
            )

    # 4. Спор
    elif deal["status"] == "dispute":
        kb_buttons.append([InlineKeyboardButton(text="💬 Чат участников", callback_data=f"open_chat:{deal['id']}")])
        text += f"\n⚖️ <i>Сделка заморожена. Арбитр изучает материалы. Поддержка: {SUPPORT_HANDLE}</i>"

    # 5. Завершена
    elif deal["status"] == "completed":
        if is_buyer:
            kb_buttons.append([InlineKeyboardButton(text="⭐ Оценить работу Продавца", callback_data=f"leave_review:{deal['id']}")])
        text += "\n🎉 <i>Сделка успешно завершена. Финансовые расчеты произведены в полном объеме.</i>"

    kb_buttons.append([InlineKeyboardButton(text="🔄 Обновить статус", callback_data=f"view_deal:{deal['id']}")])

    if is_admin and deal["status"] == "dispute":
        kb_buttons.append([
            InlineKeyboardButton(text="👑 Вернуть Покупателю 100%", callback_data=f"admin_refund:{deal['id']}"),
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

# ══════════════════════════════════════════════════════════════════════════════
# ЭТАПЫ СДЕЛКИ: ОПЛАТА, ПЕРЕДАЧА, ЗАКРЫТИЕ
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("check_pay:"))
async def cb_check_pay(call: CallbackQuery):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal or deal["buyer_id"] != user_id:
        await call.answer("Только покупатель подтверждает оплату.", show_alert=True)
        return

    # В тестовом режиме переводим сделку в статус paid
    await db_execute("UPDATE deals SET status = 'paid' WHERE id = ?", (deal_id,))

    # Если товар был из каталога — помечаем его как проданный
    if deal["product_id"]:
        await db_execute("UPDATE products SET status = 'sold' WHERE id = ?", (deal["product_id"],))

    await call.answer("✅ Оплата подтверждена! Деньги заморожены в гаранте.", show_alert=True)

    if deal["seller_id"]:
        try:
            await bot.send_message(
                deal["seller_id"],
                f"💰 <b>Покупатель внес оплату по сделке №{deal_id}!</b>\n\n"
                f"Деньги находятся на эскроу-счете Гаранта. Передайте заказ Покупателю и нажмите «Товар передан».",
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
        await call.answer("Только продавец подтверждает передачу.", show_alert=True)
        return

    await db_execute("UPDATE deals SET status = 'shipped' WHERE id = ?", (deal_id,))
    await call.answer("Покупатель уведомлен об отправке товара.")

    if deal["buyer_id"]:
        try:
            await bot.send_message(
                deal["buyer_id"],
                f"📦 <b>Продавец передал заказ по сделке №{deal_id}!</b>\n\n"
                f"Пожалуйста, проверьте товар и подтвердите получение.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔎 Проверить заказ", callback_data=f"view_deal:{deal_id}")]
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
        await call.answer("Только покупатель закрывает сделку.", show_alert=True)
        return

    if deal["status"] not in ["paid", "shipped"]:
        await call.answer("Сделка не может быть закрыта в текущем статусе.", show_alert=True)
        return

    # Зачисление 60% продавцу и 40% сервису
    await db_transaction([
        ("UPDATE users SET balance = balance + ? WHERE user_id = ?", (deal["seller_receives"], deal["seller_id"])),
        ("INSERT INTO platform_vault (deal_id, fee_amount, created_at) VALUES (?, ?, ?)", (deal_id, deal["fee"], datetime.now().isoformat())),
        ("UPDATE deals SET status = 'completed' WHERE id = ?", (deal_id,))
    ])

    await call.answer("✅ Сделка успешно завершена!", show_alert=True)

    if deal["seller_id"]:
        try:
            await bot.send_message(
                deal["seller_id"],
                f"🎉 <b>Сделка №{deal_id} успешно закрыта!</b>\n\n"
                f"💰 На ваш баланс зачислено: <b>{deal['seller_receives']:,.2f} ₽</b> (за вычетом 40% комиссии).\n"
                f"Вывод доступен во вторник и четверг.",
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

    if deal["status"] not in ["created", "waiting_payment"]:
        await call.answer("Оплаченную сделку нельзя отменить самостоятельно!", show_alert=True)
        return

    await db_execute("UPDATE deals SET status = 'cancelled' WHERE id = ?", (deal_id,))
    await call.answer("Сделка аннулирована.")
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
        await call.answer("Спор возможен только по оплаченной сделке.", show_alert=True)
        return

    await db_execute("UPDATE deals SET status = 'dispute' WHERE id = ?", (deal_id,))
    await call.answer("⚖️ Спор открыт. Арбитр вызван.", show_alert=True)

    try:
        await bot.send_message(
            ADMIN_ID,
            f"🚨 <b>ОТКРЫТ СПОР ПО СДЕЛКЕ №{deal_id}!</b>\n\n"
            f"Инициатор: <code>{user_id}</code>\n"
            f"Сумма: <b>{deal['amount']:,.2f} ₽</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔎 Открыть сделку", callback_data=f"view_deal:{deal_id}")]
            ])
        )
    except Exception:
        pass

    await render_deal_view(call.message, deal_id, user_id, is_edit=True)

# ══════════════════════════════════════════════════════════════════════════════
# АНОНИМНЫЙ МЕДИА-ЧАТ ПО СДЕЛКЕ
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
        f"💬 <b>Анонимный чат по сделке №{deal_id} активен!</b>\n\n"
        f"Вы общаетесь как: <b>{role_label}</b>\n"
        f"Все текстовые сообщения, фото, файлы и голосовые будут анонимно пересылаться партнеру.\n\n"
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

# ══════════════════════════════════════════════════════════════════════════════
# СИСТЕМА ОТЗЫВОВ И РЕЙТИНГОВ (FUNPAY / KWORK STYLE)
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("leave_review:"))
async def cb_leave_review(call: CallbackQuery, state: FSMContext):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal or deal["buyer_id"] != user_id:
        await call.answer("Только покупатель оставляет отзыв продавцу.", show_alert=True)
        return

    # Проверка на повторный отзыв
    existing = await db_get_one("SELECT * FROM reviews WHERE deal_id = ?", (deal_id,))
    if existing:
        await call.answer("Вы уже оставили отзыв по этой сделке.", show_alert=True)
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
    await call.message.edit_text("⭐ <b>Оцените качество работы и честность Продавца (1-5):</b>", reply_markup=kb)

@router.callback_query(ReviewStates.rating, F.data.startswith("rate:"))
async def cb_rate_score(call: CallbackQuery, state: FSMContext):
    score = int(call.data.split(":")[1])
    await state.update_data(rating=score)
    await state.set_state(ReviewStates.comment)
    await call.message.edit_text(
        f"Вы выбрали: <b>{score} / 5 ⭐</b>\n\n"
        f"📝 <b>Напишите текст отзыва:</b>\n"
        f"<i>(Опишите, как прошла сделка, или отправьте «-», чтобы оставить без комментария)</i>"
    )

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
    await message.answer("🎉 <b>Спасибо! Ваш отзыв опубликован в публичном профиле Продавца.</b>", reply_markup=get_main_keyboard())

@router.callback_query(F.data.startswith("view_seller_reviews:"))
async def cb_view_seller_reviews(call: CallbackQuery):
    seller_id = int(call.data.split(":")[1])
    seller = await db_get_one("SELECT * FROM users WHERE user_id = ?", (seller_id,))
    reviews = await db_get_all("SELECT * FROM reviews WHERE target_user_id = ? ORDER BY id DESC LIMIT 5", (seller_id,))

    if not seller:
        await call.answer("Продавец не найден.")
        return

    avg = round(seller["rating_sum"] / seller["rating_count"], 1) if seller["rating_count"] > 0 else 5.0
    pos_percent = round((seller["pos_reviews"] / seller["rating_count"]) * 100) if seller["rating_count"] > 0 else 100

    text = (
        f"⭐ <b>РЕПУТАЦИЯ ПРОДАВЦА {seller['username']}</b>\n"
        f"────────────────────────────\n"
        f"🌟 <b>Рейтинг:</b> {avg} / 5.0\n"
        f"👍 <b>Положительных отзывов:</b> {pos_percent}% ({seller['rating_count']} шт.)\n"
        f"────────────────────────────\n"
        f"<b>Последние отзывы клиентов:</b>\n\n"
    )

    if not reviews:
        text += "<i>У продавца пока нет отзывов. Станьте первым покупателем!</i>\n"
    else:
        for r in reviews:
            stars = "⭐" * r["rating"]
            date_str = r["created_at"][:10]
            text += f"{stars} ({date_str})\n«{r['comment']}»\n\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад к товарам", callback_data="cat_page:0")]])
    await call.message.edit_text(text, reply_markup=kb)

# ══════════════════════════════════════════════════════════════════════════════
# ПРОФИЛЬ, ВЫВОД СРЕДСТВ И СПИСОК СДЕЛОК
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
        f"💰 <b>Баланс к выводу:</b> <b>{user['balance']:,.2f} ₽</b>\n"
        f"⭐ <b>Рейтинг:</b> {avg_rating} / 5.0 (👍 {pos_p}% положительных)\n"
        f"💬 <b>Всего отзывов:</b> {user['rating_count']}\n"
        f"🤝 <b>Успешных сделок:</b> {completed_cnt}\n"
        f"────────────────────────────\n"
        f"💡 <i>Передавайте ваш ID партнеру для быстрого создания сделки.</i>"
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

@router.message(F.text == "💳 Вывод средств")
async def start_withdraw(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user = await get_or_create_user(user_id, message.from_user.username)

    if user["balance"] < MIN_WITHDRAW:
        await message.answer(
            f"💳 <b>Вывод средств</b>\n\n"
            f"Ваш текущий баланс: <b>{user['balance']:,.2f} ₽</b>\n"
            f"Минимальная сумма для вывода: <b>{MIN_WITHDRAW:,.2f} ₽</b>\n\n"
            f"📅 <b>График выплат:</b>\n"
            f"Выплаты производятся <b>каждый вторник и четверг</b>."
        )
        return

    await state.set_state(WithdrawStates.amount)
    await message.answer(
        f"💳 Введите сумму к выводу (доступно: <b>{user['balance']:,.2f} ₽</b>, мин: {MIN_WITHDRAW:,.0f} ₽):",
        reply_markup=get_cancel_reply_kb()
    )

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
    await message.answer("💳 <b>Введите номер карты банка РФ или СБП с названием банка:</b>")

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
            f"💸 <b>НОВАЯ ЗАЯВКА НА ВЫВОД!</b>\n\n"
            f"Пользователь: <code>{user_id}</code>\n"
            f"Сумма: <b>{amount:,.2f} ₽</b>\n"
            f"Реквизиты: <code>{requisites}</code>"
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
        f"🛡 <b>Гарант и P2P-маркетплейс «BastionPRO»</b>\n"
        f"────────────────────────────\n"
        f"• Комиссия платформы: <b>40%</b> (с суммы закрытой сделки Продавца)\n"
        f"• Выплаты баланса: <b>каждый вторник и четверг</b> (от 500 ₽)\n"
        f"• Выставляйте товары через раздел «📦 Мои товары»\n\n"
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
        "• Надежное шифрование военного уровня\n"
        "• Отсутствие логов и ограничений скорости\n"
        "• Мгновенная настройка на смартфонах и ПК",
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
            await bot.send_message(deal["buyer_id"], f"⚖️ По решению арбитража сумма {deal['amount']:,.2f} ₽ возвращена вам на баланс.")
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
            await bot.send_message(deal["seller_id"], f"⚖️ По решению арбитража сумма {deal['seller_receives']:,.2f} ₽ переведена на ваш баланс.")
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
    await message.answer(f"✅ Рассылка завершена! Отправлено пользователям: {sent}", reply_markup=get_main_keyboard())

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
    logger.info("Запуск P2P-маркетплейса и Гаранта BastionPRO...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
