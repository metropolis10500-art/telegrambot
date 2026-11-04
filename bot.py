import asyncio
import logging
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)


# ============================================================
# НАСТРОЙКИ
# ============================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "dating_bot.sqlite3")
BOOST_PRICE_STARS = int(os.getenv("BOOST_PRICE_STARS", "25"))
PAYMENTS_ENABLED = os.getenv("PAYMENTS_ENABLED", "0") == "1"

if not BOT_TOKEN or BOT_TOKEN == "вставь_сюда_токен_от_BotFather":
    raise SystemExit("Укажи BOT_TOKEN в файле .env")

if BOOST_PRICE_STARS < 1:
    raise SystemExit("BOOST_PRICE_STARS должен быть больше нуля")

logging.basicConfig(level=logging.INFO)

dp = Dispatcher()

# Прототип работает только в личных сообщениях с ботом.
dp.message.filter(F.chat.type == "private")
dp.callback_query.filter(F.message.chat.type == "private")

db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row

db.executescript(
    """
    PRAGMA journal_mode = WAL;

    CREATE TABLE IF NOT EXISTS profiles (
        user_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        age INTEGER NOT NULL CHECK(age BETWEEN 14 AND 80),
        city TEXT NOT NULL,
        bio TEXT NOT NULL,
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
    """
)
db.commit()


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
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


def buttons(rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=text, callback_data=data)
                for text, data in row
            ]
            for row in rows
        ]
    )


def main_menu():
    return buttons(
        [
            [("🔎 Смотреть анкеты", "browse")],
            [("👤 Моя анкета", "my_profile")],
            [("❤️ Взаимные симпатии", "matches")],
            [("🚀 Поднять анкету", "boost")],
            [("🛡 Безопасность", "safety")],
            [("⚙️ Настройки", "settings")],
        ]
    )


def candidate_menu(target_id: int):
    return buttons(
        [
            [
                ("❤️ Нравится", f"like:{target_id}"),
                ("⏭ Пропустить", f"skip:{target_id}"),
            ],
            [
                ("🚩 Жалоба", f"report:{target_id}"),
                ("⛔ Блокировка", f"block:{target_id}"),
            ],
            [("🏠 Главное меню", "menu")],
        ]
    )


def profile_text(profile) -> str:
    return (
        f"👤 {profile['name']}, {profile['age']}\n"
        f"📍 {profile['city']}\n\n"
        f"📝 {profile['bio']}"
    )


def pair_allowed(user_id: int, target_id: int) -> bool:
    """Повторная проверка пары при каждом действии."""

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
# РЕГИСТРАЦИЯ АНКЕТЫ
# ============================================================

class ProfileForm(StatesGroup):
    name = State()
    age = State()
    city = State()
    bio = State()


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()

    profile = get_profile(message.from_user.id)

    if profile:
        await message.answer(
            "👋 С возвращением!\n\nВыбери действие:",
            reply_markup=main_menu(),
        )
        return

    await message.answer(
        "👋 Добро пожаловать!\n\n"
        "Здесь можно создать анкету и находить людей по взаимной "
        "симпатии.\n\n"
        "🔒 Пользователи 14–17 лет и взрослые разделены по возрасту.\n"
        "🧪 Сейчас это тестовая версия: не загружай личные данные, "
        "которые не хочешь использовать при тестировании.\n\n"
        "Чтобы начать, создай анкету:",
        reply_markup=buttons(
            [[("✨ Создать анкету", "profile:create")]]
        ),
    )


@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_menu())


@dp.callback_query(F.data == "profile:create")
async def create_profile(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ProfileForm.name)

    await callback.answer()
    await callback.message.answer(
        "✨ Создадим анкету!\n\n"
        "Как тебя называть? Напиши имя от 2 до 30 символов.\n\n"
        "Чтобы отменить создание анкеты, отправь /cancel."
    )


@dp.message(ProfileForm.name)
async def form_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()

    if not 2 <= len(name) <= 30:
        await message.answer("Имя должно содержать от 2 до 30 символов.")
        return

    await state.update_data(name=name)
    await state.set_state(ProfileForm.age)

    await message.answer(
        "🎂 Сколько тебе лет?\n"
        "Введи число от 14 до 80."
    )


