import asyncio
from datetime import datetime, timezone
import html
import logging
import os
import secrets
import sqlite3
from urllib.parse import urlencode

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
import aiohttp
from dotenv import load_dotenv

# ============================================================
# НАСТРОЙКИ И КОНСТАНТЫ
# ============================================================
load_dotenv(override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip() or "0")
DB_PATH = os.getenv("DB_PATH", "dating_bot.sqlite3").strip()

PAYMENTS_ENABLED = os.getenv("PAYMENTS_ENABLED", "0").strip() == "1"
BOOST_PRICE_RUB = int(os.getenv("BOOST_PRICE_RUB", "99").strip() or "99")
YOOMONEY_WALLET = (
    os.getenv("YOOMONEY_WALLET", "").strip().strip('"').strip("'")
)
YOOMONEY_TOKEN = os.getenv("YOOMONEY_TOKEN", "").strip().strip('"').strip("'")

# Партнёрская кнопка Velvet VPN
VPN_BUTTON_TEXT = "💎 Velvet VPN › Доступно всё"
VPN_BUTTON_URL = "https://t.me/velvet?start=3SLkWOoF"
VPN_BUTTON = (VPN_BUTTON_TEXT, VPN_BUTTON_URL)

if (
    not BOT_TOKEN
    or ":" not in BOT_TOKEN
    or "НОВЫЙ_ТОКЕН" in BOT_TOKEN
    or "ВАШ_ТОКЕН" in BOT_TOKEN
):
    raise SystemExit("❌ ОШИБКА: Укажи настоящий BOT_TOKEN в файле .env")

