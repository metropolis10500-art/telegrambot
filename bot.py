"""
════════════════════════════════════════════════════════════════════════════════
    BASTION PRO — АВТОГАРАНТ (Aiogram 3.x + aiosqlite)
    Полная однофайловая сборка с выверенной ролевой моделью
════════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

import aiosqlite
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
# КОНФИГУРАЦИЯ СЕРВИСА
# ══════════════════════════════════════════════════════════════════════════════
BOT_TOKEN = "8882817041:AAHh1NEZEjKBDQg9l7aOZ4rrWRkvYUc7snI"  # Ваш токен бота
ADMIN_ID = 5494544187                                            # Telegram ID администратора
DB_FILE = "bastion_pro_v2.db"                                     # Чистая база данных
SUPPORT_HANDLE = "@vladofix97"
COMMISSION_RATE = 0.40                                           # 40% комиссия платформы
MIN_WITHDRAW = 500.0                                             # Минимальный вывод

# Настройка платежного шлюза ParityPay
PARITYPAY_SHOP_ID = "YOUR_SHOP_ID"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# ══════════════════════════════════════════════════════════════════════════════
# БАЗА ДАННЫХ
# ══════════════════════════════════════════════════════════════════════════════
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance REAL DEFAULT 0.0,
                rating_sum REAL DEFAULT 0.0,
                rating_count INTEGER DEFAULT 0,
                created_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS deals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                buyer_id INTEGER,
                seller_id INTEGER,
                title TEXT,
                amount REAL,
                fee REAL,
                seller_receives REAL,
                status TEXT DEFAULT 'created', -- created, waiting_payment, paid, shipped, dispute, completed, cancelled
                created_at TEXT,
                FOREIGN KEY(buyer_id) REFERENCES users(user_id),
                FOREIGN KEY(seller_id) REFERENCES users(user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                requisites TEXT,
                status TEXT DEFAULT 'pending', -- pending, approved, rejected
                created_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id INTEGER,
                target_user_id INTEGER,
                author_user_id INTEGER,
                rating INTEGER,
                comment TEXT,
                created_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS platform_vault (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id INTEGER,
                fee_amount REAL,
                created_at TEXT
            )
        """)
        await db.commit()
    logger.info("База данных инициализирована успешно.")

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()