@dp.message(ProfileForm.age)
async def form_age(message: Message, state: FSMContext):
    text = (message.text or "").strip()

    if not text.isdigit() or not 14 <= int(text) <= 80:
        await message.answer("Введи возраст числом от 14 до 80.")
        return

    await state.update_data(age=int(text))
    await state.set_state(ProfileForm.city)

    await message.answer("📍 В каком городе ты живёшь?")


@dp.message(ProfileForm.city)
async def form_city(message: Message, state: FSMContext):
    city = (message.text or "").strip()

    if not 2 <= len(city) <= 50:
        await message.answer("Название города: от 2 до 50 символов.")
        return

    await state.update_data(city=city)
    await state.set_state(ProfileForm.bio)

    await message.answer(
        "📝 Расскажи о себе и своих интересах.\n\n"
        "До 300 символов. Не указывай телефон, адрес и другие "
        "контактные данные."
    )


@dp.message(ProfileForm.bio)
async def form_bio(message: Message, state: FSMContext):
    bio = (message.text or "").strip()

    if not 5 <= len(bio) <= 300:
        await message.answer("Описание должно содержать от 5 до 300 символов.")
        return

    data = await state.get_data()
    user_id = message.from_user.id
    old_profile = get_profile(user_id)

    # При изменении возрастной группы удаляем старые симпатии,
    # чтобы они не связывали пользователей разных групп.
    if old_profile and age_group(old_profile["age"]) != age_group(data["age"]):
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
            (user_id, name, age, city, bio, active, created_at)
        VALUES (?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            name = excluded.name,
            age = excluded.age,
            city = excluded.city,
            bio = excluded.bio,
            active = 1
        """,
        (
            user_id,
            data["name"],
            data["age"],
            data["city"],
            bio,
            now_iso(),
        ),
    )
    db.commit()

    await state.clear()

    await message.answer(
        "✅ Анкета сохранена!\n\n"
        "Теперь можно посмотреть другие анкеты.",
        reply_markup=main_menu(),
    )


# ============================================================
# ПОИСК И СИМПАТИИ
# ============================================================

async def show_candidate(message: Message, viewer_id: int):
    viewer = get_profile(viewer_id)

    if not viewer:
        await message.answer(
            "Сначала создай анкету.",
            reply_markup=buttons(
                [[("✨ Создать анкету", "profile:create")]]
            ),
        )
        return

    if not viewer["active"]:
        await message.answer(
            "Твоя анкета скрыта. Включи её в настройках.",
            reply_markup=main_menu(),
        )
        return

    if viewer["age"] < 18:
        minimum_age, maximum_age = 14, 17
    else:
        minimum_age, maximum_age = 18, 80

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
        (
            viewer_id,
            minimum_age,
            maximum_age,
            viewer_id,
            viewer_id,
            viewer_id,
            int(datetime.now(timezone.utc).timestamp()),
        ),
    ).fetchone()

    if not candidate:
        await message.answer(
            "Пока новых анкет нет. Попробуй позже.",
            reply_markup=main_menu(),
        )
        return

    await message.answer(
        profile_text(candidate),
        reply_markup=candidate_menu(candidate["user_id"]),
    )


@dp.callback_query(F.data == "browse")
async def browse(callback: CallbackQuery):
    await callback.answer()
    # Важно: берём ID нажавшего пользователя, а не автора сообщения бота.
    await show_candidate(callback.message, callback.from_user.id)


@dp.callback_query(F.data.startswith("skip:"))
async def skip(callback: CallbackQuery):
    user_id = callback.from_user.id
    target_id = int(callback.data.split(":")[1])

    if not pair_allowed(user_id, target_id):
        await callback.answer("Анкета недоступна.", show_alert=True)
        return

    db.execute(
        """
        INSERT OR IGNORE INTO reactions
            (user_id, target_id, action, created_at)
        VALUES (?, ?, 'skip', ?)
        """,
        (user_id, target_id, now_iso()),
    )
    db.commit()

    await callback.answer("Анкета пропущена")
    await show_candidate(callback.message, user_id)


@dp.callback_query(F.data.startswith("like:"))
async def like(callback: CallbackQuery):
    user_id = callback.from_user.id
    target_id = int(callback.data.split(":")[1])

    if not pair_allowed(user_id, target_id):
        await callback.answer("Анкета недоступна.", show_alert=True)
        return

    db.execute(
        """
        INSERT OR IGNORE INTO reactions
            (user_id, target_id, action, created_at)
        VALUES (?, ?, 'like', ?)
        """,
        (user_id, target_id, now_iso()),
    )

    # Если ранее была нажата другая кнопка, старую реакцию не меняем.
    own_reaction = db.execute(
        """
        SELECT action FROM reactions
        WHERE user_id = ? AND target_id = ?
        """,
        (user_id, target_id),
    ).fetchone()

    if own_reaction["action"] != "like":
        db.commit()
        await callback.answer("Для этой анкеты уже выбран другой ответ.")
        return

    reciprocal = db.execute(
        """
        SELECT 1 FROM reactions
        WHERE user_id = ? AND target_id = ? AND action = 'like'
        """,
        (target_id, user_id),
    ).fetchone()

    new_match = False

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

    db.commit()
    await callback.answer("❤️ Симпатия отправлена")

    if new_match:
        target = get_profile(target_id)
        current = get_profile(user_id)

        await callback.message.answer(
            f"✨ Взаимная симпатия с пользователем {target['name']}!\n\n"
            "В этой тестовой версии контакты и переписка не открываются."
        )

        try:
            await callback.bot.send_message(
                target_id,
                f"✨ Взаимная симпатия с пользователем {current['name']}!\n\n"
                "В этой тестовой версии контакты и переписка не открываются.",
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
        """,
        (user_id, user_id, user_id),
    ).fetchall()

    # Дополнительная проверка возраста и блокировок.
    visible = [
        row for row in rows
        if pair_allowed(user_id, row["user_id"])
    ]

    await callback.answer()

    if not visible:
        await callback.message.answer(
            "❤️ Пока взаимных симпатий нет.",
            reply_markup=main_menu(),
        )
        return

    lines = ["❤️ Твои взаимные симпатии:\n"]

    for index, profile in enumerate(visible, 1):
        lines.append(
            f"{index}. {profile['name']}, {profile['age']} — {profile['city']}"
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

    if not pair_allowed(user_id, target_id):
        await callback.answer("Анкета недоступна.", show_alert=True)
        return

    db.execute(
        """
        INSERT INTO reports (reporter_id, target_id, created_at)
        VALUES (?, ?, ?)
        """,
        (user_id, target_id, now_iso()),
    )

    db.execute(
        """
        INSERT OR IGNORE INTO reactions
            (user_id, target_id, action, created_at)
        VALUES (?, ?, 'report', ?)
        """,
        (user_id, target_id, now_iso()),
    )
    db.commit()

    await callback.answer("Жалоба сохранена")
    await callback.message.answer("🚩 Жалоба сохранена для проверки.")

    await notify_admin(
        callback.bot,
        f"🚩 Жалоба на анкету\n\n"
        f"От пользователя: {user_id}\n"
        f"На пользователя: {target_id}",
    )

    await show_candidate(callback.message, user_id)


@dp.callback_query(F.data.startswith("block:"))
async def block(callback: CallbackQuery):
    user_id = callback.from_user.id
    target_id = int(callback.data.split(":")[1])

    if user_id == target_id or not get_profile(target_id):
        await callback.answer("Анкета недоступна.")
        return

    db.execute(
        """
        INSERT OR IGNORE INTO blocks (user_id, blocked_id, created_at)
        VALUES (?, ?, ?)
        """,
        (user_id, target_id, now_iso()),
    )

    user1, user2 = sorted((user_id, target_id))
    db.execute(
        "DELETE FROM matches WHERE user1 = ? AND user2 = ?",
        (user1, user2),
    )
    db.commit()

    await callback.answer("Пользователь заблокирован")
    await show_candidate(callback.message, user_id)


# ============================================================
# АНКЕТА И НАСТРОЙКИ
# ============================================================

@dp.callback_query(F.data == "my_profile")
async def my_profile(callback: CallbackQuery):
    profile = get_profile(callback.from_user.id)
    await callback.answer()

    if not profile:
        await callback.message.answer(
            "У тебя пока нет анкеты.",
            reply_markup=buttons(
                [[("✨ Создать анкету", "profile:create")]]
            ),
        )
        return

    status = "показывается" if profile["active"] else "скрыта"

    await callback.message.answer(
        f"{profile_text(profile)}\n\n"
        f"Статус: {status}",
        reply_markup=buttons(
            [
                [("✏️ Изменить анкету", "profile:create")],
                [("🏠 Главное меню", "menu")],
            ]
        ),
    )


@dp.callback_query(F.data == "settings")
async def settings(callback: CallbackQuery):
    profile = get_profile(callback.from_user.id)
    await callback.answer()

    if not profile:
        await callback.message.answer("Сначала создай анкету.")
        return

    toggle_text = "🙈 Скрыть анкету" if profile["active"] else "👁 Показать анкету"

    await callback.message.answer(
        "⚙️ Настройки анкеты",
        reply_markup=buttons(
            [
                [(toggle_text, "profile:toggle")],
                [("🗑 Удалить анкету", "profile:delete_ask")],
                [("🏠 Главное меню", "menu")],
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
        "👁 Анкета показывается." if new_value else "🙈 Анкета скрыта.",
        reply_markup=main_menu(),
    )


@dp.callback_query(F.data == "profile:delete_ask")
async def delete_ask(callback: CallbackQuery):
    await callback.answer()

    await callback.message.answer(
        "Удалить анкету, симпатии и блокировки?\n\n"
        "История платежей и жалоб может сохраняться для обработки "
        "обращений и выполнения обязательств.",
        reply_markup=buttons(
            [
                [("🗑 Да, удалить", "profile:delete_confirm")],
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
        db.execute(
            "DELETE FROM profiles WHERE user_id = ?",
            (user_id,),
        )

    await callback.answer("Анкета удалена")
    await callback.message.answer(
        "🗑 Анкета удалена.\n\n"
        "Для создания новой отправь /start."
    )


@dp.callback_query(F.data == "safety")
async def safety(callback: CallbackQuery):
    await callback.answer()

    await callback.message.answer(
        "🛡 Безопасность\n\n"
        "• Не отправляй незнакомым людям деньги и коды.\n"
        "• Не публикуй адрес, телефон и данные документов.\n"
        "• Используй жалобу и блокировку при подозрительном поведении.\n"
        "• В экстренной ситуации обращайся в местные экстренные службы.\n\n"
        "⚠️ Это прототип: заявленный возраст не проверяется.",
        reply_markup=main_menu(),
    )


@dp.callback_query(F.data == "menu")
async def menu(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("🏠 Главное меню", reply_markup=main_menu())


# ============================================================
# ОПЛАТА TELEGRAM STARS: ПОДНЯТИЕ АНКЕТЫ
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
            "Платные функции в этом прототипе доступны только 18+."
        )
        return

    if not profile["active"]:
        await callback.message.answer(
            "Сначала включи показ анкеты в настройках."
        )
        return

    if not PAYMENTS_ENABLED:
        await callback.message.answer(
            "🚀 Поднятие анкеты\n\n"
            "Платежи пока отключены: бот находится в тестовом режиме."
        )
        return

    await callback.message.answer(
        "🚀 Поднятие анкеты\n\n"
        "Твоя анкета будет иметь приоритет в поиске на 24 часа.\n"
        f"Цена: {BOOST_PRICE_STARS} Telegram Stars.\n\n"
        "Покупка разовая, без автоматического продления.",
        reply_markup=buttons(
            [[("⭐ Купить поднятие", "buy:boost")]]
        ),
    )


@dp.callback_query(F.data == "buy:boost")
async def buy_boost(callback: CallbackQuery):
    user_id = callback.from_user.id
    profile = get_profile(user_id)

    if not PAYMENTS_ENABLED:
        await callback.answer("Платежи отключены.", show_alert=True)
        return

    if not profile or profile["age"] < 18 or not profile["active"]:
        await callback.answer(
            "Покупка доступна активным анкетам 18+.",
            show_alert=True,
        )
        return

    order_id = secrets.token_hex(16)

    db.execute(
        """
        INSERT INTO orders
            (order_id, user_id, product, amount, currency, status, created_at)
        VALUES (?, ?, 'boost', ?, 'XTR', 'pending', ?)
        """,
        (order_id, user_id, BOOST_PRICE_STARS, now_iso()),
    )
    db.commit()

    await callback.answer()

    try:
        await callback.bot.send_invoice(
            chat_id=user_id,
            title="Поднятие анкеты на 24 часа",
            description=(
                "Приоритет показа анкеты в поиске на 24 часа. "
                "Разовая покупка."
            ),
            payload=order_id,
            currency="XTR",
            prices=[
                LabeledPrice(
                    label="Поднятие анкеты",
                    amount=BOOST_PRICE_STARS,
                )
            ],
        )
    except TelegramAPIError:
        logging.exception("Не удалось создать счёт")
        await callback.message.answer(
            "Не получилось создать счёт. Попробуй позже."
        )


@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    order = db.execute(
        "SELECT * FROM orders WHERE order_id = ?",
        (query.invoice_payload,),
    ).fetchone()

    profile = get_profile(query.from_user.id)

    valid = (
        PAYMENTS_ENABLED
        and order is not None
        and order["user_id"] == query.from_user.id
        and order["product"] == "boost"
        and order["status"] == "pending"
        and query.currency == "XTR"
        and query.total_amount == order["amount"]
        and profile is not None
        and profile["age"] >= 18
        and profile["active"]
    )

    if not valid:
        await query.answer(
            ok=False,
            error_message="Заказ недоступен. Создай новый счёт.",
        )
        return

    await query.answer(ok=True)


@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    payment = message.successful_payment
    order_id = payment.invoice_payload
    user_id = message.from_user.id
    charge_id = payment.telegram_payment_charge_id

    order = db.execute(
        "SELECT * FROM orders WHERE order_id = ?",
        (order_id,),
    ).fetchone()

    if not order:
        logging.error("Платёж для неизвестного заказа: %s", order_id)
        await message.answer(
            "Платёж получен, но заказ не найден. "
            "Обратись в поддержку: /paysupport."
        )
        await notify_admin(
            message.bot,
            f"⚠️ Неизвестный заказ {order_id}, пользователь {user_id}",
        )
        return

    if order["status"] == "paid":
        return

    valid = (
        order["status"] == "pending"
        and order["user_id"] == user_id
        and order["product"] == "boost"
        and payment.currency == order["currency"] == "XTR"
        and payment.total_amount == order["amount"]
    )

    if not valid:
        logging.error("Несовпадение данных заказа %s", order_id)
        await message.answer(
            "Не удалось автоматически обработать платёж. "
            "Обратись в поддержку: /paysupport."
        )
        await notify_admin(
            message.bot,
            f"⚠️ Ошибка проверки заказа {order_id}, пользователь {user_id}",
        )
        return

    profile = get_profile(user_id)

    # Заказ и услуга изменяются в одной транзакции.
    # Повторное событие не продлит услугу второй раз.
    try:
        with db:
            cursor = db.execute(
                """
                UPDATE orders
                SET status = 'paid', telegram_charge_id = ?
                WHERE order_id = ? AND status = 'pending'
                """,
                (charge_id, order_id),
            )

            if cursor.rowcount != 1:
                return

            if profile and profile["age"] >= 18:
                now_timestamp = int(datetime.now(timezone.utc).timestamp())
                start = max(now_timestamp, profile["boost_until"])
                boost_until = start + 24 * 60 * 60

                db.execute(
                    """
                    UPDATE profiles SET boost_until = ?
                    WHERE user_id = ?
                    """,
                    (boost_until, user_id),
                )
            else:
                # Платёж сохраняем, но не активируем услугу:
                # администратор должен обработать обращение.
                boost_until = None

    except sqlite3.IntegrityError:
        logging.exception("Повторный или некорректный платёж")
        return

    if boost_until is None:
        await message.answer(
            "Платёж получен, но услугу не удалось активировать. "
            "Напиши в поддержку: /paysupport."
        )
        await notify_admin(
            message.bot,
            f"⚠️ Нужна проверка оплаченного заказа {order_id}, "
            f"пользователь {user_id}",
        )
        return

    until = datetime.fromtimestamp(boost_until, timezone.utc)

    await message.answer(
        "✅ Поднятие анкеты активировано!\n\n"
        f"Действует до {until.strftime('%d.%m.%Y %H:%M')} UTC.",
        reply_markup=main_menu(),
    )


@dp.message(Command("paysupport"))
async def payment_support(message: Message):
    if ADMIN_ID:
        await message.answer(
            "💬 Опиши проблему с платежом и отправь её администратору.\n\n"
            "Не отправляй пароли, коды подтверждения и данные карты."
        )

        await notify_admin(
            message.bot,
            f"💳 Пользователь {message.from_user.id} открыл /paysupport."
        )
    else:
        await message.answer(
            "Поддержка пока не настроена. Владелец бота должен указать "
            "ADMIN_ID в файле .env."
        )


# ============================================================
# ЗАПУСК
# ============================================================

async def main():
    bot = Bot(BOT_TOKEN)

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Открыть бота"),
            BotCommand(command="cancel", description="Отменить действие"),
            BotCommand(command="paysupport", description="Помощь с оплатой"),
        ]
    )

    logging.info("Бот запущен")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
