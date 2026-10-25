"""
════════════════════════════════════════════════════════════════════════════════
    BASTION PRO — АВТОГАРАНТ (Aiogram 3.x + Встроенный sqlite3)
    Полная однофайловая сборка с выверенной ролевой моделью
    Работает БЕЗ aiosqlite (на стандартной библиотеке Python)
════════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import logging
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
# КОНФИГУРАЦИЯ
# ══════════════════════════════════════════════════════════════════════════════
BOT_TOKEN = "8882817041:AAHh1NEZEjKBDQg9l7aOZ4rrWRkvYUc7snI"  # Токен бота
ADMIN_ID = 5494544187                                            # Telegram ID админа
DB_FILE = "bastion_pro_v3.db"                                     # Файл базы данных
SUPPORT_HANDLE = "@vladofix97"
COMMISSION_RATE = 0.40                                           # Комиссия 40%
MIN_WITHDRAW = 500.0                                             # Минимальный вывод (руб)
PARITYPAY_SHOP_ID = "YOUR_SHOP_ID"                               # Идентификатор магазина

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# ══════════════════════════════════════════════════════════════════════════════
# АСИНХРОННЫЙ СЛОЙ ДЛЯ СТАНДАРТНОГО SQLITE3 (НЕ ТРЕБУЕТ AIOSQLITE)
# ══════════════════════════════════════════════════════════════════════════════
def _sync_execute(query: str, params: tuple = (), fetch: Optional[str] = None, commit: bool = False) -> Any:
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        conn.row_factory = sqlite3.Row
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
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
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
        with sqlite3.connect(DB_FILE) as conn:
            conn.executescript(schema)
            conn.commit()
    await asyncio.to_thread(_create)
    logger.info("База данных SQLite успешно инициализирована.")

async def register_user(user_id: int, username: Optional[str]):
    uname = f"@{username}" if username else "Пользователь"
    await db_execute(
        "INSERT OR IGNORE INTO users (user_id, username, balance, rating_sum, rating_count, created_at) VALUES (?, ?, 0.0, 0.0, 0, ?)",
        (user_id, uname, datetime.now().isoformat())
    )
    if username:
        await db_execute("UPDATE users SET username = ? WHERE user_id = ?", (uname, user_id))

async def safe_edit_text(message: Message, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None):
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning(f"Ошибка safe_edit: {e}")

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
# КЛАВИАТУРЫ
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
# СТАРТ И ВХОД ПО ССЫЛКЕ
# ══════════════════════════════════════════════════════════════════════════════
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    await register_user(user_id, message.from_user.username)

    args = command.args
    if args and args.startswith("deal_"):
        try:
            deal_id = int(args.replace("deal_", ""))
            await process_deal_join(message, deal_id, user_id)
            return
        except Exception as e:
            logger.error(f"Ошибка диплинка: {e}")

    await message.answer(
        "🛡 <b>Добро пожаловать в Гарант-сервис «BastionPRO»!</b>\n\n"
        "Мы обеспечиваем полную безопасность онлайн-сделок:\n"
        "• <b>Покупатель</b> защищён от потери денег — средства не переводятся до проверки товара.\n"
        "• <b>Продавец</b> гарантированно получает оплату после выполнения обязательств.\n"
        "• Анонимный медиа-чат скрывает ваши прямые Telegram-аккаунты.",
        reply_markup=get_main_keyboard()
    )

async def process_deal_join(message: Message, deal_id: int, user_id: int):
    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal:
        await message.answer("❌ Сделка не найдена.", reply_markup=get_main_keyboard())
        return

    # Если пользователь уже участник
    if deal["buyer_id"] == user_id or deal["seller_id"] == user_id:
        await render_deal_view(message, deal_id, user_id)
        return

    # Если сделку создал Продавец -> новому участнику предлагаем роль Покупателя
    if deal["buyer_id"] is None and deal["seller_id"] != user_id:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔵 Принять роль Покупателя", callback_data=f"join_role:{deal_id}:buyer")],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data="close_view")]
        ])
        await message.answer(
            f"🤝 <b>Приглашение в сделку №{deal['id']}!</b>\n\n"
            f"📌 Условия: <b>{deal['title']}</b>\n"
            f"💰 Сумма к оплате: <b>{deal['amount']:,.2f} ₽</b>\n\n"
            f"Вы вступаете как <b>🔵 Покупатель</b>. Вы внесёте средства в гарант и подтвердите получение после проверки.",
            reply_markup=kb
        )
        return

    # Если сделку создал Покупатель -> новому участнику предлагаем роль Продавца
    elif deal["seller_id"] is None and deal["buyer_id"] != user_id:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Принять роль Продавца", callback_data=f"join_role:{deal_id}:seller")],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data="close_view")]
        ])
        await message.answer(
            f"🤝 <b>Приглашение в сделку №{deal['id']}!</b>\n\n"
            f"📌 Условия: <b>{deal['title']}</b>\n"
            f"💰 Стоимость: <b>{deal['amount']:,.2f} ₽</b>\n"
            f"💼 Ваша выплата: <b>{deal['seller_receives']:,.2f} ₽</b> (за вычетом 40% комиссии)\n\n"
            f"Вы вступаете как <b>🟢 Продавец</b>. Оплату вносит Покупатель.",
            reply_markup=kb
        )
        return
    else:
        await message.answer("⚠️ Все роли в данной сделке уже заняты.", reply_markup=get_main_keyboard())

@router.callback_query(F.data.startswith("join_role:"))
async def cb_join_role(call: CallbackQuery):
    _, deal_id_str, role = call.data.split(":")
    deal_id = int(deal_id_str)
    user_id = call.from_user.id
    await register_user(user_id, call.from_user.username)

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
                f"🔔 <b>Продавец подключился к сделке №{deal_id}!</b>\nПокупатель может внести оплату."
            )
        except Exception:
            pass
        await render_deal_view(call.message, deal_id, user_id, is_edit=True)
    else:
        await call.answer("Роль уже занята.", show_alert=True)

# ══════════════════════════════════════════════════════════════════════════════
# СОЗДАНИЕ СДЕЛКИ
# ══════════════════════════════════════════════════════════════════════════════
@router.message(F.text == "➕ Создать сделку")
async def start_create_deal(message: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔵 Я Покупатель (вношу оплату)", callback_data="create_as:buyer")],
        [InlineKeyboardButton(text="🟢 Я Продавец (передаю товар)", callback_data="create_as:seller")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")]
    ])
    await message.answer(
        "🎯 <b>Шаг 1 из 4. Выберите вашу роль:</b>\n\n"
        "• <b>🔵 Покупатель:</b> Вы оплачиваете заказ через гарант. Деньги замораживаются до вашей проверки.\n"
        "• <b>🟢 Продавец:</b> Вы передаете товар только ПОСЛЕ того, как покупатель внесет депозит в гарант.",
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
        f"📝 <b>Шаг 2 из 4. Введите предмет/описание сделки:</b>\n"
        f"<i>(Например: Покупка Telegram-канала, софт, заказной дизайн)</i>"
    )

@router.message(CreateDealStates.title)
async def process_deal_title(message: Message, state: FSMContext):
    if len(message.text) < 3 or len(message.text) > 200:
        await message.answer("⚠️ Описание должно быть от 3 до 200 символов. Введите снова:")
        return

    await state.update_data(title=message.text.strip())
    await state.set_state(CreateDealStates.amount)
    data = await state.get_data()

    if data.get("creator_role") == "buyer":
        hint = "Введите сумму, которую вы будете вносить в гарант (в рублях):"
    else:
        hint = "Введите полную стоимость товара/услуги (в рублях).\nПокупатель внесет эту сумму, а вы получите 60% после закрытия сделки:"

    await message.answer(
        f"💰 <b>Шаг 3 из 4. Сумма сделки:</b>\n\n{hint}\n<i>(Минимум — 100 ₽)</i>",
        reply_markup=get_cancel_reply_kb()
    )

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
        await message.answer("⚠️ Введите корректное число (например: 1500):")
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
        f"📊 <b>Параметры сделки:</b>\n"
        f"• Сумма сделки: <b>{amount:,.2f} ₽</b>\n"
        f"• Комиссия гаранта (40%): <b>{fee:,.2f} ₽</b>\n"
        f"• К выплате продавцу: <b>{seller_receives:,.2f} ₽</b>\n\n"
        f"👥 <b>Шаг 4 из 4. Способ приглашения второго участника:</b>",
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
        other_name = "Продавца" if creator_role == "buyer" else "Покупателя"
        await call.message.edit_text(
            f"🆔 <b>Введите цифровой Telegram ID {other_name}:</b>\n\n"
            f"<i>(Партнер может узнать свой ID в боте @userinfobot)</i>"
        )
        return

    # Генерация инвайт-ссылки
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
    partner_title = "Продавцу" if creator_role == "buyer" else "Покупателю"

    await call.message.edit_text(
        f"✅ <b>Сделка №{deal_id} успешно создана!</b>\n\n"
        f"Передайте эту персональную ссылку {partner_title}:\n"
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
            await message.answer("⚠️ Нельзя указывать собственный ID. Введите ID второго участника:")
            return
    except ValueError:
        await message.answer("⚠️ ID должен состоять только из цифр. Введите снова:")
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

    # Оповещаем второго участника
    try:
        partner_role = "🟢 Продавцом" if creator_role == "buyer" else "🔵 Покупателем"
        await bot.send_message(
            partner_id,
            f"🔔 <b>Вам предложена новая сделка №{deal_id}!</b>\n\n"
            f"📌 Условия: <b>{data['title']}</b>\n"
            f"💰 Сумма: <b>{data['amount']:,.2f} ₽</b>\n"
            f"Вы назначены: <b>{partner_role}</b>.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔎 Открыть сделку", callback_data=f"view_deal:{deal_id}")]
            ])
        )
    except Exception:
        pass

    await message.answer(f"✅ <b>Сделка №{deal_id} успешно сформирована!</b>", reply_markup=get_main_keyboard())
    await render_deal_view(message, deal_id, creator_id)

# ══════════════════════════════════════════════════════════════════════════════
# КАРТОЧКА СДЕЛКИ: СТРОГОЕ РАЗГРАНИЧЕНИЕ ПРАВ
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
        "created": "⏳ Ожидание второго участника",
        "waiting_payment": "💳 Ожидание оплаты Покупателем",
        "paid": "🛡 Оплачено (в гаранте)",
        "shipped": "📦 Товар передан Продавцом (на проверке)",
        "dispute": "⚖️ Открыт спор (в арбитраже)",
        "completed": "✅ Успешно завершена",
        "cancelled": "🚫 Отменена"
    }

    if is_buyer:
        my_role_str = "🔵 Вы: <b>Покупатель</b>"
    elif is_seller:
        my_role_str = "🟢 Вы: <b>Продавец</b>"
    else:
        my_role_str = "👑 Вы: <b>Администратор</b>"

    text = (
        f"📋 <b>КАРТОЧКА СДЕЛКИ №{deal['id']}</b>\n"
        f"────────────────────────\n"
        f"📌 <b>Условия:</b> {deal['title']}\n"
        f"💰 <b>Сумма сделки:</b> {deal['amount']:,.2f} ₽\n"
        f"💼 <b>К выплате продавцу:</b> {deal['seller_receives']:,.2f} ₽ <i>(комиссия 40%: {deal['fee']:,.2f} ₽)</i>\n"
        f"🚦 <b>Статус:</b> {status_map.get(deal['status'], deal['status'])}\n"
        f"👤 {my_role_str}\n"
        f"────────────────────────\n"
    )

    kb_buttons = []

    # 1. Ожидание оплаты
    if deal["status"] in ["created", "waiting_payment"]:
        if is_buyer:
            payment_url = f"https://app.paritypay.ru/pay/{PARITYPAY_SHOP_ID}?order_id={deal['id']}&amount={deal['amount']}"
            kb_buttons.append([InlineKeyboardButton(text="💳 Оплатить через ParityPay", url=payment_url)])
            kb_buttons.append([InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_pay:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="❌ Отменить сделку", callback_data=f"cancel_deal:{deal['id']}")])
            text += "\n👉 <i>Оплатите сделку для заморозки средств на эскроу-счете.</i>"
        elif is_seller:
            kb_buttons.append([InlineKeyboardButton(text="❌ Отменить сделку", callback_data=f"cancel_deal:{deal['id']}")])
            text += "\n⏳ <i>Ожидайте оплаты от Покупателя. <b>НЕ ПЕРЕДАВАЙТЕ</b> товар, пока статус не изменится на «Оплачено»!</i>"

    # 2. Оплачено
    elif deal["status"] == "paid":
        if is_seller:
            kb_buttons.append([InlineKeyboardButton(text="📦 Товар/услуга передана", callback_data=f"ship_deal:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="💬 Анонимный чат", callback_data=f"open_chat:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="⚖️ Открыть спор", callback_data=f"open_dispute:{deal['id']}")])
            text += "\n💰 <i>Деньги в гаранте! Передайте товар Покупателю и нажмите кнопку «Товар передан».</i>"
        elif is_buyer:
            kb_buttons.append([InlineKeyboardButton(text="💬 Анонимный чат", callback_data=f"open_chat:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="⚖️ Открыть спор", callback_data=f"open_dispute:{deal['id']}")])
            text += "\n🛡 <i>Деньги зарезервированы. Ожидайте отправки товара от Продавца.</i>"

    # 3. Передано (проверка)
    elif deal["status"] == "shipped":
        if is_buyer:
            kb_buttons.append([InlineKeyboardButton(text="✅ Подтвердить получение", callback_data=f"confirm_deal:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="💬 Анонимный чат", callback_data=f"open_chat:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="⚖️ Открыть спор", callback_data=f"open_dispute:{deal['id']}")])
            text += "\n📦 <i>Продавец отправил товар. Проверьте его и подтвердите получение для выплаты средств Продавцу.</i>"
        elif is_seller:
            kb_buttons.append([InlineKeyboardButton(text="💬 Анонимный чат", callback_data=f"open_chat:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="⚖️ Открыть спор", callback_data=f"open_dispute:{deal['id']}")])
            text += "\n⏳ <i>Товар на проверке у Покупателя. Ожидайте подтверждения закрытия сделки.</i>"

    # 4. Спор
    elif deal["status"] == "dispute":
        kb_buttons.append([InlineKeyboardButton(text="💬 Чат участников", callback_data=f"open_chat:{deal['id']}")])
        text += f"\n⚖️ <i>Сделка в арбитраже. Администратор рассматривает детали. Поддержка: {SUPPORT_HANDLE}</i>"

    # 5. Завершена
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

# ══════════════════════════════════════════════════════════════════════════════
# ЭТАПЫ СДЕЛКИ: ОПЛАТА, ПЕРЕДАЧА, ПОДТВЕРЖДЕНИЕ
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("check_pay:"))
async def cb_check_pay(call: CallbackQuery):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal or deal["buyer_id"] != user_id:
        await call.answer("Только покупатель проверяет оплату.", show_alert=True)
        return

    # В тестовом режиме переводим в статус оплачено
    await db_execute("UPDATE deals SET status = 'paid' WHERE id = ?", (deal_id,))
    await call.answer("✅ Оплата зафиксирована! Средства на гаранте.", show_alert=True)

    if deal["seller_id"]:
        try:
            await bot.send_message(
                deal["seller_id"],
                f"💰 <b>Покупатель оплатил сделку №{deal_id}!</b>\n"
                f"Средства зачислены в гарант. Передайте товар Покупателю.",
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
        await call.answer("Только Продавец подтверждает передачу товара.", show_alert=True)
        return

    await db_execute("UPDATE deals SET status = 'shipped' WHERE id = ?", (deal_id,))
    await call.answer("Уведомление отправлено Покупателю.")

    if deal["buyer_id"]:
        try:
            await bot.send_message(
                deal["buyer_id"],
                f"📦 <b>Продавец передал товар по сделке №{deal_id}!</b>\n"
                f"Проверьте заказ и нажмите «Подтвердить получение».",
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
        await call.answer("Только Покупатель может закрыть сделку.", show_alert=True)
        return

    if deal["status"] not in ["paid", "shipped"]:
        await call.answer("Невозможно закрыть сделку в текущем статусе.", show_alert=True)
        return

    # Зачисление средств продавцу и комиссия
    await db_transaction([
        ("UPDATE users SET balance = balance + ? WHERE user_id = ?", (deal["seller_receives"], deal["seller_id"])),
        ("INSERT INTO platform_vault (deal_id, fee_amount, created_at) VALUES (?, ?, ?)", (deal_id, deal["fee"], datetime.now().isoformat())),
        ("UPDATE deals SET status = 'completed' WHERE id = ?", (deal_id,))
    ])

    await call.answer("✅ Сделка успешно закрыта! Продавец получил средства.", show_alert=True)

    if deal["seller_id"]:
        try:
            await bot.send_message(
                deal["seller_id"],
                f"🎉 <b>Сделка №{deal_id} успешно завершена!</b>\n\n"
                f"💰 На баланс начислено: <b>{deal['seller_receives']:,.2f} ₽</b> (за вычетом комиссии 40%).\n"
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
    await call.answer("⚖️ Сделка передана арбитру.", show_alert=True)

    try:
        await bot.send_message(
            ADMIN_ID,
            f"🚨 <b>ОТКРЫТ СПОР ПО СДЕЛКЕ №{deal_id}!</b>\n"
            f"Инициатор: <code>{user_id}</code>\n"
            f"Сумма: {deal['amount']:,.2f} ₽",
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
        await call.answer("Вы не участник сделки.", show_alert=True)
        return

    await state.set_state(ChatProxyStates.in_chat)
    await state.update_data(deal_id=deal_id)

    role_label = "🔵 Покупатель" if user_id == deal["buyer_id"] else "🟢 Продавец"
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🚪 Покинуть диалог")]], resize_keyboard=True)

    await call.message.answer(
        f"💬 <b>Анонимный чат по сделке №{deal_id} активирован!</b>\n"
        f"Ваша роль: <b>{role_label}</b>\n\n"
        f"Все сообщения, голосовые и файлы пересылаются партнёру анонимно.\n"
        f"Для выхода нажмите «🚪 Покинуть диалог».",
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
        await message.answer("⚠️ Ошибка отправки сообщения партнеру.")

# ══════════════════════════════════════════════════════════════════════════════
# ОТЗЫВЫ И РЕЙТИНГ
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("leave_review:"))
async def cb_leave_review(call: CallbackQuery, state: FSMContext):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    deal = await db_get_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not deal or deal["buyer_id"] != user_id:
        await call.answer("Только Покупатель оставляет отзыв Продавцу.", show_alert=True)
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
    await call.message.edit_text("⭐ <b>Оцените качество работы Продавца:</b>", reply_markup=kb)

@router.callback_query(ReviewStates.rating, F.data.startswith("rate:"))
async def cb_rate_score(call: CallbackQuery, state: FSMContext):
    rating = int(call.data.split(":")[1])
    await state.update_data(rating=rating)
    await state.set_state(ReviewStates.comment)
    await call.message.edit_text(f"Оценка: <b>{rating}/5 ⭐</b>\n\nНапишите краткий отзыв (или отправьте «-»):")

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
    await message.answer("🎉 Спасибо! Ваш отзыв добавлен в профиль Продавца.", reply_markup=get_main_keyboard())

# ══════════════════════════════════════════════════════════════════════════════
# ПРОФИЛЬ, ВЫВОД И СДЕЛКИ
# ══════════════════════════════════════════════════════════════════════════════
@router.message(F.text == "👤 Профиль")
async def show_profile(message: Message):
    user_id = message.from_user.id
    user = await db_get_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if not user:
        return

    avg = round(user["rating_sum"] / user["rating_count"], 2) if user["rating_count"] > 0 else 0.0
    completed = await db_get_one(
        "SELECT COUNT(*) as cnt FROM deals WHERE (buyer_id = ? OR seller_id = ?) AND status = 'completed'",
        (user_id, user_id)
    )

    text = (
        f"👤 <b>ВАШ ПРОФИЛЬ</b>\n"
        f"────────────────────────\n"
        f"🆔 <b>Telegram ID:</b> <code>{user_id}</code>\n"
        f"💰 <b>Баланс к выводу:</b> {user['balance']:,.2f} ₽\n"
        f"⭐ <b>Рейтинг:</b> {avg} / 5.0 (Отзывов: {user['rating_count']})\n"
        f"🤝 <b>Успешных сделок:</b> {completed['cnt']}\n"
        f"────────────────────────"
    )
    await message.answer(text, reply_markup=get_main_keyboard())

@router.message(F.text == "📂 Мои сделки")
async def show_my_deals(message: Message):
    user_id = message.from_user.id
    deals = await db_get_all(
        "SELECT * FROM deals WHERE (buyer_id = ? OR seller_id = ?) ORDER BY id DESC LIMIT 10",
        (user_id, user_id)
    )

    if not deals:
        await message.answer("У вас пока нет сделок.", reply_markup=get_main_keyboard())
        return

    kb = []
    for d in deals:
        ico = "🔵" if d["buyer_id"] == user_id else "🟢"
        kb.append([InlineKeyboardButton(text=f"{ico} №{d['id']} — {d['title'][:20]} ({d['amount']:,.0f} ₽)", callback_data=f"view_deal:{d['id']}")])

    await message.answer("📂 <b>Последние 10 сделок:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.message(F.text == "💳 Вывод средств")
async def start_withdraw(message: Message, state: FSMContext):
    user = await db_get_one("SELECT * FROM users WHERE user_id = ?", (message.from_user.id,))
    if not user:
        return

    if user["balance"] < MIN_WITHDRAW:
        await message.answer(
            f"💳 <b>Вывод средств</b>\n\n"
            f"Ваш баланс: <b>{user['balance']:,.2f} ₽</b>\n"
            f"Минимальный вывод: <b>{MIN_WITHDRAW:,.2f} ₽</b>\n\n"
            f"📅 <i>Выплаты производятся каждый <b>вторник</b> и <b>четверг</b>.</i>"
        )
        return

    await state.set_state(WithdrawStates.amount)
    await message.answer(
        f"💳 Введите сумму к выводу (доступно: {user['balance']:,.2f} ₽, мин: {MIN_WITHDRAW:,.0f} ₽):",
        reply_markup=get_cancel_reply_kb()
    )

@router.message(WithdrawStates.amount)
async def process_withdraw_amount(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Вывод отменен.", reply_markup=get_main_keyboard())
        return

    user = await db_get_one("SELECT * FROM users WHERE user_id = ?", (message.from_user.id,))
    try:
        val = float(message.text.replace(",", ".").strip())
        if val < MIN_WITHDRAW or val > user["balance"]:
            await message.answer(f"⚠️ Неверная сумма. Введите от {MIN_WITHDRAW:,.0f} до {user['balance']:,.2f} ₽:")
            return
    except ValueError:
        await message.answer("⚠️ Введите число:")
        return

    await state.update_data(amount=val)
    await state.set_state(WithdrawStates.requisites)
    await message.answer("💳 Введите реквизиты (Карта РФ или СБП с банком):")

@router.message(WithdrawStates.requisites)
async def process_withdraw_reqs(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Вывод отменен.", reply_markup=get_main_keyboard())
        return

    data = await state.get_data()
    user_id = message.from_user.id
    amount = data["amount"]
    reqs = message.text.strip()

    await db_transaction([
        ("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id)),
        ("INSERT INTO withdrawals (user_id, amount, requisites, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
         (user_id, amount, reqs, datetime.now().isoformat()))
    ])

    await state.clear()
    await message.answer(
        f"✅ <b>Заявка на вывод принята!</b>\n\n"
        f"Сумма: <b>{amount:,.2f} ₽</b>\n"
        f"Реквизиты: <code>{reqs}</code>\n"
        f"📅 Выплата будет произведена в ближайший расчетный день (<b>вторник</b> или <b>четверг</b>).",
        reply_markup=get_main_keyboard()
    )

# ══════════════════════════════════════════════════════════════════════════════
# ПОДДЕРЖКА И ВНЕШНИЕ ССЫЛКИ
# ══════════════════════════════════════════════════════════════════════════════
@router.message(F.text == "ℹ️ Помощь / Поддержка")
async def show_help(message: Message):
    await message.answer(
        f"🛡 <b>Гарант-сервис «BastionPRO»</b>\n\n"
        f"• Комиссия платформы: <b>40%</b> (с суммы сделки Продавца)\n"
        f"• Минимальная сделка: <b>100 ₽</b>\n"
        f"• Выплаты: <b>каждый вторник и четверг</b> (от 500 ₽)\n\n"
        f"👨‍💻 <b>Официальный арбитр/поддержка:</b> {SUPPORT_HANDLE}",
        reply_markup=get_main_keyboard()
    )

@router.message(F.text == "🌐 Подключить OnyxVPN")
async def open_onyx_vpn(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Перейти в OnyxVPN", url="https://t.me/vpnonyxxbot")]
    ])
    await message.answer(
        "🌐 <b>OnyxVPN — быстрый и безопасный VPN</b>\n\n"
        "• Надежное шифрование соединения\n"
        "• Высокая скорость и отсутствие ограничений по трафику",
        reply_markup=kb
    )

# ══════════════════════════════════════════════════════════════════════════════
# АДМИНИСТРАТИВНАЯ ПАНЕЛЬ
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    users_cnt = (await db_get_one("SELECT COUNT(*) as c FROM users"))["c"]
    deals_cnt = (await db_get_one("SELECT COUNT(*) as c FROM deals WHERE status = 'completed'"))["c"]
    vault = (await db_get_one("SELECT SUM(fee_amount) as s FROM platform_vault"))["s"] or 0.0

    text = (
        f"👑 <b>ПАНЕЛЬ АДМИНИСТРАТОРА</b>\n"
        f"────────────────────────\n"
        f"👥 Пользователей: <b>{users_cnt}</b>\n"
        f"✅ Закрытых сделок: <b>{deals_cnt}</b>\n"
        f"💰 Собрано комиссий: <b>{vault:,.2f} ₽</b>\n"
        f"────────────────────────"
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
        await call.answer("Возврат оформлен.", show_alert=True)
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
        await call.answer("Выплата продавцу произведена.", show_alert=True)
        await render_deal_view(call.message, deal_id, call.from_user.id, is_edit=True)

@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminBroadcast.text)
    await call.message.answer("Введите текст рассылки:", reply_markup=get_cancel_reply_kb())
    await call.answer()

@router.message(AdminBroadcast.text)
async def process_admin_broadcast(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено.", reply_markup=get_main_keyboard())
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
    await message.answer(f"✅ Рассылка завершена: {sent} сообщений.", reply_markup=get_main_keyboard())

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
    logger.info("BastionPRO запущен без внешних зависимостей БД!")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