async def register_user_if_not_exists(user_id: int, username: Optional[str]):
    clean_username = f"@{username}" if username else "Пользователь"
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            INSERT OR IGNORE INTO users (user_id, username, balance, rating_sum, rating_count, created_at)
            VALUES (?, ?, 0.0, 0.0, 0, ?)
        """, (user_id, clean_username, datetime.now().isoformat()))
        if username:
            await db.execute("UPDATE users SET username = ? WHERE user_id = ?", (clean_username, user_id))
        await db.commit()

async def safe_edit_text(message: Message, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None):
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning(f"Ошибка safe_edit_text: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# FSM СОСТОЯНИЯ
# ══════════════════════════════════════════════════════════════════════════════
class CreateDealStates(StatesGroup):
    role = State()            # Выбор роли: buyer или seller
    title = State()           # Описание сделки
    amount = State()          # Сумма
    partner_mode = State()    # Выбор: ID или Ссылка
    partner_id = State()      # Ввод ID партнера

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
# СТАРТ И ДИПЛИНКИ
# ══════════════════════════════════════════════════════════════════════════════
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username
    await register_user_if_not_exists(user_id, username)

    # Проверка диплинка присоединения к сделке: /start deal_123
    args = command.args
    if args and args.startswith("deal_"):
        try:
            deal_id = int(args.replace("deal_", ""))
            await process_deal_join(message, deal_id, user_id)
            return
        except Exception as e:
            logger.error(f"Ошибка парсинга диплинка: {e}")

    await message.answer(
        "🛡 <b>Добро пожаловать в Гарант-сервис «BastionPRO»!</b>\n\n"
        "Мы обеспечиваем 100% безопасность ваших расчётов при покупке "
        "и продаже цифровых товаров, услуг и каналов.\n\n"
        "• Деньги покупателя замораживаются до полной проверки товара.\n"
        "• Продавец гарантированно получает оплату при честном выполнении условий.\n"
        "• Анонимный чат внутри сделки защищает ваши личные данные.",
        reply_markup=get_main_keyboard()
    )

async def process_deal_join(message: Message, deal_id: int, user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)) as cursor:
            deal = await cursor.fetchone()

    if not deal:
        await message.answer("❌ Сделка не найдена или была удалена.", reply_markup=get_main_keyboard())
        return

    # Если пользователь уже является участником сделки
    if deal["buyer_id"] == user_id or deal["seller_id"] == user_id:
        await render_deal_view(message, deal_id, user_id)
        return

    # Если в сделке есть вакантное место — определяем строго противоположную роль
    if deal["buyer_id"] is None and deal["seller_id"] != user_id:
        # Вакантна роль Покупателя (сделку создал Продавец)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔵 Принять роль Покупателя", callback_data=f"join_role:{deal_id}:buyer")],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data="close_view")]
        ])
        await message.answer(
            f"🤝 <b>Вас пригласили в сделку №{deal['id']}!</b>\n\n"
            f"📌 <b>Условия:</b> {deal['title']}\n"
            f"💰 <b>Сумма к оплате:</b> {deal['amount']:,.2f} ₽\n\n"
            f"Вы вступаете как <b>🔵 Покупатель</b>. Вы вносите оплату в гарант и проверяете товар перед подтверждением.",
            reply_markup=kb
        )
        return

    elif deal["seller_id"] is None and deal["buyer_id"] != user_id:
        # Вакантна роль Продавца (сделку создал Покупатель)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Принять роль Продавца", callback_data=f"join_role:{deal_id}:seller")],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data="close_view")]
        ])
        await message.answer(
            f"🤝 <b>Вас пригласили в сделку №{deal['id']}!</b>\n\n"
            f"📌 <b>Условия:</b> {deal['title']}\n"
            f"💰 <b>Сумма сделки:</b> {deal['amount']:,.2f} ₽\n"
            f"💼 <b>Ваша выплата:</b> {deal['seller_receives']:,.2f} ₽ (за вычетом 40% комиссии)\n\n"
            f"Вы вступаете как <b>🟢 Продавец</b>. Оплату вносит Покупатель.",
            reply_markup=kb
        )
        return

    else:
        await message.answer("⚠️ В этой сделке уже заняты обе роли.", reply_markup=get_main_keyboard())

@router.callback_query(F.data.startswith("join_role:"))
async def cb_join_role(call: CallbackQuery):
    _, deal_id_str, role = call.data.split(":")
    deal_id = int(deal_id_str)
    user_id = call.from_user.id
    username = call.from_user.username
    await register_user_if_not_exists(user_id, username)

    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)) as cursor:
            deal = await cursor.fetchone()

        if not deal:
            await call.answer("Сделка не найдена.", show_alert=True)
            return

        creator_id = deal["buyer_id"] if deal["buyer_id"] else deal["seller_id"]

        if role == "buyer" and deal["buyer_id"] is None:
            await db.execute("UPDATE deals SET buyer_id = ?, status = 'waiting_payment' WHERE id = ?", (user_id, deal_id))
            await db.commit()
            await call.answer("Вы успешно вступили в сделку как Покупатель!")
            try:
                await bot.send_message(
                    creator_id,
                    f"🔔 <b>Второй участник подключился к сделке №{deal_id}!</b>\n"
                    f"Покупатель готов внести средства в гарант."
                )
            except Exception:
                pass
            await render_deal_view(call.message, deal_id, user_id, is_edit=True)

        elif role == "seller" and deal["seller_id"] is None:
            await db.execute("UPDATE deals SET seller_id = ? WHERE id = ?", (user_id, deal_id))
            await db.commit()
            await call.answer("Вы успешно вступили в сделку как Продавец!")
            try:
                await bot.send_message(
                    creator_id,
                    f"🔔 <b>Продавец подключился к сделке №{deal_id}!</b>\n"
                    f"Теперь вы можете внести средства в гарант."
                )
            except Exception:
                pass
            await render_deal_view(call.message, deal_id, user_id, is_edit=True)

        else:
            await call.answer("Данная роль уже занята другим пользователем.", show_alert=True)

# ══════════════════════════════════════════════════════════════════════════════
# СОЗДАНИЕ СДЕЛКИ (ЧЁТКИЙ ВЫБОР РОЛИ)
# ══════════════════════════════════════════════════════════════════════════════
@router.message(F.text == "➕ Создать сделку")
async def start_create_deal(message: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔵 Я Покупатель (буду вносить оплату)", callback_data="create_as:buyer")],
        [InlineKeyboardButton(text="🟢 Я Продавец (буду передавать товар)", callback_data="create_as:seller")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")]
    ])
    await message.answer(
        "🎯 <b>Шаг 1 из 4. Выберите вашу роль в сделке:</b>\n\n"
        "• <b>🔵 Я Покупатель:</b> Вы пополняете баланс сделки. Деньги хранятся у гаранта, пока вы не проверите товар.\n"
        "• <b>🟢 Я Продавец:</b> Вы передаёте товар только после подтверждения пополнения гаранта Покупателем. Вы получаете сумму за вычетом комиссии сервиса (40%).",
        reply_markup=kb
    )

@router.callback_query(F.data.startswith("create_as:"))
async def cb_select_creator_role(call: CallbackQuery, state: FSMContext):
    role = call.data.split(":")[1]
    await state.update_data(creator_role=role)
    await state.set_state(CreateDealStates.title)
    
    role_text = "🔵 Покупатель" if role == "buyer" else "🟢 Продавец"
    await call.message.edit_text(
        f"Вы выбрали роль: <b>{role_text}</b>\n\n"
        f"📝 <b>Шаг 2 из 4. Введите краткое описание сделки:</b>\n"
        f"<i>(Например: Покупка Telegram-канала, разработка скрипта, покупка ключа и т.д.)</i>"
    )

@router.message(CreateDealStates.title)
async def process_deal_title(message: Message, state: FSMContext):
    if len(message.text) < 3 or len(message.text) > 200:
        await message.answer("⚠️ Описание должно быть от 3 до 200 символов. Попробуйте еще раз:")
        return
    await state.update_data(title=message.text.strip())
    await state.set_state(CreateDealStates.amount)
    
    data = await state.get_data()
    role = data.get("creator_role")
    
    if role == "buyer":
        hint = "Введите сумму, которую вы готовы оплатить за сделку (в рублях):"
    else:
        hint = "Введите полную стоимость товара/услуги (в рублях).\nПокупатель внесёт эту сумму, а вы получите 60% после закрытия сделки:"

    await message.answer(
        f"💰 <b>Шаг 3 из 4. Сумма сделки:</b>\n\n{hint}\n<i>(Минимальная сумма — 100 ₽)</i>",
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
            await message.answer("⚠️ Сумма сделки должна быть от 100 до 500 000 ₽. Введите корректное число:")
            return
    except ValueError:
        await message.answer("⚠️ Введите корректное число (например: 1500 или 250.50):")
        return

    fee = round(amount * COMMISSION_RATE, 2)
    seller_receives = round(amount - fee, 2)

    await state.update_data(amount=amount, fee=fee, seller_receives=seller_receives)
    await state.set_state(CreateDealStates.partner_mode)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Создать ссылку-приглашение", callback_data="partner_mode:link")],
        [InlineKeyboardButton(text="🆔 Указать Telegram ID партнера", callback_data="partner_mode:id")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_fsm")]
    ])

    await message.answer(
        f"📊 <b>Расчёт сделки:</b>\n"
        f"• Полная сумма сделки: <b>{amount:,.2f} ₽</b>\n"
        f"• Комиссия гаранта (40%): <b>{fee:,.2f} ₽</b>\n"
        f"• Продавец получит на баланс: <b>{seller_receives:,.2f} ₽</b>\n\n"
        f"👥 <b>Шаг 4 из 4. Как вы хотите пригласить второго участника?</b>",
        reply_markup=kb
    )

@router.callback_query(F.data.startswith("partner_mode:"))
async def cb_select_partner_mode(call: CallbackQuery, state: FSMContext):
    mode = call.data.split(":")[1]
    data = await state.get_data()
    user_id = call.from_user.id
    creator_role = data["creator_role"]
    title = data["title"]
    amount = data["amount"]
    fee = data["fee"]
    seller_receives = data["seller_receives"]

    if mode == "id":
        await state.set_state(CreateDealStates.partner_id)
        other_role_str = "Продавца" if creator_role == "buyer" else "Покупателя"
        await call.message.edit_text(
            f"🆔 <b>Введите цифровой Telegram ID {other_role_str}:</b>\n\n"
            f"<i>(Партнёр может узнать свой ID в боте @userinfobot)</i>"
        )
        return

    # Режим генерации ссылки
    buyer_id = user_id if creator_role == "buyer" else None
    seller_id = user_id if creator_role == "seller" else None

    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("""
            INSERT INTO deals (buyer_id, seller_id, title, amount, fee, seller_receives, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'created', ?)
        """, (buyer_id, seller_id, title, amount, fee, seller_receives, datetime.now().isoformat()))
        deal_id = cursor.lastrowid
        await db.commit()

    await state.clear()
    bot_info = await bot.get_me()
    invite_url = f"https://t.me/{bot_info.username}?start=deal_{deal_id}"
    other_role_name = "Продавцу" if creator_role == "buyer" else "Покупателю"

    await call.message.edit_text(
        f"✅ <b>Сделка №{deal_id} успешно сформирована!</b>\n\n"
        f"Отправьте эту ссылку второму участнику ({other_role_name}):\n"
        f"👉 <code>{invite_url}</code>\n\n"
        f"Как только партнёр перейдёт по ссылке, он автоматически займет роль "
        f"<b>{'🟢 Продавца' if creator_role == 'buyer' else '🔵 Покупателя'}</b>.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔎 Открыть карточку сделки", callback_data=f"view_deal:{deal_id}")]
        ])
    )

@router.message(CreateDealStates.partner_id)
async def process_partner_id_input(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Создание сделки отменено.", reply_markup=get_main_keyboard())
        return

    try:
        partner_id = int(message.text.strip())
        if partner_id == message.from_user.id:
            await message.answer("⚠️ Вы не можете указать собственный ID. Введите ID второго участника:")
            return
    except ValueError:
        await message.answer("⚠️ ID должен состоять только из цифр (например: 123456789):")
        return

    data = await state.get_data()
    creator_id = message.from_user.id
    creator_role = data["creator_role"]

    if creator_role == "buyer":
        buyer_id = creator_id
        seller_id = partner_id
    else:
        buyer_id = partner_id
        seller_id = creator_id

    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("""
            INSERT INTO deals (buyer_id, seller_id, title, amount, fee, seller_receives, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'waiting_payment', ?)
        """, (buyer_id, seller_id, data["title"], data["amount"], data["fee"], data["seller_receives"], datetime.now().isoformat()))
        deal_id = cursor.lastrowid
        await db.commit()

    await state.clear()

    # Оповещение партнёра
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

    await message.answer(
        f"✅ <b>Сделка №{deal_id} успешно создана!</b>\nПартнеру отправлено уведомление.",
        reply_markup=get_main_keyboard()
    )
    await render_deal_view(message, deal_id, creator_id)

# ══════════════════════════════════════════════════════════════════════════════
# КАРТОЧКА СДЕЛКИ: ИДЕАЛЬНОЕ РАЗГРАНИЧЕНИЕ ПРАВ И КНОПОК
# ══════════════════════════════════════════════════════════════════════════════
async def render_deal_view(target_message: Message, deal_id: int, user_id: int, is_edit: bool = False):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)) as cursor:
            deal = await cursor.fetchone()

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
        # Пользователь не относится к сделке (может только вступить по ссылке)
        await target_message.answer("⚠️ Вы не являетесь участником этой сделки.")
        return

    # Текстовое описание статуса
    status_map = {
        "created": "⏳ Ожидание подключения второго участника",
        "waiting_payment": "💳 Ожидание внесения оплаты Покупателем",
        "paid": "🛡 Оплачено (средства заморожены в гаранте)",
        "shipped": "📦 Товар передан Продавцом (на проверке у Покупателя)",
        "dispute": "⚖️ Открыт спор (дело передано в арбитраж)",
        "completed": "✅ Сделка успешно завершена",
        "cancelled": "🚫 Сделка отменена"
    }
    status_str = status_map.get(deal["status"], deal["status"])

    # Определение роли для пользователя
    if is_buyer:
        my_role_str = "🔵 Вы: <b>Покупатель</b>"
    elif is_seller:
        my_role_str = "🟢 Вы: <b>Продавец</b>"
    else:
        my_role_str = "👑 Вы: <b>Администратор</b>"

    text = (
        f"📋 <b>КАРТОЧКА СДЕЛКИ №{deal['id']}</b>\n"
        f"────────────────────────\n"
        f"📌 <b>Предмет сделки:</b> {deal['title']}\n"
        f"💰 <b>Сумма сделки:</b> {deal['amount']:,.2f} ₽\n"
        f"💼 <b>К выплате продавцу:</b> {deal['seller_receives']:,.2f} ₽ <i>(комиссия сервиса 40%: {deal['fee']:,.2f} ₽)</i>\n"
        f"🚦 <b>Текущий статус:</b> {status_str}\n"
        f"👤 {my_role_str}\n"
        f"────────────────────────\n"
    )

    kb_buttons = []

    # 1. СТАТУС: waiting_payment или created (Оплата)
    if deal["status"] in ["created", "waiting_payment"]:
        if is_buyer:
            # Оплачивает ТОЛЬКО Покупатель!
            payment_url = f"https://app.paritypay.ru/pay/{PARITYPAY_SHOP_ID}?order_id={deal['id']}&amount={deal['amount']}"
            kb_buttons.append([InlineKeyboardButton(text="💳 Оплатить через ParityPay", url=payment_url)])
            kb_buttons.append([InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_pay:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="❌ Отменить сделку", callback_data=f"cancel_deal:{deal['id']}")])
            text += "\n👉 <i>Внесите средства в гарант для старта выполнения обязательств.</i>"
        elif is_seller:
            kb_buttons.append([InlineKeyboardButton(text="❌ Отменить сделку", callback_data=f"cancel_deal:{deal['id']}")])
            text += "\n⏳ <i>Ожидайте, пока Покупатель внесёт средства. <b>НЕ ПЕРЕДАВАЙТЕ</b> товар до смены статуса на «Оплачено»!</i>"

    # 2. СТАТУС: paid (Средства на гаранте)
    elif deal["status"] == "paid":
        if is_seller:
            # Продавец передаёт товар
            kb_buttons.append([InlineKeyboardButton(text="📦 Товар/услуга передана", callback_data=f"ship_deal:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="💬 Анонимный чат", callback_data=f"open_chat:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="⚖️ Открыть спор", callback_data=f"open_dispute:{deal['id']}")])
            text += "\n💰 <i>Деньги внесены в гарант! Передайте товар Покупателю через анонимный чат и нажмите «Товар/услуга передана».</i>"
        elif is_buyer:
            kb_buttons.append([InlineKeyboardButton(text="💬 Анонимный чат", callback_data=f"open_chat:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="⚖️ Открыть спор", callback_data=f"open_dispute:{deal['id']}")])
            text += "\n🛡 <i>Ваши деньги в безопасности в гарант-сервисе. Ожидайте передачи товара от Продавца.</i>"

    # 3. СТАТУС: shipped (Товар передан, проверка покупателем)
    elif deal["status"] == "shipped":
        if is_buyer:
            # Покупатель подтверждает получение
            kb_buttons.append([InlineKeyboardButton(text="✅ Подтвердить получение и закрыть", callback_data=f"confirm_deal:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="💬 Анонимный чат", callback_data=f"open_chat:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="⚖️ Открыть спор", callback_data=f"open_dispute:{deal['id']}")])
            text += "\n📦 <i>Продавец сообщил о передаче товара. Тщательно проверьте заказ и нажмите «Подтвердить получение».</i>"
        elif is_seller:
            kb_buttons.append([InlineKeyboardButton(text="💬 Анонимный чат", callback_data=f"open_chat:{deal['id']}")])
            kb_buttons.append([InlineKeyboardButton(text="⚖️ Открыть спор", callback_data=f"open_dispute:{deal['id']}")])
            text += "\n⏳ <i>Вы уведомили Покупателя о передаче товара. Ожидайте подтверждения и зачисления средств на ваш баланс.</i>"

    # 4. СТАТУС: dispute (Спор)
    elif deal["status"] == "dispute":
        kb_buttons.append([InlineKeyboardButton(text="💬 Чат участников", callback_data=f"open_chat:{deal['id']}")])
        text += f"\n⚖️ <i>Сделка заморожена. Администратор арбитража изучает материалы. Напишите в поддержку: {SUPPORT_HANDLE}</i>"

    # 5. СТАТУС: completed (Завершена)
    elif deal["status"] == "completed":
        if is_buyer:
            kb_buttons.append([InlineKeyboardButton(text="⭐ Оценить работу Продавца", callback_data=f"leave_review:{deal['id']}")])
        text += "\n🎉 <i>Сделка успешно завершена. Расчёты произведены в полном объёме.</i>"

    # Общие кнопки
    row_nav = [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"view_deal:{deal['id']}")]
    kb_buttons.append(row_nav)

    # Админские действия
    if is_admin and deal["status"] == "dispute":
        kb_buttons.append([
            InlineKeyboardButton(text="👑 Вернуть покупателю 100%", callback_data=f"admin_refund:{deal['id']}"),
            InlineKeyboardButton(text="👑 Выплатить продавцу", callback_data=f"admin_payout:{deal['id']}")
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
# ЖИЗНЕННЫЙ ЦИКЛ СДЕЛКИ: ОПЛАТА, ПЕРЕДАЧА, ПОДТВЕРЖДЕНИЕ
# ══════════════════════════════════════════════════════════════════════════════

# 1. Проверка оплаты покупателем
@router.callback_query(F.data.startswith("check_pay:"))
async def cb_check_pay(call: CallbackQuery):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)) as cursor:
            deal = await cursor.fetchone()

    if not deal or deal["buyer_id"] != user_id:
        await call.answer("Доступ запрещен.", show_alert=True)
        return

    # В тестовом режиме эмулируем успешную оплату через шлюз
    # При реальном вебхуке статус обновляет ваш сервер: status = 'paid'
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE deals SET status = 'paid' WHERE id = ?", (deal_id,))
        await db.commit()

    await call.answer("✅ Оплата успешно подтверждена! Средства зарезервированы в гаранте.", show_alert=True)

    # Уведомляем продавца
    if deal["seller_id"]:
        try:
            await bot.send_message(
                deal["seller_id"],
                f"💰 <b>Покупатель оплатил сделку №{deal_id}!</b>\n\n"
                f"Средства находятся под защитой Гаранта. Теперь вы можете передать товар Покупателю.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔎 Открыть сделку", callback_data=f"view_deal:{deal_id}")]
                ])
            )
        except Exception:
            pass

    await render_deal_view(call.message, deal_id, user_id, is_edit=True)

# 2. Продавец отмечает товар как переданный
@router.callback_query(F.data.startswith("ship_deal:"))
async def cb_ship_deal(call: CallbackQuery):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)) as cursor:
            deal = await cursor.fetchone()

    if not deal or deal["seller_id"] != user_id:
        await call.answer("Только Продавец может подтвердить отправку товара.", show_alert=True)
        return

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE deals SET status = 'shipped' WHERE id = ?", (deal_id,))
        await db.commit()

    await call.answer("Вы уведомили покупателя о передаче товара.")

    if deal["buyer_id"]:
        try:
            await bot.send_message(
                deal["buyer_id"],
                f"📦 <b>Продавец передал заказ по сделке №{deal_id}!</b>\n\n"
                f"Пожалуйста, проверьте выполнение всех условий. Если всё в порядке — нажмите «Подтвердить получение».",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔎 Проверить сделку", callback_data=f"view_deal:{deal_id}")]
                ])
            )
        except Exception:
            pass

    await render_deal_view(call.message, deal_id, user_id, is_edit=True)

# 3. Покупатель подтверждает получение (Закрытие и зачисление средств)
@router.callback_query(F.data.startswith("confirm_deal:"))
async def cb_confirm_deal(call: CallbackQuery):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)) as cursor:
            deal = await cursor.fetchone()

        if not deal or deal["buyer_id"] != user_id:
            await call.answer("Только Покупатель может подтвердить успешное получение.", show_alert=True)
            return

        if deal["status"] not in ["paid", "shipped"]:
            await call.answer("Невозможно завершить сделку в текущем статусе.", show_alert=True)
            return

        seller_payout = deal["seller_receives"]
        platform_fee = deal["fee"]

        # Начисление средств продавцу и фиксация комиссии платформы
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (seller_payout, deal["seller_id"]))
        await db.execute("INSERT INTO platform_vault (deal_id, fee_amount, created_at) VALUES (?, ?, ?)",
                         (deal_id, platform_fee, datetime.now().isoformat()))
        await db.execute("UPDATE deals SET status = 'completed' WHERE id = ?", (deal_id,))
        await db.commit()

    await call.answer("✅ Сделка успешно закрыта! Продавцу выплачены средства.", show_alert=True)

    # Уведомление продавца о зачислении средств
    if deal["seller_id"]:
        try:
            await bot.send_message(
                deal["seller_id"],
                f"🎉 <b>Сделка №{deal_id} успешно закрыта!</b>\n\n"
                f"💰 На ваш баланс зачислено: <b>{seller_payout:,.2f} ₽</b> (за вычетом 40% комиссии).\n"
                f"Вы можете запросить вывод средств в главном меню.",
                reply_markup=get_main_keyboard()
            )
        except Exception:
            pass

    await render_deal_view(call.message, deal_id, user_id, is_edit=True)

# 4. Отмена сделки (до момента внесения средств)
@router.callback_query(F.data.startswith("cancel_deal:"))
async def cb_cancel_deal(call: CallbackQuery):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)) as cursor:
            deal = await cursor.fetchone()

        if not deal:
            await call.answer("Сделка не найдена.", show_alert=True)
            return

        if deal["buyer_id"] != user_id and deal["seller_id"] != user_id:
            await call.answer("Нет прав.", show_alert=True)
            return

        if deal["status"] not in ["created", "waiting_payment"]:
            await call.answer("Оплаченную сделку нельзя отменить в одностороннем порядке!", show_alert=True)
            return

        await db.execute("UPDATE deals SET status = 'cancelled' WHERE id = ?", (deal_id,))
        await db.commit()

    await call.answer("Сделка отменена.")
    await render_deal_view(call.message, deal_id, user_id, is_edit=True)

# 5. Открытие спора
@router.callback_query(F.data.startswith("open_dispute:"))
async def cb_open_dispute(call: CallbackQuery):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)) as cursor:
            deal = await cursor.fetchone()

        if not deal or (deal["buyer_id"] != user_id and deal["seller_id"] != user_id):
            await call.answer("Нет прав.", show_alert=True)
            return

        if deal["status"] not in ["paid", "shipped"]:
            await call.answer("Спор можно открыть только по оплаченной сделке.", show_alert=True)
            return

        await db.execute("UPDATE deals SET status = 'dispute' WHERE id = ?", (deal_id,))
        await db.commit()

    await call.answer("⚖️ Сделка передана в арбитраж администратору.", show_alert=True)

    # Оповещение администратора
    try:
        await bot.send_message(
            ADMIN_ID,
            f"🚨 <b>ОТКРЫТ СПОР ПО СДЕЛКЕ №{deal_id}!</b>\n\n"
            f"📌 Условия: {deal['title']}\n"
            f"💰 Сумма: {deal['amount']:,.2f} ₽\n"
            f"🔵 Покупатель: <code>{deal['buyer_id']}</code>\n"
            f"🟢 Продавец: <code>{deal['seller_id']}</code>\n"
            f"Инициатор спора: <code>{user_id}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔎 Открыть сделку", callback_data=f"view_deal:{deal_id}")]
            ])
        )
    except Exception:
        pass

    await render_deal_view(call.message, deal_id, user_id, is_edit=True)

# ══════════════════════════════════════════════════════════════════════════════
# АНОНИМНЫЙ МЕДИА-ЧАТ ВНУТРИ СДЕЛКИ
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("open_chat:"))
async def cb_open_chat(call: CallbackQuery, state: FSMContext):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)) as cursor:
            deal = await cursor.fetchone()

    if not deal or (deal["buyer_id"] != user_id and deal["seller_id"] != user_id):
        await call.answer("Вы не участвуете в этой сделке.", show_alert=True)
        return

    await state.set_state(ChatProxyStates.in_chat)
    await state.update_data(deal_id=deal_id)

    role_label = "🔵 Покупатель" if user_id == deal["buyer_id"] else "🟢 Продавец"

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🚪 Покинуть диалог")]],
        resize_keyboard=True
    )

    await call.message.answer(
        f"💬 <b>Анонимный чат по сделке №{deal_id} активирован!</b>\n\n"
        f"Ваша роль: <b>{role_label}</b>\n"
        f"Все сообщения, фото, файлы и голосовые будут анонимно пересылаться вашему партнеру.\n"
        f"Для возврата в меню сделки нажмите кнопку <b>«🚪 Покинуть диалог»</b>.",
        reply_markup=kb
    )
    await call.answer()

@router.message(ChatProxyStates.in_chat, F.text == "🚪 Покинуть диалог")
async def exit_chat_dialog(message: Message, state: FSMContext):
    data = await state.get_data()
    deal_id = data.get("deal_id")
    await state.clear()
    await message.answer("🚪 Вы покинули чат сделки.", reply_markup=get_main_keyboard())
    if deal_id:
        await render_deal_view(message, deal_id, message.from_user.id)

@router.message(ChatProxyStates.in_chat)
async def proxy_chat_relay(message: Message, state: FSMContext):
    data = await state.get_data()
    deal_id = data.get("deal_id")
    user_id = message.from_user.id

    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)) as cursor:
            deal = await cursor.fetchone()

    if not deal:
        await state.clear()
        await message.answer("Сделка не найдена.", reply_markup=get_main_keyboard())
        return

    is_buyer = (user_id == deal["buyer_id"])
    recipient_id = deal["seller_id"] if is_buyer else deal["buyer_id"]
    sender_prefix = "🔵 <b>[Покупатель]:</b>" if is_buyer else "🟢 <b>[Продавец]:</b>"

    if not recipient_id:
        await message.answer("⚠️ Второй участник еще не подключился к сделке.")
        return

    try:
        if message.text:
            await bot.send_message(recipient_id, f"{sender_prefix} {message.text}")
        elif message.photo:
            caption = f"{sender_prefix} {message.caption}" if message.caption else sender_prefix
            await bot.send_photo(recipient_id, message.photo[-1].file_id, caption=caption)
        elif message.voice:
            await bot.send_voice(recipient_id, message.voice.file_id, caption=sender_prefix)
        elif message.video_note:
            await bot.send_message(recipient_id, f"{sender_prefix} <i>(Видеосообщение ниже)</i>")
            await bot.send_video_note(recipient_id, message.video_note.file_id)
        elif message.document:
            caption = f"{sender_prefix} {message.caption}" if message.caption else sender_prefix
            await bot.send_document(recipient_id, message.document.file_id, caption=caption)
    except Exception as e:
        logger.error(f"Ошибка ретрансляции чата: {e}")
        await message.answer("⚠️ Не удалось доставить сообщение собеседнику.")

# ══════════════════════════════════════════════════════════════════════════════
# ОТЗЫВЫ И РЕЙТИНГ (1-5 ЗВЁЗД)
# ══════════════════════════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("leave_review:"))
async def cb_leave_review(call: CallbackQuery, state: FSMContext):
    deal_id = int(call.data.split(":")[1])
    user_id = call.from_user.id

    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)) as cursor:
            deal = await cursor.fetchone()

    if not deal or deal["buyer_id"] != user_id:
        await call.answer("Только Покупатель оставляет отзыв Продавцу.", show_alert=True)
        return

    await state.set_state(ReviewStates.rating)
    await state.update_data(deal_id=deal_id, target_id=deal["seller_id"])

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⭐ 1", callback_data="rate:1"),
            InlineKeyboardButton(text="⭐ 2", callback_data="rate:2"),
            InlineKeyboardButton(text="⭐ 3", callback_data="rate:3"),
            InlineKeyboardButton(text="⭐ 4", callback_data="rate:4"),
            InlineKeyboardButton(text="⭐ 5", callback_data="rate:5"),
        ]
    ])
    await call.message.edit_text("⭐ <b>Пожалуйста, оцените работу Продавца:</b>", reply_markup=kb)

@router.callback_query(ReviewStates.rating, F.data.startswith("rate:"))
async def cb_rate_score(call: CallbackQuery, state: FSMContext):
    rating = int(call.data.split(":")[1])
    await state.update_data(rating=rating)
    await state.set_state(ReviewStates.comment)

    await call.message.edit_text(
        f"Вы выбрали оценку: <b>{rating} / 5 ⭐</b>\n\n"
        f"📝 <b>Напишите текстовый комментарий к отзыву:</b>\n"
        f"<i>(Или отправьте «-», чтобы оставить без комментария)</i>"
    )

@router.message(ReviewStates.comment)
async def process_review_comment(message: Message, state: FSMContext):
    comment_text = message.text.strip()
    if comment_text == "-":
        comment_text = "Без текстового комментария"

    data = await state.get_data()
    deal_id = data["deal_id"]
    target_id = data["target_id"]
    rating = data["rating"]

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            INSERT INTO reviews (deal_id, target_user_id, author_user_id, rating, comment, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (deal_id, target_id, message.from_user.id, rating, comment_text, datetime.now().isoformat()))
        await db.execute("""
            UPDATE users SET rating_sum = rating_sum + ?, rating_count = rating_count + 1 WHERE user_id = ?
        """, (rating, target_id))
        await db.commit()

    await state.clear()
    await message.answer("🎉 <b>Спасибо за отзыв! Ваша оценка внесена в профиль Продавца.</b>", reply_markup=get_main_keyboard())

# ══════════════════════════════════════════════════════════════════════════════
# ПРОФИЛЬ, ВЫВОД СРЕДСТВ И СПИСОК СДЕЛОК
# ══════════════════════════════════════════════════════════════════════════════
@router.message(F.text == "👤 Профиль")
async def show_profile(message: Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    if not user:
        await message.answer("Профиль не найден.")
        return

    rating_avg = round(user["rating_sum"] / user["rating_count"], 2) if user["rating_count"] > 0 else 0.0

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT COUNT(*) FROM deals WHERE (buyer_id = ? OR seller_id = ?) AND status = 'completed'", (user_id, user_id)) as cur:
            completed_deals = (await cur.fetchone())[0]

    text = (
        f"👤 <b>ВАШ ЛИЧНЫЙ ПРОФИЛЬ</b>\n"
        f"────────────────────────\n"
        f"🆔 <b>Ваш Telegram ID:</b> <code>{user_id}</code>\n"
        f"💰 <b>Баланс к выводу:</b> {user['balance']:,.2f} ₽\n"
        f"⭐ <b>Рейтинг:</b> {rating_avg} / 5.0 (Отзывов: {user['rating_count']})\n"
        f"🤝 <b>Успешных сделок:</b> {completed_deals}\n"
        f"────────────────────────\n"
        f"<i>Передавайте ваш Telegram ID партнеру для быстрого добавления в сделку.</i>"
    )
    await message.answer(text, reply_markup=get_main_keyboard())

@router.message(F.text == "📂 Мои сделки")
async def show_my_deals(message: Message):
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM deals 
            WHERE (buyer_id = ? OR seller_id = ?) 
            ORDER BY id DESC LIMIT 10
        """, (user_id, user_id)) as cur:
            deals = await cur.fetchall()

    if not deals:
        await message.answer("У вас пока нет активных или завершённых сделок.", reply_markup=get_main_keyboard())
        return

    kb_buttons = []
    for d in deals:
        role_ico = "🔵" if d["buyer_id"] == user_id else "🟢"
        btn_text = f"{role_ico} №{d['id']} — {d['title'][:20]} ({d['amount']:,.0f} ₽)"
        kb_buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"view_deal:{d['id']}")])

    await message.answer("📂 <b>Ваши последние 10 сделок:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons))

@router.message(F.text == "💳 Вывод средств")
async def start_withdraw(message: Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    if not user:
        return

    balance = user["balance"]
    if balance < MIN_WITHDRAW:
        await message.answer(
            f"💳 <b>Вывод средств</b>\n\n"
            f"Ваш текущий баланс: <b>{balance:,.2f} ₽</b>\n"
            f"Минимальная сумма для вывода: <b>{MIN_WITHDRAW:,.2f} ₽</b>\n\n"
            f"📅 <b>График выплат:</b> Выплаты производятся каждый <b>вторник</b> и <b>четверг</b>."
        )
        return

    await state.set_state(WithdrawStates.amount)
    await message.answer(
        f"💳 <b>Вывод средств</b>\n\n"
        f"Доступно к выводу: <b>{balance:,.2f} ₽</b>\n"
        f"📅 <i>Выплаты производятся каждый вторник и четверг.</i>\n\n"
        f"Введите сумму, которую хотите вывести (мин. {MIN_WITHDRAW:,.0f} ₽):",
        reply_markup=get_cancel_reply_kb()
    )

@router.message(WithdrawStates.amount)
async def process_withdraw_amount(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Вывод отменен.", reply_markup=get_main_keyboard())
        return

    user = await get_user(message.from_user.id)
    try:
        amount = float(message.text.replace(",", ".").strip())
        if amount < MIN_WITHDRAW or amount > user["balance"]:
            await message.answer(f"⚠️ Сумма должна быть от {MIN_WITHDRAW:,.0f} ₽ и не превышать ваш баланс ({user['balance']:,.2f} ₽):")
            return
    except ValueError:
        await message.answer("⚠️ Введите корректное число:")
        return

    await state.update_data(amount=amount)
    await state.set_state(WithdrawStates.requisites)
    await message.answer("💳 <b>Введите реквизиты для выплаты:</b>\n<i>(Номер карты РФ / СБП с указанием банка)</i>")

@router.message(WithdrawStates.requisites)
async def process_withdraw_requisites(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Вывод отменен.", reply_markup=get_main_keyboard())
        return

    requisites = message.text.strip()
    data = await state.get_data()
    amount = data["amount"]
    user_id = message.from_user.id

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
        cursor = await db.execute("""
            INSERT INTO withdrawals (user_id, amount, requisites, status, created_at)
            VALUES (?, ?, ?, 'pending', ?)
        """, (user_id, amount, requisites, datetime.now().isoformat()))
        withdraw_id = cursor.lastrowid
        await db.commit()

    await state.clear()
    await message.answer(
        f"✅ <b>Заявка на вывод №{withdraw_id} принята в обработку!</b>\n\n"
        f"Сумма: <b>{amount:,.2f} ₽</b>\n"
        f"Реквизиты: <code>{requisites}</code>\n\n"
        f"📅 Средства поступят в ближайший расчетный день (<b>вторник</b> или <b>четверг</b>).",
        reply_markup=get_main_keyboard()
    )

    # Оповещение администратора
    try:
        await bot.send_message(
            ADMIN_ID,
            f"💸 <b>НОВАЯ ЗАЯВКА НА ВЫВОД №{withdraw_id}!</b>\n\n"
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
async def show_help(message: Message):
    text = (
        f"🛡 <b>Гарант-сервис «BastionPRO»</b>\n"
        f"────────────────────────\n"
        f"• Комиссия платформы: <b>40%</b> (удерживается из выплаты Продавцу)\n"
        f"• Минимальная сумма сделки: <b>100 ₽</b>\n"
        f"• Выплаты баланса: <b>каждый вторник и четверг</b> (от 500 ₽)\n\n"
        f"По всем спорным ситуациям и вопросам обращайтесь напрямую к арбитру:\n"
        f"👨‍💻 <b>Официальная поддержка:</b> {SUPPORT_HANDLE}"
    )
    await message.answer(text, reply_markup=get_main_keyboard())

@router.message(F.text == "🌐 Подключить OnyxVPN")
async def open_onyx_vpn(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Перейти в OnyxVPN", url="https://t.me/vpnonyxxbot")]
    ])
    await message.answer(
        "🌐 <b>OnyxVPN — безопасный и скоростной доступ в интернет</b>\n\n"
        "• Высокая скорость без ограничений трафика\n"
        "• Надежное шифрование данных и отсутствие логов\n"
        "• Мгновенное подключение на смартфонах и ПК",
        reply_markup=kb
    )

# ══════════════════════════════════════════════════════════════════════════════
# АДМИНИСТРАТИВНАЯ ПАНЕЛЬ И АРБИТРАЖ
# ══════════════════════════════════════════════════════════════════════════════
@router.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            total_users = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM deals WHERE status = 'completed'") as cur:
            completed_deals = (await cur.fetchone())[0]
        async with db.execute("SELECT SUM(fee_amount) FROM platform_vault") as cur:
            vault_sum = (await cur.fetchone())[0] or 0.0

    text = (
        f"👑 <b>ПАНЕЛЬ УПРАВЛЕНИЯ АДМИНИСТРАТОРА</b>\n"
        f"────────────────────────\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"✅ Закрытых сделок: <b>{completed_deals}</b>\n"
        f"💰 Заработано комиссий (40%): <b>{vault_sum:,.2f} ₽</b>\n"
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

    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)) as cursor:
            deal = await cursor.fetchone()

        if not deal:
            await call.answer("Сделка не найдена.")
            return

        # Возврат полной суммы на баланс покупателя
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (deal["amount"], deal["buyer_id"]))
        await db.execute("UPDATE deals SET status = 'cancelled' WHERE id = ?", (deal_id,))
        await db.commit()

    await call.answer("Средства возвращены Покупателю на баланс.", show_alert=True)
    try:
        await bot.send_message(deal["buyer_id"], f"⚖️ По решению арбитража сделка №{deal_id} отменена. Сумма {deal['amount']:,.2f} ₽ возвращена на ваш баланс.")
        await bot.send_message(deal["seller_id"], f"⚖️ По решению арбитража сделка №{deal_id} отменена в пользу Покупателя.")
    except Exception:
        pass
    await render_deal_view(call.message, deal_id, call.from_user.id, is_edit=True)

@router.callback_query(F.data.startswith("admin_payout:"))
async def cb_admin_payout(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    deal_id = int(call.data.split(":")[1])

    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)) as cursor:
            deal = await cursor.fetchone()

        if not deal:
            await call.answer("Сделка не найдена.")
            return

        # Выплата продавцу 60%, платформе 40%
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (deal["seller_receives"], deal["seller_id"]))
        await db.execute("INSERT INTO platform_vault (deal_id, fee_amount, created_at) VALUES (?, ?, ?)",
                         (deal_id, deal["fee"], datetime.now().isoformat()))
        await db.execute("UPDATE deals SET status = 'completed' WHERE id = ?", (deal_id,))
        await db.commit()

    await call.answer("Средства выплачены Продавцу.", show_alert=True)
    try:
        await bot.send_message(deal["seller_id"], f"⚖️ По решению арбитража сделка №{deal_id} закрыта. Сумма {deal['seller_receives']:,.2f} ₽ зачислена на ваш баланс.")
        await bot.send_message(deal["buyer_id"], f"⚖️ По решению арбитража спор по сделке №{deal_id} закрыт в пользу Продавца.")
    except Exception:
        pass
    await render_deal_view(call.message, deal_id, call.from_user.id, is_edit=True)

@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminBroadcast.text)
    await call.message.answer("📢 Введите текст рассылки для всех зарегистрированных пользователей:", reply_markup=get_cancel_reply_kb())
    await call.answer()

@router.message(AdminBroadcast.text)
async def process_admin_broadcast(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Рассылка отменена.", reply_markup=get_main_keyboard())
        return

    text = message.text
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT user_id FROM users") as cur:
            users = await cur.fetchall()

    await state.clear()
    sent_count = 0
    for u in users:
        try:
            await bot.send_message(u[0], text)
            sent_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass

    await message.answer(f"✅ Рассылка завершена! Доставлено пользователям: {sent_count}", reply_markup=get_main_keyboard())

# Отмена любых FSM сценариев через Inline-кнопку
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
    logger.info("Запуск бота BastionPRO...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())