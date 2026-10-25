"""
════════════════════════════════════════════════════════════════════════════════
    BASTION PRO — АВТОГАРАНТ (Aiogram 3.x + Встроенный SQLite WAL)
    • Комиссия платформы: 40%
    • Поддержка .env без внешних зависимостей
    • Не зависает, автоматический сброс состояний и авторегистрация
    • Разделение ролей Покупателя и Продавца
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
# ЗАГРУЗКА .ENV (БЕЗ НЕОБХОДИМОСТИ УСТАНОВКИ DOTENV)
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
DB_FILE = os.getenv("DB_FILE", "bastion_pro_v3.db")
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
# ПОТОКОБЕЗОПАСНЫЙ СЛОЙ БД С РЕЖИМОМ WAL (ИСПРАВЛЯЕТ ЗАВИСАНИЯ)
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
        cursor = conn.cursor()
        cursor.execute(query, params)
        res = None
        if fetch == "one":
            row = cursor.fetchone()
            res = dict(row) if row else None
        elif fetch == "all":
            rows = cursor.fetchall()
            res = [dict(r) for r in rows]
        if commit:
            conn.commit()
            if not fetch:
                res = cursor.lastrowid
        return res

def _sync_transaction(operations: List[tuple]) -> None:
    with _get_connection() as conn:
        cursor = conn.cursor()
        for query, params in operations:
            cursor.execute(query, params)
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
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS deals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        buyer_id INTEGER,
        seller_id INTEGER,
        title TEXT,
        amount REAL,
        fee REAL,
        seller_receives REAL,
        status TEXT DEFAULT 'created',
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
    logger.info("База данных SQLite в режиме WAL успешно подключена.")

async def get_or_create_user(user_id: int, username: Optional[str] = None) -> Dict[str, Any]:
    user = await db_get_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    clean_username = f"@{username}" if username else "Пользователь"
    if not user:
        await db_execute(
            "INSERT INTO users (user_id, username, balance, rating_sum, rating_count, created_at) VALUES (?, ?, 0.0, 0.0, 0, ?)",
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
            logger.warning(f"safe_edit_text warning: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# FSM СОСТОЯНИЯ
# ══════════════════════════════════════════════════════════════════════════════
class CreateDealStates(StatesGroup):
    role = State()
    title = State()
    amount = State()
    partner_mode = State()
    partner_id = State()

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
# ГЛАВНОЕ МЕНЮ И КЛАВИАТУРЫ
# ══════════════════════════════════════════════════════════════════════════════
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
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
# СТАРТ И КРАСИВОЕ ПРИВЕТСТВИЕ
# ══════════════════════════════════════════════════════════════════════════════
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user = await get_or_create_user(user_id, message.from_user.username)

    # Проверка диплинка приглашения в сделку: /start deal_123
    args = command.args
    if args and args.startswith("deal_"):
        try:
            deal_id = int(args.replace("deal_", ""))
            await process_deal_join(message, deal_id, user_id)
            return
        except Exception as e:
            logger.error(f"Ошибка входа по ссылке: {e}")

    welcome_text = (
        f"🛡 <b>Эскроу-сервис «BastionPRO»</b>\n"
        f"<i>Интеллектуальная система безопасных торговых расчетов</i>\n"
        f"────────────────────────────\n"
        f"Здравствуйте, <b>{message.from_user.first_name}</b>!\n\n"
        f"Сервис выступает нейтральной третьей стороной и гарантирует сохранность ваших средств при любых сделках:\n\n"
        f"• <b>🔵 Для Покупателя:</b> Ваши деньги замораживаются на эскроу-счете. Продавец не получит оплату, пока вы лично не подтвердите получение товара или услуги.\n"
        f"• <b>🟢 Для Продавца:</b> Вы гарантированно получаете оплату сразу после передачи обязательств. Покупатель не сможет отозвать средства.\n"
        f"• <b>💬 Защищенный медиа-чат:</b> Общайтесь и передавайте файлы инкогнито без раскрытия личных аккаунтов.\n\n"
        f"📊 <b>Условия работы:</b>\n"
        f"• Комиссия платформы: <b>40%</b> (с суммы закрытой сделки)\n"
        f"• График выплат: <b>каждый вторник и четверг</b> (от 500 ₽)\n"
        f"• Арбитраж и поддержка: <b>{SUPPORT_HANDLE}</b>\n"
        f"────────────────────────────\n"
        f"👇 <i>Воспользуйтесь меню ниже для управления сделками и счетом:</i>"
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard())

async def process_deal_join(message: Message, deal_id: int, user_id: int):
    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal:
        await message.answer("❌ Сделка не найдена или была аннулирована.", reply_markup=get_main_keyboard())
        return

    if deal["buyer_id"] == user_id or deal["seller_id"] == user_id:
        await render_deal_view(message, deal_id, user_id)
        return

    # Если роль покупателя вакантна
    if deal["buyer_id"] is None and deal["seller_id"] != user_id:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔵 Принять роль Покупателя", callback_data=f"join_role:{deal_id}:buyer")],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data="close_view")]
        ])
        await message.answer(
            f"🤝 <b>Вас пригласили в сделку №{deal['id']}!</b>\n\n"
            f"📌 <b>Условия:</b> {deal['title']}\n"
            f"💰 <b>Сумма к оплате:</b> {deal['amount']:,.2f} ₽\n\n"
            f"Вы вступаете как <b>🔵 Покупатель</b>. Средства останутся на гаранте до вашей проверки.",
            reply_markup=kb
        )
        return

    # Если роль продавца вакантна
    elif deal["seller_id"] is None and deal["buyer_id"] != user_id:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Принять роль Продавца", callback_data=f"join_role:{deal_id}:seller")],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data="close_view")]
        ])
        await message.answer(
            f"🤝 <b>Вас пригласили в сделку №{deal['id']}!</b>\n\n"
            f"📌 <b>Условия:</b> {deal['title']}\n"
            f"💰 <b>Стоимость сделки:</b> {deal['amount']:,.2f} ₽\n"
            f"💼 <b>Ваша выплата:</b> {deal['seller_receives']:,.2f} ₽ (за вычетом комиссии 40%)\n\n"
            f"Вы вступаете как <b>🟢 Продавец</b>. Оплату вносит Покупатель.",
            reply_markup=kb
        )
        return
    else:
        await message.answer("⚠️ Все места в данной сделке уже заняты участниками.", reply_markup=get_main_keyboard())

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
        await call.answer("Вы успешно вступили как Покупатель!")
        try:
            await bot.send_message(
                creator_id,
                f"🔔 <b>Покупатель подключился к сделке №{deal_id}!</b>\nОжидается внесение оплаты."
            )
        except Exception:
            pass
        await render_deal_view(call.message, deal_id, user_id, is_edit=True)

    elif role == "seller" and deal["seller_id"] is None:
        await db_execute("UPDATE deals SET seller_id = ?, status = 'waiting_payment' WHERE id = ?", (user_id, deal_id))
        await call.answer("Вы успешно вступили как Продавец!")
        try:
            await bot.send_message(
                creator_id,
                f"🔔 <b>Продавец подключился к сделке №{deal_id}!</b>\nПокупатель может внести депозит."
            )
        except Exception:
            pass
        await render_deal_view(call.message, deal_id, user_id, is_edit=True)
    else:
        await call.answer("Данная роль уже занята.", show_alert=True)

# ══════════════════════════════════════════════════════════════════════════════
# СОЗДАНИЕ СДЕЛКИ: УЛУЧШЕННОЕ МЕНЮ (КАК НА СКРИНШОТЕ)
# ══════════════════════════════════════════════════════════════════════════════
@router.message(F.text == "➕ Создать сделку")
async def start_create_deal(message: Message, state: FSMContext):
    await state.clear()
    await get_or_create_user(message.from_user.id, message.from_user.username)

    text = (
        "🎯 <b>Шаг 1 из 4. Выберите вашу роль:</b>\n"
        "<code>[● ○ ○ ○] Инициализация сделки</code>\n"
        "────────────────────────────\n"
        "• 🔵 <b>Покупатель:</b> Вы оплачиваете заказ через гарант. Деньги замораживаются до вашей проверки.\n"
        "• 🟢 <b>Продавец:</b> Вы передаете товар только <b>ПОСЛЕ</b> того, как покупатель внесет депозит в гарант.\n"
        "────────────────────────────\n"
        "<i>Нажмите на кнопку с вашей ролью для перехода к описанию:</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔵 Я Покупатель (вношу оплату)", callback_data="create_as:buyer")],
        [InlineKeyboardButton(text="🟢 Я Продавец (передаю товар)", callback_data="create_as:seller")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")]
    ])
    await message.answer(text, reply_markup=kb)

@router.callback_query(F.data.startswith("create_as:"))
async def cb_select_creator_role(call: CallbackQuery, state: FSMContext):
    role = call.data.split(":")[1]
    await state.update_data(creator_role=role)
    await state.set_state(CreateDealStates.title)

    role_badge = "🔵 Покупатель (вношу оплату)" if role == "buyer" else "🟢 Продавец (передаю товар)"
    await call.message.edit_text(
        f"🎯 <b>Шаг 2 из 4. Описание сделки</b>\n"
        f"<code>[● ● ○ ○] Выбрана роль: {role_badge}</code>\n"
        f"────────────────────────────\n"
        f"📝 <b>Введите предмет или техническое задание сделки:</b>\n\n"
        f"<i>Пример: Покупка Telegram-канала с передачей прав / Разработка Telegram-бота / Покупка ключа</i>"
    )

@router.message(CreateDealStates.title)
async def process_deal_title(message: Message, state: FSMContext):
    if len(message.text) < 3 or len(message.text) > 250:
        await message.answer("⚠️ Описание должно быть от 3 до 250 символов. Введите повторно:")
        return

    await state.update_data(title=message.text.strip())
    await state.set_state(CreateDealStates.amount)
    data = await state.get_data()

    if data.get("creator_role") == "buyer":
        hint = "Введите сумму, которую вы готовы внести в гарант (в рублях):"
    else:
        hint = "Введите полную стоимость товара/услуги (в рублях).\nПокупатель внесет эту сумму, а вы получите 60% после закрытия сделки:"

    await message.answer(
        f"💰 <b>Шаг 3 из 4. Сумма сделки</b>\n"
        f"<code>[● ● ● ○] Финансовые условия</code>\n"
        f"────────────────────────────\n"
        f"{hint}\n\n"
        f"<i>(Минимальная сумма — 100 ₽)</i>",
        reply_markup=get_cancel_reply_kb()
    )

@router.message(CreateDealStates.amount)
async def process_deal_amount(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Создание сделки отменено.", reply_markup=get_main_keyboard())
        return

    try:
        amount = float(message.text.replace(",", ".").strip())
        if amount < 100.0 or amount > 500000.0:
            await message.answer("⚠️ Сумма должна быть от 100 до 500 000 ₽. Попробуйте еще раз:")
            return
    except ValueError:
        await message.answer("⚠️ Пожалуйста, введите корректное число (например: 1500 или 250.50):")
        return

    fee = round(amount * COMMISSION_RATE, 2)
    seller_receives = round(amount - fee, 2)

    await state.update_data(amount=amount, fee=fee, seller_receives=seller_receives)
    await state.set_state(CreateDealStates.partner_mode)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Сформировать ссылку для партнера", callback_data="partner_mode:link")],
        [InlineKeyboardButton(text="🆔 Указать Telegram ID партнера", callback_data="partner_mode:id")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")]
    ])

    await message.answer(
        f"📊 <b>Шаг 4 из 4. Расчет и приглашение</b>\n"
        f"<code>[● ● ● ●] Подтверждение параметров</code>\n"
        f"────────────────────────────\n"
        f"• Полная сумма сделки: <b>{amount:,.2f} ₽</b>\n"
        f"• Комиссия гаранта (40%): <b>{fee:,.2f} ₽</b>\n"
        f"• Выплата Продавцу: <b>{seller_receives:,.2f} ₽</b>\n"
        f"────────────────────────────\n"
        f"👥 <b>Выберите способ подключения второго участника:</b>",
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
        other_role = "Продавца" if creator_role == "buyer" else "Покупателя"
        await call.message.edit_text(
            f"🆔 <b>Введите цифровой Telegram ID {other_role}:</b>\n\n"
            f"<i>(Партнер может узнать свой ID в боте @userinfobot)</i>"
        )
        return

    buyer_id = user_id if creator_role == "buyer" else None
    seller_id = user_id if creator_role == "seller" else None

    deal_id = await db_execute(
        """INSERT INTO deals (buyer_id, seller_id, title, amount, fee, seller_receives, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'created', ?)""",
        (buyer_id, seller_id, data["title"], data["amount"], data["fee"], data["seller_receives"], datetime.now().isoformat())
    )

    await state.clear()
    bot_info = await bot.get_me()
    invite_url = f"https://t.me/{bot_info.username}?start=deal_{deal_id}"
    other_role_str = "Продавцу" if creator_role == "buyer" else "Покупателю"

    await call.message.edit_text(
        f"✅ <b>Сделка №{deal_id} успешно создана!</b>\n\n"
        f"Отправьте персональную ссылку {other_role_str}:\n"
        f"👉 <code>{invite_url}</code>\n\n"
        f"При входе партнер автоматически получит статус "
        f"<b>{'🟢 Продавца' if creator_role == 'buyer' else '🔵 Покупателя'}</b>.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔎 Открыть карточку сделки", callback_data=f"view_deal:{deal_id}")]
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
            await message.answer("⚠️ Вы не можете указать свой личный ID. Введите ID второго участника:")
            return
    except ValueError:
        await message.answer("⚠️ Telegram ID состоит только из цифр. Повторите ввод:")
        return

    data = await state.get_data()
    creator_id = message.from_user.id
    creator_role = data["creator_role"]

    if creator_role == "buyer":
        buyer_id, seller_id = creator_id, partner_id
    else:
        buyer_id, seller_id = partner_id, creator_id

    deal_id = await db_execute(
        """INSERT INTO deals (buyer_id, seller_id, title, amount, fee, seller_receives, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'waiting_payment', ?)""",
        (buyer_id, seller_id, data["title"], data["amount"], data["fee"], data["seller_receives"], datetime.now().isoformat())
    )

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

# ══════════════════════════════════════════════════════════════════════════════
# КАРТОЧКА СДЕЛКИ И РАЗГРАНИЧЕНИЕ ПРАВ
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
        "paid": "🛡 Оплачено (средства на эскроу-счете)",
        "shipped": "📦 Товар передан Продавцом (на проверке)",
        "dispute": "⚖️ Открыт спор (дело передано в арбитраж)",
        "completed": "✅ Успешно завершена",
        "cancelled": "🚫 Сделка аннулирована"
    }

    if is_buyer:
        my_role_str = "🔵 Вы: <b>Покупатель</b>"
    elif is_seller:
        my_role_str = "🟢 Вы: <b>Продавец</b>"
    else:
        my_role_str = "👑 Вы: <b>Администратор</b>"

    text = (
        f"📋 <b>КАРТОЧКА СДЕЛКИ №{deal['id']}</b>\n"
        f"────────────────────────────\n"
        f"📌 <b>Предмет сделки:</b> {deal['title']}\n"
        f"💰 <b>Сумма сделки:</b> {deal['amount']:,.2f} ₽\n"
        f"💼 <b>К выплате Продавцу:</b> {deal['seller_receives']:,.2f} ₽ <i>(комиссия сервиса 40%: {deal['fee']:,.2f} ₽)</i>\n"
        f"🚦 <b>Текущий статус:</b> {status_map.get(deal['status'], deal['status'])}\n"
        f"👤 {my_role_str}\n"
        f"────────────────────────────\n"
    )

    kb_buttons = []

    # 1. Ожидание оплаты
    if deal["status"] in ["created", "waiting_payment"]:
        if is_buyer:
            payment_url = f"https://app.paritypay.ru/pay/{PARITYPAY_SHOP_ID}?order_id={deal['id']}&amount={deal['amount']}"
            kb_buttons.append([InlineKeyboardButton(text="💳 Оплатить через ParityPay", url=payment_url)])
            kb_buttons.append([InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_pay:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="❌ Отменить сделку", callback_data=f"cancel_deal:{deal['id']}")])
            text += "\n👉 <i>Внесите оплату в гарант для старта выполнения обязательств.</i>"
        elif is_seller:
            kb_buttons.append([InlineKeyboardButton(text="❌ Отменить сделку", callback_data=f"cancel_deal:{deal['id']}")])
            text += "\n⏳ <i>Ожидайте оплаты от Покупателя. <b>НЕ ПЕРЕДАВАЙТЕ</b> товар, пока статус не изменится на «Оплачено»!</i>"

    # 2. Оплачено
    elif deal["status"] == "paid":
        if is_seller:
            kb_buttons.append([InlineKeyboardButton(text="📦 Товар/услуга передана", callback_data=f"ship_deal:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="💬 Анонимный чат", callback_data=f"open_chat:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="⚖️ Открыть спор", callback_data=f"open_dispute:{deal['id']}")])
            text += "\n💰 <i>Деньги в гаранте! Передайте товар Покупателю и нажмите «Товар/услуга передана».</i>"
        elif is_buyer:
            kb_buttons.append([InlineKeyboardButton(text="💬 Анонимный чат", callback_data=f"open_chat:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="⚖️ Открыть спор", callback_data=f"open_dispute:{deal['id']}")])
            text += "\n🛡 <i>Ваши деньги зарезервированы. Ожидайте передачи товара от Продавца.</i>"

    # 3. Передано (на проверке)
    elif deal["status"] == "shipped":
        if is_buyer:
            kb_buttons.append([InlineKeyboardButton(text="✅ Подтвердить получение", callback_data=f"confirm_deal:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="💬 Анонимный чат", callback_data=f"open_chat:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="⚖️ Открыть спор", callback_data=f"open_dispute:{deal['id']}")])
            text += "\n📦 <i>Продавец отправил товар. Проверьте заказ и нажмите кнопку подтверждения.</i>"
        elif is_seller:
            kb_buttons.append([InlineKeyboardButton(text="💬 Анонимный чат", callback_data=f"open_chat:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="⚖️ Открыть спор", callback_data=f"open_dispute:{deal['id']}")])
            text += "\n⏳ <i>Заказ на проверке у Покупателя. Средства будут переведены после его подтверждения.</i>"

    # 4. Спор
    elif deal["status"] == "dispute":
        kb_buttons.append([InlineKeyboardButton(text="💬 Чат участников", callback_data=f"open_chat:{deal['id']}")])
        text += f"\n⚖️ <i>Сделка в арбитраже. Администратор рассматривает доказательства сторон. Поддержка: {SUPPORT_HANDLE}</i>"

    # 5. Завершена
    elif deal["status"] == "completed":
        if is_buyer:
            kb_buttons.append([InlineKeyboardButton(text="⭐ Оценить работу Продавца", callback_data=f"leave_review:{deal['id']}")])
        text += "\n🎉 <i>Сделка успешно завершена. Расчет произведен.</i>"

    kb_buttons.append([InlineKeyboardButton(text="🔄 Обновить статус", callback_data=f"view_deal:{deal['id']}")])

    if is_admin and deal["status"] == "dispute":
        kb_buttons.append([
            InlineKeyboardButton(text="👑 Арбитраж: Вернуть Покупателю", callback_data=f"admin_refund:{deal['id']}"),
            InlineKeyboardButton(text="👑 Арбитраж: Выплатить Продавцу", callback_data=f"admin_payout:{deal['id']}")
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
# ЭТАПЫ СДЕЛКИ: ОПЛАТА, ПЕРЕДАЧА, ПОДТВЕРЖДЕНИЕ
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("check_pay:"))
async def cb_check_pay(call: CallbackQuery):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal or deal["buyer_id"] != user_id:
        await call.answer("Только покупатель подтверждает оплату.", show_alert=True)
        return

    # Фиксируем оплату сделки
    await db_execute("UPDATE deals SET status = 'paid' WHERE id = ?", (deal_id,))
    await call.answer("✅ Оплата подтверждена! Средства зачислены в гарант.", show_alert=True)

    if deal["seller_id"]:
        try:
            await bot.send_message(
                deal["seller_id"],
                f"💰 <b>Покупатель внес оплату по сделке №{deal_id}!</b>\n"
                f"Средства находятся на эскроу-счете. Передайте заказ Покупателю.",
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
        await call.answer("Только Продавец может подтвердить передачу.", show_alert=True)
        return

    await db_execute("UPDATE deals SET status = 'shipped' WHERE id = ?", (deal_id,))
    await call.answer("Покупатель уведомлен об отправке товара.")

    if deal["buyer_id"]:
        try:
            await bot.send_message(
                deal["buyer_id"],
                f"📦 <b>Продавец передал заказ по сделке №{deal_id}!</b>\n"
                f"Пожалуйста, проверьте товар и нажмите «Подтвердить получение».",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔎 Проверить сделку", callback_data=f"view_deal:{deal_id}")]
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
        await call.answer("Только Покупатель закрывает сделку.", show_alert=True)
        return

    if deal["status"] not in ["paid", "shipped"]:
        await call.answer("Невозможно закрыть сделку в текущем статусе.", show_alert=True)
        return

    # Зачисление средств продавцу (60%) и в хранилище платформы (40%)
    await db_transaction([
        ("UPDATE users SET balance = balance + ? WHERE user_id = ?", (deal["seller_receives"], deal["seller_id"])),
        ("INSERT INTO platform_vault (deal_id, fee_amount, created_at) VALUES (?, ?, ?)", (deal_id, deal["fee"], datetime.now().isoformat())),
        ("UPDATE deals SET status = 'completed' WHERE id = ?", (deal_id,))
    ])

    await call.answer("✅ Сделка успешно завершена! Выплата начислена Продавцу.", show_alert=True)

    if deal["seller_id"]:
        try:
            await bot.send_message(
                deal["seller_id"],
                f"🎉 <b>Сделка №{deal_id} успешно закрыта!</b>\n\n"
                f"💰 На ваш баланс зачислено: <b>{deal['seller_receives']:,.2f} ₽</b> (за вычетом 40% комиссии).\n"
                f"Вы можете запросить вывод средств в главном меню.",
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
        await call.answer("Оплаченную сделку нельзя отменить без решения арбитража!", show_alert=True)
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
        await call.answer("Спор можно открыть только по оплаченной сделке.", show_alert=True)
        return

    await db_execute("UPDATE deals SET status = 'dispute' WHERE id = ?", (deal_id,))
    await call.answer("⚖️ Дело передано в арбитраж администратору.", show_alert=True)

    try:
        await bot.send_message(
            ADMIN_ID,
            f"🚨 <b>ОТКРЫТ СПОР ПО СДЕЛКЕ №{deal_id}!</b>\n\n"
            f"Инициатор спора: <code>{user_id}</code>\n"
            f"Сумма: <b>{deal['amount']:,.2f} ₽</b>\n"
            f"Предмет: {deal['title']}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔎 Открыть сделку", callback_data=f"view_deal:{deal_id}")]
            ])
        )
    except Exception:
        pass

    await render_deal_view(call.message, deal_id, user_id, is_edit=True)

# ══════════════════════════════════════════════════════════════════════════════
# АНОНИМНЫЙ МЕДИА-ЧАТ
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("open_chat:"))
async def cb_open_chat(call: CallbackQuery, state: FSMContext):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal or (deal["buyer_id"] != user_id and deal["seller_id"] != user_id):
        await call.answer("Вы не участник этой сделки.", show_alert=True)
        return

    await state.set_state(ChatProxyStates.in_chat)
    await state.update_data(deal_id=deal_id)

    role_label = "🔵 Покупатель" if user_id == deal["buyer_id"] else "🟢 Продавец"
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🚪 Покинуть диалог")]], resize_keyboard=True)

    await call.message.answer(
        f"💬 <b>Анонимный чат по сделке №{deal_id} активен!</b>\n\n"
        f"Ваш статус: <b>{role_label}</b>\n"
        f"Все текстовые сообщения, голосовые, файлы и фото пересылаются собеседнику без раскрытия вашего профиля.\n\n"
        f"Для выхода нажмите кнопку <b>«🚪 Покинуть диалог»</b>.",
        reply_markup=kb
    )
    await call.answer()

@router.message(ChatProxyStates.in_chat, F.text == "🚪 Покинуть диалог")
async def exit_proxy_chat(message: Message, state: FSMContext):
    data = await state.get_data()
    deal_id = data.get("deal_id")
    await state.clear()
    await message.answer("🚪 Вы покинули анонимный чат.", reply_markup=get_main_keyboard())
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
        await message.answer("⚠️ Не удалось доставить сообщение собеседнику.")

# ══════════════════════════════════════════════════════════════════════════════
# ПРОФИЛЬ (РАБОТАЕТ БЕЗ СБОЕВ)
# ══════════════════════════════════════════════════════════════════════════════
@router.message(F.text.in_(["👤 Профиль", "Профиль"]))
async def show_profile(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user = await get_or_create_user(user_id, message.from_user.username)

    avg_rating = round(user["rating_sum"] / user["rating_count"], 2) if user["rating_count"] > 0 else 0.0

    completed = await db_get_one(
        "SELECT COUNT(*) as cnt FROM deals WHERE (buyer_id = ? OR seller_id = ?) AND status = 'completed'",
        (user_id, user_id)
    )
    completed_cnt = completed["cnt"] if completed else 0

    text = (
        f"👤 <b>ЛИЧНЫЙ ПРОФИЛЬ УЧАСТНИКА</b>\n"
        f"────────────────────────────\n"
        f"🆔 <b>Ваш Telegram ID:</b> <code>{user_id}</code>\n"
        f"💰 <b>Баланс к выводу:</b> <b>{user['balance']:,.2f} ₽</b>\n"
        f"⭐ <b>Рейтинг доверия:</b> {avg_rating} / 5.0 (Отзывов: {user['rating_count']})\n"
        f"🤝 <b>Успешных сделок:</b> {completed_cnt}\n"
        f"────────────────────────────\n"
        f"💡 <i>Передавайте ваш Telegram ID партнеру для быстрого создания сделки.</i>"
    )
    await message.answer(text, reply_markup=get_main_keyboard())

# ══════════════════════════════════════════════════════════════════════════════
# ВЫВОД СРЕДСТВ (КАЖДЫЙ ВТОРНИК И ЧЕТВЕРГ)
# ══════════════════════════════════════════════════════════════════════════════
@router.message(F.text.in_(["💳 Вывод средств", "Вывод средств"]))
async def start_withdraw(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user = await get_or_create_user(user_id, message.from_user.username)

    if user["balance"] < MIN_WITHDRAW:
        await message.answer(
            f"💳 <b>Вывод средств с баланса</b>\n"
            f"────────────────────────────\n"
            f"Ваш текущий баланс: <b>{user['balance']:,.2f} ₽</b>\n"
            f"Минимальная сумма для вывода: <b>{MIN_WITHDRAW:,.2f} ₽</b>\n\n"
            f"📅 <b>График выплат:</b>\n"
            f"Выплаты производятся <b>каждый вторник и четверг</b>.\n"
            f"────────────────────────────\n"
            f"<i>Завершите сделки в роли Продавца для пополнения баланса.</i>",
            reply_markup=get_main_keyboard()
        )
        return

    await state.set_state(WithdrawStates.amount)
    await message.answer(
        f"💳 <b>Оформление заявки на вывод</b>\n\n"
        f"Доступно к выводу: <b>{user['balance']:,.2f} ₽</b>\n"
        f"📅 <i>Выплаты производятся каждый вторник и четверг.</i>\n\n"
        f"Введите сумму, которую хотите вывести (минимум {MIN_WITHDRAW:,.0f} ₽):",
        reply_markup=get_cancel_reply_kb()
    )

@router.message(WithdrawStates.amount)
async def process_withdraw_amount(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Вывод средств отменен.", reply_markup=get_main_keyboard())
        return

    user = await get_or_create_user(message.from_user.id)
    try:
        val = float(message.text.replace(",", ".").strip())
        if val < MIN_WITHDRAW or val > user["balance"]:
            await message.answer(f"⚠️ Сумма должна быть от {MIN_WITHDRAW:,.0f} ₽ и не превышать {user['balance']:,.2f} ₽. Введите повторно:")
            return
    except ValueError:
        await message.answer("⚠️ Введите корректное число (например: 750):")
        return

    await state.update_data(amount=val)
    await state.set_state(WithdrawStates.requisites)
    await message.answer(
        "💳 <b>Введите ваши реквизиты:</b>\n\n"
        "<i>(Номер карты банка РФ или номер телефона СБП с указанием названия банка)</i>"
    )

@router.message(WithdrawStates.requisites)
async def process_withdraw_reqs(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Вывод средств отменен.", reply_markup=get_main_keyboard())
        return

    data = await state.get_data()
    amount = data["amount"]
    requisites = message.text.strip()
    user_id = message.from_user.id

    # Списание с баланса и создание заявки
    await db_transaction([
        ("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id)),
        ("INSERT INTO withdrawals (user_id, amount, requisites, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
         (user_id, amount, requisites, datetime.now().isoformat()))
    ])

    await state.clear()
    await message.answer(
        f"✅ <b>Заявка на вывод средств принята!</b>\n\n"
        f"💰 Сумма: <b>{amount:,.2f} ₽</b>\n"
        f"Реквизиты: <code>{requisites}</code>\n\n"
        f"📅 Средства поступят на указанный счет в ближайший расчетный день (<b>вторник</b> или <b>четверг</b>).",
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
# МОИ СДЕЛКИ
# ══════════════════════════════════════════════════════════════════════════════
@router.message(F.text.in_(["📂 Мои сделки", "Мои сделки"]))
async def show_my_deals(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    await get_or_create_user(user_id, message.from_user.username)

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

# ══════════════════════════════════════════════════════════════════════════════
# ОТЗЫВЫ И РЕЙТИНГ
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("leave_review:"))
async def cb_leave_review(call: CallbackQuery, state: FSMContext):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal or deal["buyer_id"] != user_id:
        await call.answer("Только Покупатель может оставить отзыв Продавцу.", show_alert=True)
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
    await call.message.edit_text("⭐ <b>Пожалуйста, оцените качество работы Продавца:</b>", reply_markup=kb)

@router.callback_query(ReviewStates.rating, F.data.startswith("rate:"))
async def cb_rate_score(call: CallbackQuery, state: FSMContext):
    score = int(call.data.split(":")[1])
    await state.update_data(rating=score)
    await state.set_state(ReviewStates.comment)
    await call.message.edit_text(f"Оценка: <b>{score} / 5 ⭐</b>\n\nНапишите краткий комментарий к сделке (или отправьте «-»):")

@router.message(ReviewStates.comment)
async def process_review_comment(message: Message, state: FSMContext):
    txt = message.text.strip()
    comment = "Без комментария" if txt == "-" else txt
    data = await state.get_data()

    await db_transaction([
        ("INSERT INTO reviews (deal_id, target_user_id, author_user_id, rating, comment, created_at) VALUES (?, ?, ?, ?, ?, ?)",
         (data["deal_id"], data["target_id"], message.from_user.id, data["rating"], comment, datetime.now().isoformat())),
        ("UPDATE users SET rating_sum = rating_sum + ?, rating_count = rating_count + 1 WHERE user_id = ?",
         (data["rating"], data["target_id"]))
    ])

    await state.clear()
    await message.answer("🎉 Спасибо! Ваш отзыв зафиксирован в профиле Продавца.", reply_markup=get_main_keyboard())

# ══════════════════════════════════════════════════════════════════════════════
# ПОДДЕРЖКА И ВНЕШНИЕ СЕРВИСЫ
# ══════════════════════════════════════════════════════════════════════════════
@router.message(F.text.in_(["ℹ️ Помощь / Поддержка", "Помощь / Поддержка"]))
async def show_help(message: Message, state: FSMContext):
    await state.clear()
    text = (
        f"🛡 <b>Гарант-сервис «BastionPRO»</b>\n"
        f"────────────────────────────\n"
        f"• Комиссия платформы: <b>40%</b> (вычитается при успешной сделке с Продавца)\n"
        f"• Минимальная сумма сделки: <b>100 ₽</b>\n"
        f"• Выплаты баланса: <b>каждый вторник и четверг</b> (от 500 ₽)\n\n"
        f"По всем спорным ситуациям, арбитражу и вопросам сотрудничества:\n"
        f"👨‍💻 <b>Официальный контакт:</b> {SUPPORT_HANDLE}"
    )
    await message.answer(text, reply_markup=get_main_keyboard())

@router.message(F.text == "🌐 Подключить OnyxVPN")
async def open_onyx_vpn(message: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Запустить OnyxVPN", url="https://t.me/vpnonyxxbot")]
    ])
    await message.answer(
        "🌐 <b>OnyxVPN — безопасный высокоскоростной интернет</b>\n\n"
        "• Надежное шифрование военного уровня\n"
        "• Высокая скорость и отсутствие ограничений трафика\n"
        "• Быстрое подключение в одно нажатие",
        reply_markup=kb
    )

# ══════════════════════════════════════════════════════════════════════════════
# АДМИНИСТРАТИВНЫЙ АРБИТРАЖ
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id != ADMIN_ID:
        return

    users_row = await db_get_one("SELECT COUNT(*) as c FROM users")
    deals_row = await db_get_one("SELECT COUNT(*) as c FROM deals WHERE status = 'completed'")
    vault_row = await db_get_one("SELECT SUM(fee_amount) as s FROM platform_vault")

    total_users = users_row["c"] if users_row else 0
    completed_deals = deals_row["c"] if deals_row else 0
    vault_sum = (vault_row["s"] if vault_row and vault_row["s"] else 0.0)

    text = (
        f"👑 <b>ПАНЕЛЬ УПРАВЛЕНИЯ АДМИНИСТРАТОРА</b>\n"
        f"────────────────────────────\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"✅ Закрытых сделок: <b>{completed_deals}</b>\n"
        f"💰 Собрано комиссий сервиса (40%): <b>{vault_sum:,.2f} ₽</b>\n"
        f"────────────────────────────"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Рассылка всем", callback_data="admin_broadcast")]
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
        await call.answer("Возврат средств покупателю выполнен.", show_alert=True)
        try:
            await bot.send_message(deal["buyer_id"], f"⚖️ По решению арбитража сумма {deal['amount']:,.2f} ₽ возвращена на ваш баланс.")
            await bot.send_message(deal["seller_id"], f"⚖️ По решению арбитража спор по сделке №{deal_id} решен в пользу Покупателя.")
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
            await bot.send_message(deal["seller_id"], f"⚖️ По решению арбитража сумма {deal['seller_receives']:,.2f} ₽ зачислена на ваш баланс.")
            await bot.send_message(deal["buyer_id"], f"⚖️ По решению арбитража сделка №{deal_id} закрыта в пользу Продавца.")
        except Exception:
            pass
        await render_deal_view(call.message, deal_id, call.from_user.id, is_edit=True)

@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminBroadcast.text)
    await call.message.answer("📢 Введите текст сообщения для рассылки всем пользователям:", reply_markup=get_cancel_reply_kb())
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
    await message.answer(f"✅ Рассылка успешно завершена! Доставлено: {sent} пользователям.", reply_markup=get_main_keyboard())

# Отмена любых действий
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
    logger.info("Запуск гарант-сервиса BastionPRO...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