if BOOST_PRICE_RUB < 2:
    raise SystemExit(
        "❌ ОШИБКА: Минимальная сумма перевода в ЮMoney — 2 рубля (BOOST_PRICE_RUB >= 2)"
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

dp = Dispatcher()
dp.message.filter(F.chat.type == "private")
dp.callback_query.filter(F.message.chat.type == "private")

# ============================================================
# БАЗА ДАННЫХ И АВТО-МИГРАЦИЯ
# ============================================================
db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row

db.executescript(
    """
    PRAGMA journal_mode = WAL;
    PRAGMA synchronous = NORMAL;

    CREATE TABLE IF NOT EXISTS profiles (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        name TEXT NOT NULL,
        age INTEGER NOT NULL CHECK(age BETWEEN 14 AND 80),
        city TEXT NOT NULL,
        bio TEXT NOT NULL,
        photo_id TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        boost_until INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS reactions (
        user_id INTEGER NOT NULL,
        target_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(user_id, target_id)
    );

    CREATE TABLE IF NOT EXISTS matches (
        user1 INTEGER NOT NULL,
        user2 INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(user1, user2)
    );

    CREATE TABLE IF NOT EXISTS blocks (
        user_id INTEGER NOT NULL,
        blocked_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(user_id, blocked_id)
    );

    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reporter_id INTEGER NOT NULL,
        target_id INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        product TEXT NOT NULL,
        amount INTEGER NOT NULL,
        currency TEXT NOT NULL,
        status TEXT NOT NULL,
        telegram_charge_id TEXT UNIQUE,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_profiles_search ON profiles(active, age, boost_until);
    CREATE INDEX IF NOT EXISTS idx_reactions_user ON reactions(user_id, target_id);
    CREATE INDEX IF NOT EXISTS idx_blocks_pair ON blocks(user_id, blocked_id);
    """
)

existing_columns = {
    row["name"] for row in db.execute("PRAGMA table_info(profiles)").fetchall()
}
if "photo_id" not in existing_columns:
    db.execute("ALTER TABLE profiles ADD COLUMN photo_id TEXT")
if "username" not in existing_columns:
    db.execute("ALTER TABLE profiles ADD COLUMN username TEXT")
db.commit()


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ И КЛАВИАТУРЫ
# ============================================================
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def age_group(age: int) -> str:
    return "minor" if age < 18 else "adult"


def get_profile(user_id: int):
    return db.execute(
        "SELECT * FROM profiles WHERE user_id = ?",
        (user_id,),
    ).fetchone()


def buttons(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """Универсальный генератор красивых кнопок.

    Если второй параметр начинается с http:// или https:// — создаёт
    URL-кнопку.
    """
    keyboard = []
    for row in rows:
        kb_row = []
        for text, action in row:
            if action.startswith(("http://", "https://")):
                kb_row.append(InlineKeyboardButton(text=text, url=action))
            else:
                kb_row.append(
                    InlineKeyboardButton(text=text, callback_data=action)
                )
        keyboard.append(kb_row)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def main_menu() -> InlineKeyboardMarkup:
    return buttons(
        [
            [("🔥 Смотреть анкеты", "browse")],
            [("👤 Моя анкета", "my_profile"), ("💌 Симпатии", "matches")],
            [("🚀 Поднять в ТОП", "boost"), ("⚙️ Настройки", "settings")],
            [("🛡 Безопасность", "safety")],
            [VPN_BUTTON],
        ]
    )


def candidate_menu(target_id: int) -> InlineKeyboardMarkup:
    return buttons(
        [
            [
                ("❤️ Нравится", f"like:{target_id}"),
                ("⏭ Следующая", f"skip:{target_id}"),
            ],
            [
                ("🚩 Жалоба", f"report:{target_id}"),
                ("⛔ В ЧС", f"block:{target_id}"),
            ],
            [VPN_BUTTON],
            [("🏠 В главное меню", "menu")],
        ]
    )


def profile_text(profile: sqlite3.Row) -> str:
    name = html.escape(profile["name"])
    city = html.escape(profile["city"])
    bio = html.escape(profile["bio"])
    is_boosted = profile["boost_until"] > int(
        datetime.now(timezone.utc).timestamp()
    )
    boost_badge = "  ⚡️ <b>TOP</b>" if is_boosted else ""
    return (
        f"👤 <b>{name}, {profile['age']}</b>{boost_badge}\n"
        f"📍 <i>г. {city}</i>\n\n"
        f"<blockquote>💬 {bio}</blockquote>"
    )


def user_contact_link(profile: sqlite3.Row) -> str:
    name = html.escape(profile["name"])
    link = f'<a href="tg://user?id={profile["user_id"]}"><b>{name}</b></a>'
    if profile["username"]:
        return f"{link} (@{html.escape(profile['username'])})"
    return link


async def send_profile_card(
    message: Message,
    profile: sqlite3.Row,
    reply_markup: InlineKeyboardMarkup,
    extra_text: str = "",
):
    text = profile_text(profile)
    if extra_text:
        text += f"\n\n{extra_text}"

    if profile["photo_id"]:
        try:
            await message.answer_photo(
                photo=profile["photo_id"],
                caption=text,
                reply_markup=reply_markup,
            )
            return
        except TelegramAPIError:
            pass
    await message.answer(text, reply_markup=reply_markup)


async def clear_old_markup(callback: CallbackQuery):
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramAPIError:
        pass


def pair_allowed(user_id: int, target_id: int) -> bool:
    if user_id == target_id:
        return False
    user = get_profile(user_id)
    target = get_profile(target_id)
    if not user or not target:
        return False
    if not user["active"] or not target["active"]:
        return False
    if age_group(user["age"]) != age_group(target["age"]):
        return False
    block = db.execute(
        """
        SELECT 1 FROM blocks
        WHERE (user_id = ? AND blocked_id = ?)
           OR (user_id = ? AND blocked_id = ?)
        """,
        (user_id, target_id, target_id, user_id),
    ).fetchone()
    return block is None


async def notify_admin(bot: Bot, text: str):
    if not ADMIN_ID:
        return
    try:
        await bot.send_message(ADMIN_ID, text)
    except TelegramAPIError:
        logging.exception("Не удалось отправить сообщение администратору")


# ============================================================
# ФУНКЦИИ ОПЛАТЫ YOOMONEY (ЮMONEY)
# ============================================================
def build_yoomoney_url(order_id: str, amount_rub: int) -> str:
    params = {
        "receiver": YOOMONEY_WALLET,
        "quickpay-form": "button",
        "paymentType": "AC",
        "sum": str(amount_rub),
        "label": order_id,
        "targets": f"Поднятие анкеты на 24 часа (#{order_id[:8]})",
    }
    return f"https://yoomoney.ru/quickpay/confirm?{urlencode(params)}"


async def verify_yoomoney_payment(
    order_id: str, expected_amount: int
) -> str | None:
    url = "https://yoomoney.ru/api/operation-history"
    headers = {
        "Authorization": f"Bearer {YOOMONEY_TOKEN}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "label": order_id,
        "records": "5",
    }

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, data=data) as resp:
                if resp.status != 200:
                    logging.error("Ошибка ответа ЮMoney API: %s", resp.status)
                    return None
                result = await resp.json()
    except Exception:
        logging.exception("Ошибка при подключении к ЮMoney API")
        return None

    if "error" in result:
        logging.error("Ошибка ЮMoney API: %s", result["error"])
        return None

    for op in result.get("operations", []):
        amount_received = float(op.get("amount", 0))
        if (
            op.get("status") == "success"
            and op.get("direction") == "in"
            and op.get("label") == order_id
            and amount_received >= expected_amount * 0.95
        ):
            return str(op.get("operation_id"))

    return None


# ============================================================
# СОСТОЯНИЯ (FSM)
# ============================================================
class ProfileForm(StatesGroup):
    name = State()
    age = State()
    city = State()
    bio = State()
    photo = State()


class SupportForm(StatesGroup):
    message = State()


# ============================================================
# РЕГИСТРАЦИЯ И РЕДАКТИРОВАНИЕ АНКЕТЫ
# ============================================================
@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    profile = get_profile(message.from_user.id)
    if profile:
        if profile["username"] != message.from_user.username:
            db.execute(
                "UPDATE profiles SET username = ? WHERE user_id = ?",
                (message.from_user.username, message.from_user.id),
            )
            db.commit()
        name = html.escape(profile["name"])
        await message.answer(
            f"✨ <b>С возвращением, {name}!</b>\n\n"
            "Готов(а) к новым знакомствам? Выбирай нужный раздел в меню ниже 👇",
            reply_markup=main_menu(),
        )
        return

    await message.answer(
        "💘 <b>Добро пожаловать в бот знакомств!</b>\n\n"
        "Здесь ты можешь найти новых друзей, компанию для прогулок или свою вторую половинку.\n\n"
        "<blockquote>"
        "✨ Быстрое создание анкеты за 1 минуту\n"
        "💌 Открытие контактов только при взаимной симпатии\n"
        "🔒 Безопасность: подростки (14–17) и взрослые (18+) разделены"
        "</blockquote>\n\n"
        "Нажми кнопку ниже, чтобы начать:",
        reply_markup=buttons(
            [
                [("✨ Создать анкету", "profile:create")],
                [VPN_BUTTON],
            ]
        ),
    )


@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("↩️ Действие отменено.", reply_markup=main_menu())


@dp.callback_query(F.data == "profile:create")
async def create_profile(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ProfileForm.name)
    await callback.answer()
    await callback.message.answer(
        "✨ <b>Шаг 1 из 5 — Имя</b>\n\n"
        "Как тебя называть? Напиши своё имя (от 2 до 30 символов).\n\n"
        "<i>Для отмены в любой момент отправь /cancel</i>"
    )


@dp.message(ProfileForm.name)
async def form_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not 2 <= len(name) <= 30:
        await message.answer("⚠️ Имя должно содержать от 2 до 30 символов.")
        return
    await state.update_data(name=name)
    await state.set_state(ProfileForm.age)
    await message.answer(
        "🎂 <b>Шаг 2 из 5 — Возраст</b>\n\n"
        "Сколько тебе лет? Введи число от <b>14</b> до <b>80</b>."
    )


@dp.message(ProfileForm.age)
async def form_age(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit() or not 14 <= int(text) <= 80:
        await message.answer("⚠️ Введи корректный возраст числом от 14 до 80.")
        return
    await state.update_data(age=int(text))
    await state.set_state(ProfileForm.city)
    await message.answer(
        "📍 <b>Шаг 3 из 5 — Город</b>\n\nВ каком городе ты живёшь?"
    )


@dp.message(ProfileForm.city)
async def form_city(message: Message, state: FSMContext):
    city = (message.text or "").strip()
    if not 2 <= len(city) <= 50:
        await message.answer("⚠️ Название города: от 2 до 50 символов.")
        return
    await state.update_data(city=city)
    await state.set_state(ProfileForm.bio)
    await message.answer(
        "📝 <b>Шаг 4 из 5 — О себе</b>\n\n"
        "Расскажи немного о своих увлечениях, интересах и кого хочешь найти (от 5 до 300 символов).\n\n"
        "<i>🔒 Не указывай номер телефона или домашний адрес в открытом описании.</i>"
    )


@dp.message(ProfileForm.bio)
async def form_bio(message: Message, state: FSMContext):
    bio = (message.text or "").strip()
    if not 5 <= len(bio) <= 300:
        await message.answer(
            "⚠️ Описание должно содержать от 5 до 300 символов."
        )
        return
    await state.update_data(bio=bio)
    await state.set_state(ProfileForm.photo)
    await message.answer(
        "📸 <b>Шаг 5 из 5 — Фотография</b>\n\n"
        "Отправь своё лучшее фото! Анкеты с фотографией получают в 5 раз больше симпатий.\n\n"
        "<i>Или нажми кнопку ниже, чтобы продолжить без фото:</i>",
        reply_markup=buttons([[("⏭ Пропустить фото", "profile:skip_photo")]]),
    )


async def finish_profile_saving(
    message: Message, user, state: FSMContext, photo_id: str | None
):
    data = await state.get_data()
    user_id = user.id
    old_profile = get_profile(user_id)

    with db:
        if old_profile and age_group(old_profile["age"]) != age_group(
            data["age"]
        ):
            db.execute(
                "DELETE FROM reactions WHERE user_id = ? OR target_id = ?",
                (user_id, user_id),
            )
            db.execute(
                "DELETE FROM matches WHERE user1 = ? OR user2 = ?",
                (user_id, user_id),
            )

        db.execute(
            """
            INSERT INTO profiles
                (user_id, username, name, age, city, bio, photo_id, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                name = excluded.name,
                age = excluded.age,
                city = excluded.city,
                bio = excluded.bio,
                photo_id = excluded.photo_id,
                active = 1
            """,
            (
                user_id,
                user.username,
                data["name"],
                data["age"],
                data["city"],
                data["bio"],
                photo_id,
                now_iso(),
            ),
        )

    await state.clear()
    profile = get_profile(user_id)
    await send_profile_card(
        message,
        profile,
        reply_markup=main_menu(),
        extra_text="✅ <b>Твоя анкета готова и опубликована!</b>",
    )


@dp.message(ProfileForm.photo, F.photo)
async def form_photo_received(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await finish_profile_saving(message, message.from_user, state, photo_id)


@dp.callback_query(ProfileForm.photo, F.data == "profile:skip_photo")
async def form_photo_skipped(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await clear_old_markup(callback)
    await finish_profile_saving(
        callback.message, callback.from_user, state, None
    )


@dp.message(ProfileForm.photo)
async def form_photo_invalid(message: Message):
    await message.answer(
        "📸 Пожалуйста, отправь фотографию (как картинку, а не документ) или нажми кнопку «Пропустить фото»."
    )


# ============================================================
# ПОИСК И СИМПАТИИ
# ============================================================
async def show_candidate(message: Message, viewer_id: int):
    viewer = get_profile(viewer_id)
    if not viewer:
        await message.answer(
            "Сначала создай свою анкету!",
            reply_markup=buttons(
                [[("✨ Создать анкету", "profile:create")], [VPN_BUTTON]]
            ),
        )
        return
    if not viewer["active"]:
        await message.answer(
            "🙈 <b>Твоя анкета сейчас скрыта.</b>\n"
            "Включи показ анкеты в настройках, чтобы смотреть других пользователей.",
            reply_markup=main_menu(),
        )
        return

    min_age, max_age = (14, 17) if viewer["age"] < 18 else (18, 80)
    now_ts = int(datetime.now(timezone.utc).timestamp())

    candidate = db.execute(
        """
        SELECT p.*
        FROM profiles p
        WHERE p.user_id != ?
          AND p.active = 1
          AND p.age BETWEEN ? AND ?
          AND NOT EXISTS (
              SELECT 1 FROM reactions r
              WHERE r.user_id = ? AND r.target_id = p.user_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM blocks b
              WHERE (b.user_id = ? AND b.blocked_id = p.user_id)
                 OR (b.user_id = p.user_id AND b.blocked_id = ?)
          )
        ORDER BY
            CASE WHEN p.boost_until > ? THEN 0 ELSE 1 END,
            RANDOM()
        LIMIT 1
        """,
        (viewer_id, min_age, max_age, viewer_id, viewer_id, viewer_id, now_ts),
    ).fetchone()

    if not candidate:
        await message.answer(
            "✨ <b>Ты посмотрел(а) все доступные анкеты!</b>\n\n"
            "Загляни сюда чуть позже — скоро появятся новые люди. "
            "А чтобы тебя заметили первым, можешь поднять свою анкету в ТОП 🚀",
            reply_markup=main_menu(),
        )
        return

    await send_profile_card(
        message,
        candidate,
        reply_markup=candidate_menu(candidate["user_id"]),
    )


@dp.callback_query(F.data == "browse")
async def browse(callback: CallbackQuery):
    await callback.answer()
    await show_candidate(callback.message, callback.from_user.id)


@dp.callback_query(F.data.startswith("skip:"))
async def skip(callback: CallbackQuery):
    user_id = callback.from_user.id
    target_id = int(callback.data.split(":")[1])
    await clear_old_markup(callback)

    if not pair_allowed(user_id, target_id):
        await callback.answer("Анкета недоступна.", show_alert=True)
        await show_candidate(callback.message, user_id)
        return

    db.execute(
        """
        INSERT OR IGNORE INTO reactions (user_id, target_id, action, created_at)
        VALUES (?, ?, 'skip', ?)
        """,
        (user_id, target_id, now_iso()),
    )
    db.commit()
    await callback.answer("⏭ Следующая анкета")
    await show_candidate(callback.message, user_id)


@dp.callback_query(F.data.startswith("like:"))
async def like(callback: CallbackQuery):
    user_id = callback.from_user.id
    target_id = int(callback.data.split(":")[1])
    await clear_old_markup(callback)

    if not pair_allowed(user_id, target_id):
        await callback.answer("Анкета уже недоступна.", show_alert=True)
        await show_candidate(callback.message, user_id)
        return

    new_match = False
    with db:
        db.execute(
            """
            INSERT OR IGNORE INTO reactions (user_id, target_id, action, created_at)
            VALUES (?, ?, 'like', ?)
            """,
            (user_id, target_id, now_iso()),
        )
        own_reaction = db.execute(
            "SELECT action FROM reactions WHERE user_id = ? AND target_id = ?",
            (user_id, target_id),
        ).fetchone()

        if own_reaction["action"] != "like":
            await callback.answer("Для этой анкеты уже выбран другой ответ.")
            return

        reciprocal = db.execute(
            """
            SELECT 1 FROM reactions
            WHERE user_id = ? AND target_id = ? AND action = 'like'
            """,
            (target_id, user_id),
        ).fetchone()

        if reciprocal:
            user1, user2 = sorted((user_id, target_id))
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO matches (user1, user2, created_at)
                VALUES (?, ?, ?)
                """,
                (user1, user2, now_iso()),
            )
            new_match = cursor.rowcount == 1

    await callback.answer("❤️ Симпатия отправлена!")

    if new_match:
        target = get_profile(target_id)
        current = get_profile(user_id)

        await callback.message.answer(
            f"💘 <b>У вас взаимная симпатия!</b>\n\n"
            f"Тебе ответил(а) взаимностью: {user_contact_link(target)}\n"
            f"📍 Город: <i>{html.escape(target['city'])}</i>\n\n"
            f"👉 Нажми на имя, чтобы написать сообщение!",
            reply_markup=buttons([[VPN_BUTTON]]),
        )
        try:
            await callback.bot.send_message(
                target_id,
                f"💘 <b>У вас взаимная симпатия!</b>\n\n"
                f"Вы понравились друг другу с: {user_contact_link(current)}\n"
                f"📍 Город: <i>{html.escape(current['city'])}</i>\n\n"
                f"👉 Нажми на имя, чтобы начать общение!",
                reply_markup=buttons(
                    [[("💌 Все симпатии", "matches")], [VPN_BUTTON]]
                ),
            )
        except TelegramAPIError:
            logging.info("Не удалось уведомить пользователя %s", target_id)

    await show_candidate(callback.message, user_id)


@dp.callback_query(F.data == "matches")
async def matches(callback: CallbackQuery):
    user_id = callback.from_user.id
    rows = db.execute(
        """
        SELECT p.*
        FROM matches m
        JOIN profiles p ON p.user_id =
            CASE WHEN m.user1 = ? THEN m.user2 ELSE m.user1 END
        WHERE (m.user1 = ? OR m.user2 = ?)
          AND p.active = 1
        ORDER BY m.created_at DESC
        LIMIT 30
        """,
        (user_id, user_id, user_id),
    ).fetchall()

    visible = [row for row in rows if pair_allowed(user_id, row["user_id"])]
    await callback.answer()

    if not visible:
        await callback.message.answer(
            "💌 <b>Список взаимных симпатий пока пуст</b>\n\n"
            "Смотри анкеты и ставь ❤️ — как только кто-то ответит взаимностью, "
            "здесь появится ссылка на его профиль!",
            reply_markup=main_menu(),
        )
        return

    lines = ["💌 <b>Твои взаимные симпатии:</b>\n"]
    for index, profile in enumerate(visible, 1):
        city = html.escape(profile["city"])
        lines.append(
            f"{index}. {user_contact_link(profile)}, {profile['age']} — <i>г. {city}</i>"
        )

    await callback.message.answer(
        "\n".join(lines),
        reply_markup=main_menu(),
    )


# ============================================================
# ЖАЛОБЫ И БЛОКИРОВКИ
# ============================================================
@dp.callback_query(F.data.startswith("report:"))
async def report(callback: CallbackQuery):
    user_id = callback.from_user.id
    target_id = int(callback.data.split(":")[1])
    await clear_old_markup(callback)

    if not pair_allowed(user_id, target_id):
        await callback.answer("Анкета недоступна.", show_alert=True)
        return

    with db:
        db.execute(
            "INSERT INTO reports (reporter_id, target_id, created_at) VALUES (?, ?, ?)",
            (user_id, target_id, now_iso()),
        )
        db.execute(
            """
            INSERT OR IGNORE INTO reactions (user_id, target_id, action, created_at)
            VALUES (?, ?, 'report', ?)
            """,
            (user_id, target_id, now_iso()),
        )

    await callback.answer("🚩 Жалоба отправлена")
    await callback.message.answer(
        "🚩 <i>Жалоба передана модератору на проверку.</i>"
    )

    target = get_profile(target_id)
    target_info = (
        f"{html.escape(target['name'])}, {target['age']} ({target_id})"
        if target
        else str(target_id)
    )
    await notify_admin(
        callback.bot,
        f"🚩 <b>Жалоба на анкету</b>\n\n"
        f"От: <code>{user_id}</code>\n"
        f"На: {target_info}\n"
        f"Текст анкеты: {html.escape(target['bio']) if target else 'Н/Д'}",
    )
    await show_candidate(callback.message, user_id)


@dp.callback_query(F.data.startswith("block:"))
async def block(callback: CallbackQuery):
    user_id = callback.from_user.id
    target_id = int(callback.data.split(":")[1])
    await clear_old_markup(callback)

    if user_id == target_id or not get_profile(target_id):
        await callback.answer("Анкета недоступна.")
        return

    user1, user2 = sorted((user_id, target_id))
    with db:
        db.execute(
            "INSERT OR IGNORE INTO blocks (user_id, blocked_id, created_at) VALUES (?, ?, ?)",
            (user_id, target_id, now_iso()),
        )
        db.execute(
            "DELETE FROM matches WHERE user1 = ? AND user2 = ?",
            (user1, user2),
        )

    await callback.answer("⛔ Пользователь добавлен в ЧС")
    await show_candidate(callback.message, user_id)


# ============================================================
# ПРОФИЛЬ И НАСТРОЙКИ
# ============================================================
@dp.callback_query(F.data == "my_profile")
async def my_profile(callback: CallbackQuery):
    profile = get_profile(callback.from_user.id)
    await callback.answer()
    if not profile:
        await callback.message.answer(
            "У тебя пока нет анкеты.",
            reply_markup=buttons(
                [[("✨ Создать анкету", "profile:create")], [VPN_BUTTON]]
            ),
        )
        return

    status = "🟢 Видна в поиске" if profile["active"] else "🔴 Скрыта из поиска"
    await send_profile_card(
        callback.message,
        profile,
        reply_markup=buttons(
            [
                [
                    ("✏️ Изменить анкету", "profile:create"),
                    ("🚀 В ТОП", "boost"),
                ],
                [VPN_BUTTON],
                [("🏠 В главное меню", "menu")],
            ]
        ),
        extra_text=f"Статус анкеты: <b>{status}</b>",
    )


@dp.callback_query(F.data == "settings")
async def settings(callback: CallbackQuery):
    profile = get_profile(callback.from_user.id)
    await callback.answer()
    if not profile:
        await callback.message.answer("Сначала создай анкету.")
        return

    toggle_text = (
        "🙈 Скрыть мою анкету"
        if profile["active"]
        else "👁 Показать мою анкету"
    )
    await callback.message.answer(
        "⚙️ <b>Настройки профиля</b>\n\n"
        "Здесь ты можешь временно скрыть свою анкету из поиска или полностью удалить свои данные.",
        reply_markup=buttons(
            [
                [(toggle_text, "profile:toggle")],
                [
                    ("✏️ Заполнить заново", "profile:create"),
                    ("🗑 Удалить", "profile:delete_ask"),
                ],
                [VPN_BUTTON],
                [("🏠 В главное меню", "menu")],
            ]
        ),
    )


@dp.callback_query(F.data == "profile:toggle")
async def toggle_profile(callback: CallbackQuery):
    profile = get_profile(callback.from_user.id)
    if not profile:
        await callback.answer("Анкета не найдена.")
        return

    new_value = 0 if profile["active"] else 1
    db.execute(
        "UPDATE profiles SET active = ? WHERE user_id = ?",
        (new_value, callback.from_user.id),
    )
    db.commit()
    await callback.answer()
    await callback.message.answer(
        "🟢 <b>Твоя анкета снова видна в поиске!</b>"
        if new_value
        else "🔴 <b>Твоя анкета скрыта из поиска.</b>",
        reply_markup=main_menu(),
    )


@dp.callback_query(F.data == "profile:delete_ask")
async def delete_ask(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "⚠️ <b>Ты точно хочешь удалить свою анкету?</b>\n\n"
        "Все твои симпатии, мэтчи и настройки будут безвозвратно удалены.",
        reply_markup=buttons(
            [
                [("🗑 Да, удалить навсегда", "profile:delete_confirm")],
                [("↩️ Отмена", "menu")],
            ]
        ),
    )


@dp.callback_query(F.data == "profile:delete_confirm")
async def delete_confirm(callback: CallbackQuery):
    user_id = callback.from_user.id
    with db:
        db.execute(
            "DELETE FROM reactions WHERE user_id = ? OR target_id = ?",
            (user_id, user_id),
        )
        db.execute(
            "DELETE FROM matches WHERE user1 = ? OR user2 = ?",
            (user_id, user_id),
        )
        db.execute(
            "DELETE FROM blocks WHERE user_id = ? OR blocked_id = ?",
            (user_id, user_id),
        )
        db.execute("DELETE FROM profiles WHERE user_id = ?", (user_id,))

    await callback.answer("Анкета удалена")
    await callback.message.answer(
        "🗑 <b>Твоя анкета полностью удалена.</b>\n\n"
        "Если захочешь вернуться — просто нажми /start!",
        reply_markup=buttons([[VPN_BUTTON]]),
    )


@dp.callback_query(F.data == "safety")
async def safety(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "🛡 <b>Правила безопасного общения</b>\n\n"
        "<blockquote>"
        "• Никогда не переводите незнакомцам деньги и не сообщайте коды из СМС.\n"
        "• Не публикуйте свой домашний адрес и личные документы.\n"
        "• Используйте кнопки «🚩 Жалоба» и «⛔ В ЧС» при подозрительном поведении.\n"
        "• Первые встречи назначайте только в людных общественных местах."
        "</blockquote>",
        reply_markup=main_menu(),
    )


@dp.callback_query(F.data == "menu")
async def menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await callback.message.answer(
        "🏠 <b>Главное меню</b>\nВыбирай нужное действие 👇",
        reply_markup=main_menu(),
    )


# ============================================================
# ОПЛАТА ЧЕРЕЗ ЮMONEY И ПОДДЕРЖКА
# ============================================================
@dp.callback_query(F.data == "boost")
async def boost_info(callback: CallbackQuery):
    profile = get_profile(callback.from_user.id)
    await callback.answer()
    if not profile:
        await callback.message.answer("Сначала создай анкету.")
        return
    if profile["age"] < 18:
        await callback.message.answer(
            "🔒 Платные функции доступны только пользователям старше 18 лет.",
            reply_markup=main_menu(),
        )
        return
    if not profile["active"]:
        await callback.message.answer(
            "Сначала включи показ анкеты в настройках.",
            reply_markup=main_menu(),
        )
        return
    if not PAYMENTS_ENABLED or not YOOMONEY_WALLET or not YOOMONEY_TOKEN:
        await callback.message.answer(
            "🚀 <b>Поднятие анкеты в ТОП</b>\n\n"
            "Платежи временно отключены или настраиваются администратором.",
            reply_markup=main_menu(),
        )
        return

    now_ts = int(datetime.now(timezone.utc).timestamp())
    active_boost_note = ""
    if profile["boost_until"] > now_ts:
        until_dt = datetime.fromtimestamp(profile["boost_until"], timezone.utc)
        active_boost_note = (
            f"\n⚡️ <i>У тебя уже активно поднятие до {until_dt.strftime('%d.%m.%Y %H:%M')} UTC. "
            f"Новая покупка добавит ещё +24 часа.</i>\n"
        )

    await callback.message.answer(
        "🚀 <b>Поднятие анкеты в ТОП на 24 часа</b>\n\n"
        "<blockquote>"
        "⚡️ Специальный значок <b>TOP</b> возле имени\n"
        "🔝 Приоритетный показ твоей анкеты всем пользователям\n"
        "❤️ В среднем в 4 раза больше лайков и взаимных симпатий"
        "</blockquote>\n"
        f"💳 Стоимость: <b>{BOOST_PRICE_RUB} ₽</b> (Карта РФ / ЮMoney)\n"
        f"{active_boost_note}\n"
        "<i>Покупка разовая, без скрытых подписок.</i>",
        reply_markup=buttons(
            [
                [
                    (
                        f"💳 Оплатить {BOOST_PRICE_RUB} ₽ и поднять",
                        "buy:boost",
                    )
                ],
                [VPN_BUTTON],
                [("🏠 В главное меню", "menu")],
            ]
        ),
    )


@dp.callback_query(F.data == "buy:boost")
async def buy_boost(callback: CallbackQuery):
    user_id = callback.from_user.id
    profile = get_profile(user_id)
    if not PAYMENTS_ENABLED or not YOOMONEY_WALLET or not YOOMONEY_TOKEN:
        await callback.answer("Платежи сейчас недоступны.", show_alert=True)
        return
    if not profile or profile["age"] < 18 or not profile["active"]:
        await callback.answer(
            "Покупка доступна активным анкетам 18+.", show_alert=True
        )
        return

    order_id = secrets.token_hex(12)
    db.execute(
        """
        INSERT INTO orders (order_id, user_id, product, amount, currency, status, created_at)
        VALUES (?, ?, 'boost', ?, 'RUB', 'pending', ?)
        """,
        (order_id, user_id, BOOST_PRICE_RUB, now_iso()),
    )
    db.commit()

    pay_url = build_yoomoney_url(order_id, BOOST_PRICE_RUB)

    await callback.answer()
    await callback.message.answer(
        f"🧾 <b>Счёт #{order_id[:8]} сформирован!</b>\n\n"
        f"Сумма к оплате: <b>{BOOST_PRICE_RUB} ₽</b>\n\n"
        "1️⃣ Нажми кнопку <b>«Перейти к оплате»</b> ниже.\n"
        "2️⃣ После оплаты вернись в бот и нажми <b>«🔄 Я оплатил(а) — Проверить»</b>.",
        reply_markup=buttons(
            [
                [(f"💳 Перейти к оплате ({BOOST_PRICE_RUB} ₽)", pay_url)],
                [("🔄 Я оплатил(а) — Проверить", f"check_pay:{order_id}")],
                [VPN_BUTTON],
                [("🏠 В главное меню", "menu")],
            ]
        ),
    )


@dp.callback_query(F.data.startswith("check_pay:"))
async def check_payment_callback(callback: CallbackQuery):
    order_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id

    order = db.execute(
        "SELECT * FROM orders WHERE order_id = ? AND user_id = ?",
        (order_id, user_id),
    ).fetchone()

    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    if order["status"] == "paid":
        await callback.answer(
            "✅ Этот счёт уже оплачен и активирован!", show_alert=True
        )
        await clear_old_markup(callback)
        return

    operation_id = await verify_yoomoney_payment(order_id, order["amount"])
    if not operation_id:
        await callback.answer(
            "⏳ Платёж ещё не поступил.\n"
            "Если ты только что оплатил(а), подожди 10–20 секунд и нажми кнопку проверки ещё раз.",
            show_alert=True,
        )
        return

    profile = get_profile(user_id)
    try:
        with db:
            cursor = db.execute(
                """
                UPDATE orders
                SET status = 'paid', telegram_charge_id = ?
                WHERE order_id = ? AND status = 'pending'
                """,
                (operation_id, order_id),
            )
            if cursor.rowcount != 1:
                await callback.answer("Заказ уже обработан.")
                return

            if profile and profile["age"] >= 18:
                now_ts = int(datetime.now(timezone.utc).timestamp())
                start_ts = max(now_ts, profile["boost_until"])
                boost_until = start_ts + 24 * 60 * 60
                db.execute(
                    "UPDATE profiles SET boost_until = ? WHERE user_id = ?",
                    (boost_until, user_id),
                )
            else:
                boost_until = None
    except sqlite3.IntegrityError:
        await callback.answer("Этот платёж уже был учтён ранее.")
        return

    await clear_old_markup(callback)

    if boost_until is None:
        await callback.message.answer(
            "Платёж получен, но твой профиль не найден или не подходит по возрасту. Напиши в /paysupport."
        )
        await notify_admin(
            callback.bot,
            f"⚠️ Оплачен заказ ЮMoney {order_id}, но не активирован (юзер {user_id})",
        )
        return

    until = datetime.fromtimestamp(boost_until, timezone.utc)
    await callback.answer("🎉 Оплата подтверждена!")
    await callback.message.answer(
        "⚡️ <b>Оплата прошла успешно! Твоя анкета в ТОПе!</b>\n\n"
        f"Приоритетный показ действует до: <b>{until.strftime('%d.%m.%Y %H:%M')} UTC</b>.",
        reply_markup=main_menu(),
    )


@dp.message(Command("paysupport"))
async def payment_support(message: Message, state: FSMContext):
    if not ADMIN_ID:
        await message.answer("Поддержка пока не настроена администратором.")
        return

    await state.set_state(SupportForm.message)
    await message.answer(
        "💬 <b>Поддержка по платежам</b>\n\n"
        "Опиши свою проблему одним сообщением (укажи номер счёта или время оплаты), "
        "и я сразу передам его администратору.\n\n"
        "<i>Для отмены нажми /cancel</i>"
    )


@dp.message(SupportForm.message)
async def process_support_message(message: Message, state: FSMContext):
    text = (message.text or message.caption or "").strip()
    if not text:
        await message.answer("Пожалуйста, опиши проблему текстом.")
        return

    await state.clear()
    user = message.from_user
    username = f"@{user.username}" if user.username else "без username"

    await notify_admin(
        message.bot,
        f"💳 <b>Обращение в /paysupport</b>\n"
        f"От: <a href='tg://user?id={user.id}'>{html.escape(user.full_name)}</a> ({username}, ID: <code>{user.id}</code>)\n\n"
        f"<blockquote>{html.escape(text)}</blockquote>",
    )
    await message.answer(
        "✅ <b>Твоё обращение отправлено администратору!</b>\nОжидай ответа в ближайшее время.",
        reply_markup=main_menu(),
    )


# ============================================================
# ЗАПУСК БОТА
# ============================================================
async def main():
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        me = await bot.get_me()
        logging.info("✅ Успешный вход: @%s (ID: %s)", me.username, me.id)

        await bot.set_my_commands(
            [
                BotCommand(command="start", description="🏠 Главное меню"),
                BotCommand(command="cancel", description="↩️ Отменить действие"),
                BotCommand(
                    command="paysupport", description="💬 Помощь с оплатой"
                ),
            ]
        )
        logging.info("🚀 Бот запущен и готов к работе!")
        await dp.start_polling(bot)

    except TelegramUnauthorizedError:
        masked = (
            f"{BOT_TOKEN[:6]}...{BOT_TOKEN[-4:]}"
            if len(BOT_TOKEN) > 10
            else BOT_TOKEN
        )
        logging.error(
            "❌ ОШИБКА АВТОРИЗАЦИИ: Telegram отклонил токен [%s].\n"
            "Проверь файл .env и настройки переменных (Variables) на хостинге!",
            masked,
        )
    finally:
        await bot.session.close()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
