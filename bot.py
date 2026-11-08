from __future__ import annotations

import asyncio
import html
import logging
import math
import os
import random
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlencode

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter, TelegramUnauthorizedError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

# =========================
# ЗАГРУЗКА НАСТРОЕК (.env)
# =========================
load_dotenv(override=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
DB_PATH = os.getenv("DB_PATH", "dating_bot.sqlite3").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip() or 0)

PAYMENTS_ENABLED = os.getenv("PAYMENTS_ENABLED", "1").strip() == "1"
COMMISSION_PERCENT = int(os.getenv("COMMISSION_PERCENT", "30").strip() or 30)

YOOMONEY_WALLET = os.getenv("YOOMONEY_WALLET", "410010000000000").strip()
YOOMONEY_TOKEN = os.getenv("YOOMONEY_TOKEN", "").strip().strip('"').strip("'")

# Ссылки партнёра и технической поддержки
VELVET_VPN_URL = "https://t.me/velvet?start=3SLkWOoF"
SUPPORT_URL = "https://t.me/vladofix97"
SUPPORT_USERNAME = "@vladofix97"

FREE_DAILY_LIKES = 25

# =========================
# СТИКЕРЫ И АНИМАЦИИ
# =========================
# Подставьте реальные file_id стикеров от вашего бота (узнать можно через @Stickers или @GetStickerBot)
STICKERS: Dict[str, str] = {
    # Основные экраны
    "welcome": "",        # приветствие / старт
    "empty_profile": "",  # когда нет анкеты
    # Действия с анкетами
    "like": "",           # поставили лайк
    "super_like": "",     # супер-лайк / супер-письмо
    "skip": "",           # пропустили анкету
    "match": "",          # взаимный мэтч
    "match_super": "",    # крутой мэтч с анимацией
    # Бонусы и покупки
    "daily_bonus": "",    # бонус дня
    "purchase": "",       # успешная покупка
    "vip": "",            # покупка VIP
    "coins": "",          # монеты / заработок
    # Ошибки и лимиты
    "error": "",          # ошибка / лимит
    "no_candidates": "",  # анкеты закончились
    # Навигация
    "bye": "",            # прощание / отмена
    "back": "",           # возврат назад
    "celebration": "",    # sucesso / покупка / бонус
    "gear": "",           # настройки
    "transition": "",     # переход между экранами
    "home": "",           # главное меню
    "message": "",        # новое сообщение
}


async def send_chat_action(bot: Bot, user_id: int, action: str) -> None:
    """Имитация действия бота (печатает, загружает фото и т.д.)."""
    try:
        await bot.send_chat_action(chat_id=user_id, action=action)
    except Exception:
        pass


async def send_sticker(bot: Bot, user_id: int, key: str) -> None:
    """Отправить стикер по ключу, если задан file_id."""
    sticker_id = STICKERS.get(key, "")
    if not sticker_id:
        return
    try:
        await bot.send_sticker(chat_id=user_id, sticker=sticker_id)
    except Exception:
        pass


async def send_transition_sticker(bot: Bot, user_id: int, transition_type: str = "default") -> None:
    """Отправить стикер анимации при переходе между экранами."""
    sticker_keys = {
        "default": "transition",
        "home": "home",
        "browse": "dice",
        "shop": "coins",
        "settings": "gear",
        "match": "match",
        "like": "like",
        "message": "message",
        "success": "celebration",
        "error": "error",
    }
    key = sticker_keys.get(transition_type, "transition")
    await send_sticker(bot, user_id, key)


async def dice_animation(bot: Bot, chat_id: int, duration_seconds: float = 1.5) -> str:
    """
    Показывает анимацию броска кубика редактированием временного сообщения.
    Возвращает 最終 выпавшее значение (1-6) в виде строки с эмодзи.
    """
    faces = ["🎲", "⚀", "⚁", "⚂", "⚃", "⚄", "⚅"]
    import asyncio as _asyncio

    temp_msg = await bot.send_message(chat_id=chat_id, text="🎲 Бросаем кубик…")
    try:
        steps = int(duration_seconds * 4)  # 4 кадра в секунду
        for i in range(steps):
            face = random.choice(faces[1:])  # без пустого кубика
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=temp_msg.message_id,
                text=f"🎲 Бросаем кубик… {face}",
            )
            await _asyncio.sleep(0.25)
        final_face = random.choice(faces[1:])
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=temp_msg.message_id,
            text=f"🎲 Выпало: {final_face}",
        )
        await _asyncio.sleep(0.4)
        await bot.delete_message(chat_id=chat_id, message_id=temp_msg.message_id)
    except Exception:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=temp_msg.message_id)
        except Exception:
            pass
    return final_face


async def typing_animation(bot: Bot, chat_id: int, duration_seconds: float = 1.0) -> None:
    """Имитация_print("печатает") с паузой для реалистичности."""
    try:
        await bot.send_chat_action(chat_id=chat_id, action="typing")
        await asyncio.sleep(duration_seconds)
    except Exception:
        pass


async def hearts_animation(bot: Bot, chat_id: int, count: int = 5) -> None:
    """Анимация летящих сердец при лайке/мэтче."""
    try:
        msg = await bot.send_message(chat_id=chat_id, text="❤️" * count)
        await asyncio.sleep(0.8)
        await bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    except Exception:
        pass


def celebration_line(text: str) -> str:
    """Праздничная строка с конфетти."""
    return f"🎉 {text} 🎉"


def progress_bar(current: int, maximum: int, length: int = 10) -> str:
    """Визуальный прогресс-бар."""
    if maximum <= 0:
        filled = length
    else:
        filled = max(0, min(length, int(current / maximum * length)))
    empty = length - filled
    return "🟩" * filled + "⬜️" * empty


def emoji_line(emoji: str, label: str, value: str) -> str:
    return f"{emoji} <b>{label}:</b> {value}"


def screen_header(title: str, subtitle: Optional[str] = None) -> str:
    """Красивый заголовок экрана с эмодзи."""
    lines = [f"✨ <b>{esc(title)}</b>"]
    if subtitle:
        lines.append(f"<i>{esc(subtitle)}</i>")
    return "\n".join(lines)


def screen_box(title: str, lines: List[str]) -> str:
    """Красивый блок информации с рамкой."""
    inner = "\n".join(f"   {line}" for line in lines)
    return f"┌─ <b>{esc(title)}</b>\n{inner}\n└─"


def stat_row(emoji: str, label: str, value: str, highlight: bool = False) -> str:
    """Компактная строка статистики."""
    marker = "🔹" if highlight else "  "
    return f"{marker} {emoji} <b>{label}:</b> {value}"


# Эмодзи-разделители для «оживления» текста
DIVIDERS: List[str] = [
    "✨━━━━━━━━━━━━━━━━━━━✨",
    "💫━━━━━━━━━━━━━━━━━━━💫",
    "🔥━━━━━━━━━━━━━━━━━━━🔥",
    "💘━━━━━━━━━━━━━━━━━━━💘",
    "⚡━━━━━━━━━━━━━━━━━━━⚡",
]


def random_divider() -> str:
    return random.choice(DIVIDERS)


def divider_line() -> str:
    """Единый разделитель секций."""
    return "━━━━━━━━━━━━━━━━━━━━"


def celebration_line(text: str) -> str:
    """Праздничная строка с конфетти."""
    return f"🎉 {text} 🎉"


async def dice_animation(bot: Bot, chat_id: int, duration_seconds: float = 1.5) -> str:
    """
    Показывает анимацию броска кубика редактированием временного сообщения.
    Возвращает 最終 выпавшее значение (1-6) в виде строки с эмодзи.
    """
    faces = ["🎲", "⚀", "⚁", "⚂", "⚃", "⚄", "⚅"]
    import asyncio as _asyncio

    temp_msg = await bot.send_message(chat_id=chat_id, text="🎲 Бросаем кубик…")
    try:
        steps = int(duration_seconds * 4)  # 4 кадра в секунду
        for i in range(steps):
            face = random.choice(faces[1:])  # без пустого кубика
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=temp_msg.message_id,
                text=f"🎲 Бросаем кубик… {face}",
            )
            await _asyncio.sleep(0.25)
        final_face = random.choice(faces[1:])
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=temp_msg.message_id,
            text=f"🎲 Выпало: {final_face}",
        )
        await _asyncio.sleep(0.4)
        await bot.delete_message(chat_id=chat_id, message_id=temp_msg.message_id)
    except Exception:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=temp_msg.message_id)
        except Exception:
            pass
    return final_face

# Временное хранилище ID последних сообщений бота для чистоты чата
last_bot_messages: Dict[int, List[int]] = {}
# Защита от спама уведомлениями админу при проверке оплаты (order_id -> timestamp)
pass
BOT_USERNAME: str = ""


# =========================
# СПРАВОЧНИКИ: ИНТЕРЕСЫ, ГОРОДА, ЛЕДОКОЛЫ
# =========================
def esc(value: Any) -> str:
    """Безопасное экранирование любого пользовательского текста для HTML."""
    if value is None:
        return ""
    return html.escape(str(value))


INTERESTS_MAP: Dict[str, str] = {
    "love": "❤️ Отношения",
    "friends": "🥂 Дружба и тусовки",
    "walks": "🌆 Вечерние прогулки",
    "movies": "🎬 Кино и сериалы",
    "music": "🎵 Музыка",
    "sport": "🏋️ Спорт и фитнес",
    "games": "🎮 Видеоигры",
    "travel": "✈️ Путешествия",
    "coffee": "☕️ Кофе и рестораны",
    "art": "🎨 Творчество",
}

ICEBREAKERS: List[str] = [
    "🎲 Привет! Какой твой идеальный сценарий на вечер пятницы: шумная тусовка, прогулка по ночному городу или сериал с пиццей?",
    "🎲 Привет! Если бы мы прямо сейчас могли телепортироваться в любую точку мира на выходные, куда бы полетели?",
    "🎲 Привет! Какая песня сейчас стоит у тебя на повторе в плейлисте?",
    "🎲 Привет! Кофе или чай? И какое твоё самое любимое место в нашем городе?",
    "🎲 Привет! Расскажи 2 правдивых факта о себе и 1 выдуманный — а я попробую угадать, где ложь 😊",
    "🎲 Привет! Какой фильм или сериал ты можешь пересматривать бесконечно?",
    "🎲 Привет! Что тебя больше всего цепляет в людях при первом знакомстве?",
]

CITY_ALIASES: Dict[str, str] = {
    # Россия
    "мск": "Москва",
    "москва": "Москва",
    "moscow": "Москва",
    "спб": "Санкт-Петербург",
    "питер": "Санкт-Петербург",
    "санкт петербург": "Санкт-Петербург",
    "санкт-петербург": "Санкт-Петербург",
    "петербург": "Санкт-Петербург",
    "екб": "Екатеринбург",
    "екатеринбург": "Екатеринбург",
    "нск": "Новосибирск",
    "новосиб": "Новосибирск",
    "новосибирск": "Новосибирск",
    "крд": "Краснодар",
    "краснодар": "Краснодар",
    "нн": "Нижний Новгород",
    "нижний новгород": "Нижний Новгород",
    "казань": "Казань",
    "кзн": "Казань",
    "ростов": "Ростов-на-Дону",
    "ростов на дону": "Ростов-на-Дону",
    "ростов-на-дону": "Ростов-на-Дону",
    "рнд": "Ростов-на-Дону",
    "самара": "Самара",
    "челябинск": "Челябинск",
    "челяба": "Челябинск",
    "уфа": "Уфа",
    "воронеж": "Воронеж",
    "врн": "Воронеж",
    "пермь": "Пермь",
    "волгоград": "Волгоград",
    "красноярск": "Красноярск",
    "омск": "Омск",
    "ения": "Енисейск",
    "саратов": "Саратов",
    "тула": "Тула",
    "рязань": "Рязань",
    " vladimir": "Владимир",
    "владимир": "Владимир",
    "орёл": "Орёл",
    "орел": "Орёл",
    "курск": "Курск",
    "белгород": "Белгород",
    "брянск": "Брянск",
    "смоленск": "Смоленск",
    "тверь": "Тверь",
    "ярославль": "Ярославль",
    "иваново": "Иваново",
    "кострома": "Кострома",
    "владивосток": "Владивосток",
    "хабаровск": "Хабаровск",
    "сочи": "Сочи",
    "ялта": "Ялта",
    "севастополь": "Севастополь",
    "розы": "Ростов-на-Дону",
    # СНГ
    "минск": "Минск",
    "алматы": "Алматы",
    "астана": "Астана",
    "бишкек": "Бишкек",
    "ташкент": "Ташкент",
    "киев": "Киев",
    "харьков": "Харьков",
    "одесса": "Одесса",
}


COUNTRY_ALIASES: Dict[str, str] = {
    "россия": "Россия",
    "rf": "Россия",
    "russia": "Россия",
    "росси": "Россия",
    "беларусь": "Беларусь",
    "belarus": "Беларусь",
    "by": "Беларусь",
    "кз": "Казахстан",
    "казахстан": "Казахстан",
    "kazakhstan": "Казахстан",
    "украина": "Украина",
    "ukraine": "Украина",
    "ua": "Украина",
}

RUSSIAN_CITIES: List[str] = [
    "Москва", "Санкт-Петербург", "Екатеринбург", "Новосибирск", "Казань",
    "Нижний Новгород", "Челябинск", "Самара", "Омск", "Ростов-на-Дону",
    "Уфа", "Красноярск", "Пермь", "Волгоград", "Краснодар", "Владивосток",
    "Хабаровск", "Сочи", "Ярославль", "Иваново", "Владимир",
    "Тула", "Рязань", "Тверь", "Кострома", "Орёл", "Курск", "Белгород",
    "Брянск", "Смоленск", "Самара", "Саратов", "Ульяновск", "Пенза",
    "Тамбов", "Липецк", "Воронеж", "Курган", "Тюмень", "Сургут",
    "Новороссийск", "Архангельск", "Мурманск", "Псков", "Великий Новгород",
    "Череповец", "Вологда", "Йошкар-Ола", "Саранск", "Чебоксары",
    "Ижевск", "Пермь", "Оренбург", "Нижневартовск", "Набережные Челны",
]


def normalize_city(raw_city: str) -> str:
    """Приводитpopularные сокращения городов к единому стандарту."""
    cleaned = " ".join(raw_city.strip().split())
    low = cleaned.lower()
    if low in CITY_ALIASES:
        return CITY_ALIASES[low]
    return cleaned[:1].upper() + cleaned[1:] if cleaned else "Не указан"


def normalize_country(raw_country: str) -> str:
    """Нормализует название страны."""
    cleaned = " ".join(raw_country.strip().split())
    low = cleaned.lower()
    if low in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[low]
    return cleaned[:1].upper() + cleaned[1:] if cleaned else "Россия"


def format_gender_label(gender: str) -> str:
    return "👨 Парень" if gender == "M" else "👩 Девушка"


def format_looking_for_label(looking_for: str) -> str:
    if looking_for == "F":
        return "👩 Девушек"
    if looking_for == "M":
        return "👨 Парней"
    return "👥 Всех"


def parse_interests(raw: str) -> Set[str]:
    if not raw:
        return set()
    return {k.strip() for k in raw.split(",") if k.strip() in INTERESTS_MAP}


def format_interests_list(raw: str) -> str:
    keys = parse_interests(raw)
    if not keys:
        return "Не указаны"
    return ", ".join(INTERESTS_MAP[k] for k in keys)


def calc_compatibility(viewer: Optional[sqlite3.Row], target: sqlite3.Row) -> Tuple[int, List[str]]:
    """Считает процент совместимости и список общих интересов."""
    if not viewer:
        return 0, []
    v_int = parse_interests(str(viewer["interests"] or ""))
    t_int = parse_interests(str(target["interests"] or ""))
    common = [INTERESTS_MAP[k] for k in (v_int & t_int)]

    score = 55
    if str(viewer["city"]).lower() == str(target["city"]).lower():
        score += 15
    age_diff = abs(int(viewer["age"]) - int(target["age"]))
    if age_diff <= 2:
        score += 12
    elif age_diff <= 5:
        score += 7

    score += min(17, len(common) * 6)
    return min(99, score), common


def format_online_status(last_active: int) -> str:
    if last_active <= 0:
        return "🕒 Недавно"
    diff = int(time.time()) - last_active
    if diff < 900:
        return "🟢 Онлайн"
    if diff < 3600:
        return f"🟢 Был(а) {max(1, diff // 60)} мин. назад"
    if diff < 86400:
        return f"🕒 Был(а) {diff // 3600} ч. назад"
    return "🕒 Был(а) недавно"


# =========================
# ТАРИФЫ И УСЛУГИ МАГАЗИНА
# =========================
@dataclass(frozen=True)
class Product:
    key: str
    title: str
    description: str
    base_price_rub: int


PRODUCTS: Dict[str, Product] = {
    # VIP подписки
    "vip_3": Product(
        key="vip_3",
        title="👑 VIP на 3 дня (Пробный)",
        description="• Просмотр всех, кто лайкнул\n• Безлимитные лайки\n• +2 💌 Супер-письма\n• Значок VIP в анкете",
        base_price_rub=149,
    ),
    "vip_7": Product(
        key="vip_7",
        title="👑 VIP на 7 дней",
        description="• Просмотр всех, кто лайкнул\n• Безлимитные лайки\n• +3 💌 Супер-письма\n• Значок VIP в анкете\n• Приоритет в поиске",
        base_price_rub=299,
    ),
    "vip_30": Product(
        key="vip_30",
        title="👑 VIP на 30 дней (ХИТ)",
        description="• Все привилегии VIP на месяц\n• +10 💌 Супер-писем\n• Максимальный приоритет показа\n• Значок VIP\n• Безлимитные лайки",
        base_price_rub=799,
    ),
    "vip_90": Product(
        key="vip_90",
        title="👑 VIP на 90 дней (МАКСИМУМ)",
        description="• Все привилегии на 3 месяца\n• +25 💌 Супер-писем\n• Максимальный приоритет\n• Скрытый режим просмотра\n• Экономия 25%",
        base_price_rub=1999,
    ),
    # Покупка функций по отдельности
    "likes_unlimited": Product(
        key="likes_unlimited",
        title="❤️ Безлимитные лайки на 7 дней",
        description="• Ставь лайки без ограничений неделю\n• Идеально для активного поиска",
        base_price_rub=199,
    ),
    "likes_pack_50": Product(
        key="likes_pack_50",
        title="❤️ Пакет 50 дополнительных лайков",
        description="• +50 лайков сверх дневного лимита\n• Не сгорают, используй когда нужно",
        base_price_rub=99,
    ),
    "boost_24": Product(
        key="boost_24",
        title="🚀 Буст в ТОП на 24 часа",
        description="• Твоя анкета показывается первой всем пользователям\n• +300% больше просмотров\n• 24 часа максимальной видимости",
        base_price_rub=249,
    ),
    "boost_72": Product(
        key="boost_72",
        title="🚀 Буст в ТОП на 72 часа",
        description="• Твоя анкета в ТОП 3 дня\n• +500% больше просмотров\n• Максимальная активность",
        base_price_rub=599,
    ),
    "super_1": Product(
        key="super_1",
        title="💌 1 Супер-письмо",
        description="• Напиши любому человеку сразу без мэтча\n• Мгновенная доставка с кнопкой ответа",
        base_price_rub=49,
    ),
    "super_5": Product(
        key="super_5",
        title="💌 Пакет 5 Супер-писем",
        description="• 5 сообщений любому человеку без мэтча\n• Выгода 18%",
        base_price_rub=199,
    ),
    "super_15": Product(
        key="super_15",
        title="💌 Пакет 15 Супер-писем",
        description="• 15 сообщений любому человеку без мэтча\n• Выгода 40%",
        base_price_rub=449,
    ),
    "reset_skips": Product(
        key="reset_skips",
        title="🔄 Сброс пропущенных анкет",
        description="• Очисти историю «Дальше 👎»\n• Снова увидь всех пропущенных пользователей",
        base_price_rub=129,
    ),
    "see_likes": Product(
        key="see_likes",
        title="👀 Посмотреть, кто лайкнул (3 дня)",
        description="• Доступ к списку входящих лайков\n• Прямой просмотр без VIP\n• 3 дня доступа",
        base_price_rub=149,
    ),
    "anonymous_view": Product(
        key="anonymous_view",
        title="👤 Анонимный просмотр на 7 дней",
        description="• Сматривай анкеты без следа\n• Пользователи не увидят, что ты заходил\n• Полная приватность",
        base_price_rub=179,
    ),
}


def calc_price_with_commission(base_price: int) -> Tuple[int, int, int]:
    commission = math.ceil(base_price * (COMMISSION_PERCENT / 100.0))
    total = base_price + commission
    return base_price, commission, total


def create_yoomoney_pay_link(order_id: str, total_rub: int, description: str) -> str:
    params = {
        "receiver": YOOMONEY_WALLET,
        "quickpay-form": "button",
        "paymentType": "AC",
        "sum": str(total_rub),
        "label": order_id,
        "targets": description[:120],
    }
    return f"https://yoomoney.ru/quickpay/confirm?{urlencode(params)}"


def is_yoomoney_token_configured() -> bool:
    if not YOOMONEY_TOKEN:
        return False
    placeholders = ("ваш_токен", "your_token", "insert", "placeholder", "токен_юmoney", "xxx")
    low = YOOMONEY_TOKEN.lower()
    if any(p in low for p in placeholders) or len(YOOMONEY_TOKEN) < 20:
        return False
    return True


def check_yoomoney_payment(order_id: str) -> dict:
    """
    Проверяет статус платежа в ЮMoney.
    Возвращает dict с полями: success (bool), label (str), amount (float), status (str).
    """
    if not YOOMONEY_TOKEN:
        return {"success": False, "error": "YOOMONEY_TOKEN не задан"}

    try:
        import aiohttp
        # Историческая операция по метке (order_id)
        url = "https://yoomoney.ru/api/operation-history"
        params = {"label": order_id, "records": 10}

        # Синхронная проверка через requests будет в отдельном потоке,
        # здесь возвращаем заглушку для асинхронного вызова
        return {"success": False, "error": "use_async_check"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def async_check_yoomoney_payment(order_id: str) -> dict:
    """Асинхронная проверка платежа ЮMoney через API."""
    if not YOOMONEY_TOKEN:
        return {"success": False, "error": "YOOMONEY_TOKEN не задан"}

    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://yoomoney.ru/api/operation-history",
                params={"label": order_id, "records": 10},
                headers={"Authorization": f"Bearer {YOOMONEY_TOKEN}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    operations = data.get("operations", [])
                    for op in operations:
                        if op.get("status") == "success" and op.get("direction") == "in":
                            return {
                                "success": True,
                                "status": "success",
                                "amount": float(op.get("amount", 0)),
                                "datetime": op.get("datetime", ""),
                                "title": op.get("title", ""),
                            }
                    return {"success": False, "status": "pending"}
                return {"success": False, "error": f"HTTP {resp.status}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =========================
# БАЗА ДАННЫХ SQLite
# =========================
class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        # Одно постоянное соединение с WAL + блокировка на запись (сериализация транзакций)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._init_db()

    @contextmanager
    def _session(self):
        """Контекстный менеджер с автоматической блокировкой и коммитом/откатом."""
        with self._lock:
            conn = self._conn
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _init_db(self) -> None:
        with self._session() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    name TEXT NOT NULL,
                    gender TEXT NOT NULL DEFAULT 'M',
                    looking_for TEXT NOT NULL DEFAULT 'A',
                    age INTEGER NOT NULL,
                    min_pref_age INTEGER NOT NULL DEFAULT 18,
                    max_pref_age INTEGER NOT NULL DEFAULT 45,
                    city TEXT NOT NULL,
                    bio TEXT NOT NULL,
                    interests TEXT NOT NULL DEFAULT '',
                    photo_id TEXT,
                    media_type TEXT NOT NULL DEFAULT 'photo',
                    verified INTEGER NOT NULL DEFAULT 0,
                    banned INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    boost_until INTEGER NOT NULL DEFAULT 0,
                    vip_until INTEGER NOT NULL DEFAULT 0,
                    superlikes INTEGER NOT NULL DEFAULT 1,
                    referred_by INTEGER NOT NULL DEFAULT 0,
                    ref_count INTEGER NOT NULL DEFAULT 0,
                    last_active INTEGER NOT NULL DEFAULT 0,
                    last_daily_bonus INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    country TEXT NOT NULL DEFAULT 'Россия'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_referrals (
                    user_id INTEGER PRIMARY KEY,
                    inviter_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reactions (
                    user_id INTEGER NOT NULL,
                    target_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, target_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS matches (
                    user1 INTEGER NOT NULL,
                    user2 INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (user1, user2)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS blocks (
                    user_id INTEGER NOT NULL,
                    blocked_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, blocked_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reporter_id INTEGER NOT NULL,
                    target_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    product TEXT NOT NULL,
                    base_amount INTEGER NOT NULL DEFAULT 0,
                    commission INTEGER NOT NULL DEFAULT 0,
                    amount INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    status TEXT NOT NULL,
                    telegram_charge_id TEXT,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    author_name TEXT NOT NULL,
                    rating INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender_id INTEGER NOT NULL,
                    receiver_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    media_type TEXT NOT NULL DEFAULT 'text',
                    media_id TEXT,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS photo_gallery (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    photo_id TEXT NOT NULL,
                    media_type TEXT NOT NULL DEFAULT 'photo',
                    position INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS icebreakers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'general',
                    is_active INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            self._migrate(conn)
            self._create_indexes(conn)
            self.init_default_icebreakers()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        p_cols = [r["name"] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()]
        migrations = [
            ("gender", "ALTER TABLE profiles ADD COLUMN gender TEXT NOT NULL DEFAULT 'M'"),
            ("looking_for", "ALTER TABLE profiles ADD COLUMN looking_for TEXT NOT NULL DEFAULT 'A'"),
            ("min_pref_age", "ALTER TABLE profiles ADD COLUMN min_pref_age INTEGER NOT NULL DEFAULT 18"),
            ("max_pref_age", "ALTER TABLE profiles ADD COLUMN max_pref_age INTEGER NOT NULL DEFAULT 45"),
            ("interests", "ALTER TABLE profiles ADD COLUMN interests TEXT NOT NULL DEFAULT ''"),
            ("media_type", "ALTER TABLE profiles ADD COLUMN media_type TEXT NOT NULL DEFAULT 'photo'"),
            ("verified", "ALTER TABLE profiles ADD COLUMN verified INTEGER NOT NULL DEFAULT 0"),
            ("banned", "ALTER TABLE profiles ADD COLUMN banned INTEGER NOT NULL DEFAULT 0"),
            ("vip_until", "ALTER TABLE profiles ADD COLUMN vip_until INTEGER NOT NULL DEFAULT 0"),
            ("superlikes", "ALTER TABLE profiles ADD COLUMN superlikes INTEGER NOT NULL DEFAULT 1"),
            ("referred_by", "ALTER TABLE profiles ADD COLUMN referred_by INTEGER NOT NULL DEFAULT 0"),
            ("ref_count", "ALTER TABLE profiles ADD COLUMN ref_count INTEGER NOT NULL DEFAULT 0"),
            ("last_active", "ALTER TABLE profiles ADD COLUMN last_active INTEGER NOT NULL DEFAULT 0"),
            ("last_daily_bonus", "ALTER TABLE profiles ADD COLUMN last_daily_bonus INTEGER NOT NULL DEFAULT 0"),
            ("country", "ALTER TABLE profiles ADD COLUMN country TEXT NOT NULL DEFAULT 'Россия'"),
        ]
        for col_name, sql in migrations:
            if col_name not in p_cols:
                conn.execute(sql)

        o_cols = [r["name"] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
        if "base_amount" not in o_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN base_amount INTEGER NOT NULL DEFAULT 0")
        if "commission" not in o_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN commission INTEGER NOT NULL DEFAULT 0")

    def _create_indexes(self, conn: sqlite3.Connection) -> None:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_profiles_search ON profiles(active, banned, age, gender, city);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reactions_user ON reactions(user_id, action);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reactions_target ON reactions(target_id, action);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_blocks_user ON blocks(user_id, blocked_id);")

    # =========================
    # ВАЛИДАЦИЯ ВХОДНЫХ ДАННЫХ
    # =========================
    @staticmethod
    def validate_name(name: str) -> bool:
        if not name or not (2 <= len(name) <= 32):
            return False
        # Только буквы, пробелы, дефис, апостроф
        return bool(re.fullmatch(r"[А-Яа-яЁёA-Za-z\s\-']+", name))

    @staticmethod
    def validate_age(age: int) -> bool:
        return 14 <= age <= 80

    @staticmethod
    def validate_city(city: str) -> bool:
        if not city or not (2 <= len(city) <= 40):
            return False
        return bool(re.fullmatch(r"[А-Яа-яЁёA-Za-z\s\-']+", city))

    @staticmethod
    def validate_bio(bio: str) -> bool:
        return 10 <= len(bio) <= 500

    @staticmethod
    def validate_interests(interests: str) -> bool:
        if not interests:
            return True
        keys = [k.strip() for k in interests.split(",") if k.strip()]
        return len(keys) <= 5 and all(k in INTERESTS_MAP for k in keys)

    # =========================
    # ПРОФИЛЬ
    # =========================
    def get_profile(self, user_id: int) -> Optional[sqlite3.Row]:
        with self._session() as conn:
            return conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()

    def touch_user(self, user_id: int, username: Optional[str] = None) -> None:
        now = int(time.time())
        with self._session() as conn:
            if username is not None:
                conn.execute("UPDATE profiles SET last_active = ?, username = ? WHERE user_id = ?", (now, username, user_id))
            else:
                conn.execute("UPDATE profiles SET last_active = ? WHERE user_id = ?", (now, user_id))

    def set_banned(self, user_id: int, banned: bool) -> None:
        with self._session() as conn:
            conn.execute(
                "UPDATE profiles SET banned = ?, active = ? WHERE user_id = ?",
                (1 if banned else 0, 0 if banned else 1, user_id),
            )

    def set_verified(self, user_id: int, verified: bool = True) -> bool:
        p = self.get_profile(user_id)
        if not p:
            return False
        was_verified = int(p["verified"]) == 1
        with self._session() as conn:
            if not was_verified and verified:
                conn.execute(
                    "UPDATE profiles SET verified = 1, superlikes = superlikes + 1 WHERE user_id = ?",
                    (user_id,),
                )
            else:
                conn.execute("UPDATE profiles SET verified = ? WHERE user_id = ?", (1 if verified else 0, user_id))
        return not was_verified and verified

    def claim_daily_bonus(self, user_id: int) -> Tuple[bool, int]:
        """Возвращает (успешно_ли, сколько_часов_осталось_ждать)."""
        p = self.get_profile(user_id)
        if not p:
            return False, 0
        now = int(time.time())
        last = int(p["last_daily_bonus"])
        if now - last < 86400:
            hours_left = max(1, (86400 - (now - last)) // 3600)
            return False, hours_left

        cur_boost = max(int(p["boost_until"]), now)
        new_boost = cur_boost + 2 * 3600
        with self._session() as conn:
            conn.execute(
                "UPDATE profiles SET last_daily_bonus = ?, boost_until = ? WHERE user_id = ?",
                (now, new_boost, user_id),
            )
        return True, 0

    def set_pending_referral(self, user_id: int, inviter_id: int) -> None:
        if user_id == inviter_id or inviter_id <= 0:
            return
        if self.get_profile(user_id):
            return
        with self._session() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO pending_referrals (user_id, inviter_id, created_at) VALUES (?, ?, ?)",
                (user_id, inviter_id, int(time.time())),
            )

    def pop_pending_referral(self, user_id: int) -> int:
        with self._session() as conn:
            row = conn.execute(
                "SELECT inviter_id FROM pending_referrals WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row:
                conn.execute("DELETE FROM pending_referrals WHERE user_id = ?", (user_id,))
                return int(row["inviter_id"])
            return 0

    def reward_referral_signup(self, inviter_id: int, new_user_id: int) -> Tuple[int, bool]:
        now = int(time.time())
        with self._session() as conn:
            inviter = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (inviter_id,)).fetchone()
            if not inviter:
                return 0, False

            conn.execute(
                "UPDATE profiles SET referred_by = ?, superlikes = superlikes + 1 WHERE user_id = ?",
                (inviter_id, new_user_id),
            )

            new_ref_count = int(inviter["ref_count"]) + 1
            cur_boost = max(int(inviter["boost_until"]), now)
            new_boost = cur_boost + 6 * 3600

            got_milestone_vip = (new_ref_count % 3 == 0)
            cur_vip = max(int(inviter["vip_until"]), now)
            new_vip = cur_vip + (86400 if got_milestone_vip else 0)
            added_super = 2 if got_milestone_vip else 1

            conn.execute(
                """
                UPDATE profiles
                SET ref_count = ?, boost_until = ?, vip_until = ?, superlikes = superlikes + ?
                WHERE user_id = ?
                """,
                (new_ref_count, new_boost, new_vip, added_super, inviter_id),
            )
            return new_ref_count, got_milestone_vip

    def reward_inviter_on_purchase(self, buyer_id: int) -> Optional[int]:
        p = self.get_profile(buyer_id)
        if not p or int(p["referred_by"]) <= 0:
            return None
        inviter_id = int(p["referred_by"])
        inviter = self.get_profile(inviter_id)
        if not inviter:
            return None
        now = int(time.time())
        cur_vip = max(int(inviter["vip_until"]), now)
        with self._session() as conn:
            conn.execute(
                "UPDATE profiles SET vip_until = ?, superlikes = superlikes + 2 WHERE user_id = ?",
                (cur_vip + 86400, inviter_id),
            )
        return inviter_id

    def get_top_referrers(self, limit: int = 10) -> List[sqlite3.Row]:
        with self._session() as conn:
            return conn.execute(
                "SELECT name, age, city, ref_count, vip_until FROM profiles WHERE ref_count > 0 ORDER BY ref_count DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def upsert_profile(
        self,
        user_id: int,
        username: Optional[str],
        name: str,
        gender: str,
        looking_for: str,
        age: int,
        city: str,
        bio: str,
        interests: str,
        photo_id: Optional[str],
        media_type: str = "photo",
        country: str = "Россия",
    ) -> bool:
        now = int(time.time())
        norm_city = normalize_city(city)
        norm_country = normalize_country(country)
        if age < 18:
            min_a, max_a = 14, 17
        else:
            min_a = max(18, age - 5)
            max_a = min(80, age + 8)

        existing = self.get_profile(user_id)
        with self._session() as conn:
            if existing:
                conn.execute(
                    """
                    UPDATE profiles
                    SET username = ?, name = ?, gender = ?, looking_for = ?, age = ?,
                        min_pref_age = ?, max_pref_age = ?, city = ?, bio = ?, interests = ?,
                        photo_id = ?, media_type = ?, active = 1, last_active = ?, country = ?
                    WHERE user_id = ?
                    """,
                    (
                        username,
                        name,
                        gender,
                        looking_for,
                        age,
                        min_a,
                        max_a,
                        norm_city,
                        bio,
                        interests,
                        photo_id,
                        media_type,
                        now,
                        norm_country,
                        user_id,
                    ),
                )
                return False
            else:
                conn.execute(
                    """
                    INSERT INTO profiles (
                        user_id, username, name, gender, looking_for, age, min_pref_age, max_pref_age,
                        city, bio, interests, photo_id, media_type, verified, banned,
                        active, boost_until, vip_until, superlikes, referred_by, ref_count, last_active, created_at, country
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 1, 0, 0, 1, 0, 0, ?, ?, ?)
                    """,
                    (
                        user_id,
                        username,
                        name,
                        gender,
                        looking_for,
                        age,
                        min_a,
                        max_a,
                        norm_city,
                        bio,
                        interests,
                        photo_id,
                        media_type,
                        now,
                        now,
                        norm_country,
                    ),
                )
                return True

    def update_media(self, user_id: int, photo_id: Optional[str], media_type: str = "photo") -> None:
        with self._session() as conn:
            conn.execute(
                "UPDATE profiles SET photo_id = ?, media_type = ? WHERE user_id = ?",
                (photo_id, media_type, user_id),
            )

    def update_bio(self, user_id: int, bio: str) -> None:
        with self._session() as conn:
            conn.execute("UPDATE profiles SET bio = ? WHERE user_id = ?", (bio, user_id))

    def update_city(self, user_id: int, city: str) -> None:
        norm_city = normalize_city(city)
        with self._session() as conn:
            conn.execute("UPDATE profiles SET city = ? WHERE user_id = ?", (norm_city, user_id))

    def update_country(self, user_id: int, country: str) -> None:
        norm_country = normalize_country(country)
        with self._session() as conn:
            conn.execute("UPDATE profiles SET country = ? WHERE user_id = ?", (norm_country, user_id))

    def update_preferences(self, user_id: int, gender: str, looking_for: str) -> None:
        with self._session() as conn:
            conn.execute(
                "UPDATE profiles SET gender = ?, looking_for = ? WHERE user_id = ?",
                (gender, looking_for, user_id),
            )

    def update_age_range(self, user_id: int, min_age: int, max_age: int) -> None:
        with self._session() as conn:
            conn.execute(
                "UPDATE profiles SET min_pref_age = ?, max_pref_age = ? WHERE user_id = ?",
                (min_age, max_age, user_id),
            )

    def update_interests(self, user_id: int, interests: str) -> None:
        with self._session() as conn:
            conn.execute("UPDATE profiles SET interests = ? WHERE user_id = ?", (interests, user_id))

    def set_active(self, user_id: int, active: bool) -> None:
        with self._session() as conn:
            conn.execute("UPDATE profiles SET active = ? WHERE user_id = ?", (1 if active else 0, user_id))

    def is_vip(self, user_id: int) -> bool:
        p = self.get_profile(user_id)
        return bool(p and int(p["vip_until"]) > int(time.time()))

    def add_boost(self, user_id: int, hours: int) -> int:
        now = int(time.time())
        p = self.get_profile(user_id)
        current_until = int(p["boost_until"]) if p else 0
        new_until = max(now, current_until) + hours * 3600
        with self._session() as conn:
            conn.execute("UPDATE profiles SET boost_until = ? WHERE user_id = ?", (new_until, user_id))
        return new_until

    def add_vip(self, user_id: int, days: int, bonus_superlikes: int = 0) -> int:
        now = int(time.time())
        p = self.get_profile(user_id)
        current_until = int(p["vip_until"]) if p else 0
        new_until = max(now, current_until) + days * 86400
        with self._session() as conn:
            conn.execute(
                "UPDATE profiles SET vip_until = ?, superlikes = superlikes + ? WHERE user_id = ?",
                (new_until, bonus_superlikes, user_id),
            )
        return new_until

    def add_superlikes(self, user_id: int, count: int) -> None:
        with self._session() as conn:
            conn.execute("UPDATE profiles SET superlikes = superlikes + ? WHERE user_id = ?", (count, user_id))

    def use_superlike(self, user_id: int) -> bool:
        p = self.get_profile(user_id)
        if not p or int(p["superlikes"]) <= 0:
            return False
        with self._session() as conn:
            conn.execute("UPDATE profiles SET superlikes = superlikes - 1 WHERE user_id = ?", (user_id,))
        return True

    def reset_user_skips(self, user_id: int) -> int:
        with self._session() as conn:
            cur = conn.execute("DELETE FROM reactions WHERE user_id = ? AND action = 'skip'", (user_id,))
            return cur.rowcount

    def count_today_likes(self, user_id: int) -> int:
        since = int(time.time()) - 86400
        with self._session() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM reactions WHERE user_id = ? AND action = 'like' AND created_at >= ?",
                (user_id, since),
            ).fetchone()
            return int(row["cnt"]) if row else 0

    def record_reaction(self, user_id: int, target_id: int, action: str) -> None:
        with self._session() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO reactions (user_id, target_id, action, created_at) VALUES (?, ?, ?, ?)",
                (user_id, target_id, action, int(time.time())),
            )

    def has_liked(self, user_id: int, target_id: int) -> bool:
        with self._session() as conn:
            row = conn.execute(
                "SELECT 1 FROM reactions WHERE user_id = ? AND target_id = ? AND action = 'like'",
                (user_id, target_id),
            ).fetchone()
            return row is not None

    def atomic_register_like(self, user_id: int, target_id: int) -> bool:
        """
        Атомарно записывает лайк и создаёт мэтч, если есть обратная симпатия.
        Возвращает True, если мэтч создан. Защита от raced-conditions через BEGIN IMMEDIATE.
        """
        now = int(time.time())
        with self._session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR REPLACE INTO reactions (user_id, target_id, action, created_at) VALUES (?, ?, 'like', ?)",
                (user_id, target_id, now),
            )
            row = conn.execute(
                "SELECT 1 FROM reactions WHERE user_id = ? AND target_id = ? AND action = 'like'",
                (target_id, user_id),
            ).fetchone()
            if row:
                u1, u2 = (user_id, target_id) if user_id < target_id else (target_id, user_id)
                conn.execute(
                    "INSERT OR IGNORE INTO matches (user1, user2, created_at) VALUES (?, ?, ?)",
                    (u1, u2, now),
                )
                return True
            return False

    def create_match(self, a: int, b: int) -> None:
        u1, u2 = (a, b) if a < b else (b, a)
        with self._session() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO matches (user1, user2, created_at) VALUES (?, ?, ?)",
                (u1, u2, int(time.time())),
            )

    def list_matches(self, user_id: int, limit: int = 20) -> List[sqlite3.Row]:
        me = self.get_profile(user_id)
        if not me:
            return []
        my_age = int(me["age"])
        min_age, max_age = (14, 17) if my_age < 18 else (18, 100)

        with self._session() as conn:
            rows = conn.execute(
                """
                SELECT
                    CASE WHEN user1 = ? THEN user2 ELSE user1 END AS other_id,
                    created_at
                FROM matches
                WHERE user1 = ? OR user2 = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, user_id, user_id, limit),
            ).fetchall()

            result: List[sqlite3.Row] = []
            for r in rows:
                other_id = int(r["other_id"])
                if self.is_blocked_between(user_id, other_id):
                    continue
                p = self.get_profile(other_id)
                if p and int(p["active"]) == 1 and int(p["banned"]) == 0 and min_age <= int(p["age"]) <= max_age:
                    result.append(p)
            return result

    def get_who_liked_me(self, user_id: int) -> List[sqlite3.Row]:
        me = self.get_profile(user_id)
        if not me:
            return []
        my_age = int(me["age"])
        min_age, max_age = (14, 17) if my_age < 18 else (18, 100)

        with self._session() as conn:
            return conn.execute(
                """
                SELECT p.* FROM profiles p
                JOIN reactions r ON p.user_id = r.user_id
                WHERE r.target_id = ? AND r.action = 'like'
                  AND p.active = 1 AND p.banned = 0
                  AND p.age BETWEEN ? AND ?
                  AND p.user_id NOT IN (SELECT target_id FROM reactions WHERE user_id = ?)
                  AND p.user_id NOT IN (SELECT blocked_id FROM blocks WHERE user_id = ?)
                  AND p.user_id NOT IN (SELECT user_id FROM blocks WHERE blocked_id = ?)
                ORDER BY r.created_at DESC
                """,
                (user_id, min_age, max_age, user_id, user_id, user_id),
            ).fetchall()

    def block_user(self, user_id: int, blocked_id: int) -> None:
        with self._session() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO blocks (user_id, blocked_id, created_at) VALUES (?, ?, ?)",
                (user_id, blocked_id, int(time.time())),
            )

    def is_blocked_between(self, a: int, b: int) -> bool:
        with self._session() as conn:
            row = conn.execute(
                "SELECT 1 FROM blocks WHERE (user_id = ? AND blocked_id = ?) OR (user_id = ? AND blocked_id = ?)",
                (a, b, b, a),
            ).fetchone()
            return row is not None

    def report_user(self, reporter_id: int, target_id: int) -> None:
        with self._session() as conn:
            conn.execute(
                "INSERT INTO reports (reporter_id, target_id, created_at) VALUES (?, ?, ?)",
                (reporter_id, target_id, int(time.time())),
            )

    def log_chat_message(self, sender_id: int, receiver_id: int, text: str, media_type: str = "text", media_id: Optional[str] = None) -> None:
        with self._session() as conn:
            conn.execute(
                "INSERT INTO chat_messages (sender_id, receiver_id, text, media_type, media_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (sender_id, receiver_id, text, media_type, media_id, int(time.time())),
            )

    def get_chat_history(self, user1: int, user2: int, limit: int = 50) -> List[sqlite3.Row]:
        with self._session() as conn:
            return conn.execute(
                """
                SELECT * FROM chat_messages
                WHERE (sender_id = ? AND receiver_id = ?) OR (sender_id = ? AND receiver_id = ?)
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user1, user2, user2, user1, limit),
            ).fetchall()

    def get_chat_partners(self, user_id: int) -> List[sqlite3.Row]:
        """Список пользователей, с которыми есть переписка."""
        with self._session() as conn:
            return conn.execute(
                """
                SELECT DISTINCT
                    CASE WHEN sender_id = ? THEN receiver_id ELSE sender_id END AS partner_id,
                    MAX(created_at) as last_msg
                FROM chat_messages
                WHERE sender_id = ? OR receiver_id = ?
                GROUP BY partner_id
                ORDER BY last_msg DESC
                """,
                (user_id, user_id, user_id),
            ).fetchall()

    def add_photo_to_gallery(self, user_id: int, photo_id: str, media_type: str = "photo", position: int = 0) -> int:
        with self._session() as conn:
            cur = conn.execute(
                "INSERT INTO photo_gallery (user_id, photo_id, media_type, position, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, photo_id, media_type, position, int(time.time())),
            )
            return cur.lastrowid

    def get_photo_gallery(self, user_id: int) -> List[sqlite3.Row]:
        with self._session() as conn:
            return conn.execute(
                "SELECT * FROM photo_gallery WHERE user_id = ? ORDER BY position ASC, created_at ASC",
                (user_id,),
            ).fetchall()

    def delete_photo_from_gallery(self, photo_id: int, user_id: int) -> bool:
        with self._session() as conn:
            cur = conn.execute(
                "DELETE FROM photo_gallery WHERE id = ? AND user_id = ?",
                (photo_id, user_id),
            )
            return cur.rowcount > 0

    def set_main_photo(self, user_id: int, gallery_id: int) -> None:
        """Устанавливает фото из галереи как основное в профиле."""
        with self._session() as conn:
            row = conn.execute("SELECT photo_id, media_type FROM photo_gallery WHERE id = ? AND user_id = ?", (gallery_id, user_id)).fetchone()
            if row:
                conn.execute("UPDATE profiles SET photo_id = ?, media_type = ? WHERE user_id = ?", (row["photo_id"], row["media_type"], user_id))

    def get_icebreakers(self, category: Optional[str] = None, limit: int = 10) -> List[sqlite3.Row]:
        with self._session() as conn:
            if category:
                return conn.execute(
                    "SELECT * FROM icebreakers WHERE category = ? AND is_active = 1 ORDER BY RANDOM() LIMIT ?",
                    (category, limit),
                ).fetchall()
            return conn.execute(
                "SELECT * FROM icebreakers WHERE is_active = 1 ORDER BY RANDOM() LIMIT ?",
                (limit,),
            ).fetchall()

    def add_icebreaker(self, text: str, category: str = "general") -> None:
        with self._session() as conn:
            conn.execute(
                "INSERT INTO icebreakers (text, category, is_active) VALUES (?, ?, 1)",
                (text, category),
            )

    def init_default_icebreakers(self) -> None:
        """Добавляет стандартные ледоколы, если таблица пуста."""
        with self._session() as conn:
            count = conn.execute("SELECT COUNT(*) FROM icebreakers").fetchone()[0]
            if count > 0:
                return
        default_icebreakers = [
            ("Привет! Как дела?", "general"),
            ("Чем обычно занимаешься в свободное время?", "general"),
            ("Твой любимый фильм или сериал?", "general"),
            ("Куда бы ты хотел съездить в отпуске?", "путешествия"),
            ("Ты больше сова или жаворонок?", "general"),
            ("Какая твоя любимая еда?", "еда"),
            ("Какая музыка тебя вдохновляет?", "музыка"),
            ("У тебя есть хобби?", "general"),
            ("Кто твой кумир или пример для подражания?", "general"),
            ("Что для тебя идеальный вечер?", "романтика"),
        ]
        for text, category in default_icebreakers:
            self.add_icebreaker(text, category)

    def find_candidate(self, user_id: int, city_prioritized: bool = True) -> Optional[sqlite3.Row]:
        me = self.get_profile(user_id)
        if not me:
            return None

        now = int(time.time())
        my_city = str(me["city"]).strip().lower()
        my_country = str(me["country"] or "Россия").strip().lower()
        my_age = int(me["age"])
        my_gender = str(me["gender"] or "M")
        my_looking = str(me["looking_for"] or "A")

        # Строгая граница 18+ / <18
        hard_min, hard_max = (14, 17) if my_age < 18 else (18, 100)
        pref_min = max(hard_min, int(me["min_pref_age"] or hard_min))
        pref_max = min(hard_max, int(me["max_pref_age"] or hard_max))

        with self._session() as conn:
            base_query = """
                SELECT *
                FROM profiles p
                WHERE p.user_id != ?
                  AND p.active = 1
                  AND p.banned = 0
                  AND p.age BETWEEN ? AND ?
                  AND (? = 'A' OR p.gender = ?)
                  AND (p.looking_for = 'A' OR p.looking_for = ?)
                  AND p.user_id NOT IN (SELECT target_id FROM reactions WHERE user_id = ?)
                  AND p.user_id NOT IN (SELECT blocked_id FROM blocks WHERE user_id = ?)
                  AND p.user_id NOT IN (SELECT user_id FROM blocks WHERE blocked_id = ?)
            """
            # Ищем в той же стране
            country_clause = " AND LOWER(TRIM(p.country)) = ? "
            order_clause = """
                ORDER BY (CASE WHEN p.boost_until > ? THEN 1 ELSE 0 END) DESC,
                         (CASE WHEN p.vip_until > ? THEN 1 ELSE 0 END) DESC,
                         p.verified DESC,
                         (CASE WHEN p.last_active > ? THEN 1 ELSE 0 END) DESC,
                         RANDOM()
                LIMIT 1
            """
            recent_cutoff = now - 3600 * 12

            # 1. Ищем в том же городе и стране
            if city_prioritized:
                params_pref_city = [
                    user_id,
                    pref_min,
                    pref_max,
                    my_looking,
                    my_looking,
                    my_gender,
                    user_id,
                    user_id,
                    user_id,
                    my_country,
                    my_city,
                    now,
                    now,
                    recent_cutoff,
                ]
                q1 = base_query + country_clause + " AND LOWER(TRIM(p.city)) = ? " + order_clause
                row = conn.execute(q1, params_pref_city).fetchone()
                if row:
                    return row

            # 2. Ищем в любой город той же страны
            params_pref_country = [
                user_id,
                pref_min,
                pref_max,
                my_looking,
                my_looking,
                my_gender,
                user_id,
                user_id,
                user_id,
                my_country,
                now,
                now,
                recent_cutoff,
            ]
            q2 = base_query + country_clause + order_clause
            row = conn.execute(q2, params_pref_country).fetchone()
            if row:
                return row

            # 3. Если по узкому фильтру возраста никого не осталось в стране — ищем по всей допустимой категории в стране
            params_fallback = [
                user_id,
                hard_min,
                hard_max,
                my_looking,
                my_looking,
                my_gender,
                user_id,
                user_id,
                user_id,
                my_country,
                now,
                now,
                recent_cutoff,
            ]
            q3 = base_query + country_clause + order_clause
            row = conn.execute(q3, params_fallback).fetchone()
            if row:
                return row

            # 4. Если в стране никого нет — ищем глобально (все страны)
            params_global = [
                user_id,
                pref_min,
                pref_max,
                my_looking,
                my_looking,
                my_gender,
                user_id,
                user_id,
                user_id,
                now,
                now,
                recent_cutoff,
            ]
            row = conn.execute(base_query + order_clause, params_global).fetchone()
            if row:
                return row

            # 5. Глобальный fallback по широкому возрасту
            params_global_fallback = [
                user_id,
                hard_min,
                hard_max,
                my_looking,
                my_looking,
                my_gender,
                user_id,
                user_id,
                user_id,
                now,
                now,
                recent_cutoff,
            ]
            return conn.execute(base_query + order_clause, params_global_fallback).fetchone()

    def create_order(
        self, order_id: str, user_id: int, product: str, base_amount: int, commission: int, total_amount: int
    ) -> None:
        with self._session() as conn:
            conn.execute(
                """
                INSERT INTO orders (
                    order_id, user_id, product, base_amount, commission, amount, currency, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'RUB', 'created', ?)
                """,
                (order_id, user_id, product, base_amount, commission, total_amount, int(time.time())),
            )

    def get_order(self, order_id: str) -> Optional[sqlite3.Row]:
        with self._session() as conn:
            return conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()

    def get_active_created_order(self, user_id: int, product: str) -> Optional[sqlite3.Row]:
        """Возвращает существующий неоплаченный заказ на этот товар, если есть."""
        with self._session() as conn:
            return conn.execute(
                "SELECT * FROM orders WHERE user_id = ? AND product = ? AND status = 'created' ORDER BY created_at DESC LIMIT 1",
                (user_id, product),
            ).fetchone()

    def mark_order_paid(self, order_id: str, charge_id: str) -> None:
        with self._session() as conn:
            conn.execute(
                "UPDATE orders SET status = 'paid', telegram_charge_id = ? WHERE order_id = ?",
                (charge_id, order_id),
            )

    def add_or_update_review(self, user_id: int, author_name: str, rating: int, text: str) -> Tuple[int, bool]:
        now = int(time.time())
        with self._session() as conn:
            existing = conn.execute("SELECT id FROM reviews WHERE user_id = ?", (user_id,)).fetchone()
            if existing:
                rev_id = int(existing["id"])
                conn.execute(
                    "UPDATE reviews SET author_name = ?, rating = ?, text = ?, created_at = ? WHERE id = ?",
                    (author_name, rating, text, now, rev_id),
                )
                return rev_id, False
            else:
                cur = conn.execute(
                    "INSERT INTO reviews (user_id, author_name, rating, text, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, author_name, rating, text, now),
                )
                return int(cur.lastrowid or 0), True

    def get_reviews_stats(self) -> Tuple[int, float]:
        with self._session() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt, COALESCE(AVG(rating), 0) as avg_r FROM reviews").fetchone()
            return int(row["cnt"]), round(float(row["avg_r"]), 1)

    def list_reviews(self, limit: int = 5, offset: int = 0) -> List[sqlite3.Row]:
        with self._session() as conn:
            return conn.execute(
                "SELECT * FROM reviews ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()

    def delete_review(self, review_id: int) -> bool:
        with self._session() as conn:
            cur = conn.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
            return cur.rowcount > 0

    def get_admin_stats(self) -> Dict[str, int]:
        now = int(time.time())
        with self._session() as conn:
            total_users = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
            verified_users = conn.execute("SELECT COUNT(*) FROM profiles WHERE verified = 1").fetchone()[0]
            vip_users = conn.execute("SELECT COUNT(*) FROM profiles WHERE vip_until > ?", (now,)).fetchone()[0]
            total_refs = conn.execute("SELECT COUNT(*) FROM profiles WHERE referred_by > 0").fetchone()[0]
            paid_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'paid'").fetchone()[0]
            total_rev = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM orders WHERE status = 'paid'"
            ).fetchone()[0]
            total_comm = conn.execute(
                "SELECT COALESCE(SUM(commission), 0) FROM orders WHERE status = 'paid'"
            ).fetchone()[0]
            total_reviews = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
            total_matches = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
            return {
                "users": total_users,
                "verified": verified_users,
                "vips": vip_users,
                "refs": total_refs,
                "orders": paid_orders,
                "revenue": total_rev,
                "commission": total_comm,
                "reviews": total_reviews,
                "matches": total_matches,
            }

    def get_all_user_ids(self) -> List[int]:
        with self._session() as conn:
            rows = conn.execute("SELECT user_id FROM profiles WHERE banned = 0").fetchall()
            return [int(r["user_id"]) for r in rows]


# =========================
# СОСТОЯНИЯ (FSM)
# =========================
class ProfileForm(StatesGroup):
    name = State()
    gender = State()
    looking_for = State()
    age = State()
    country = State()
    city = State()
    interests = State()
    bio = State()
    photo = State()


class EditProfileForm(StatesGroup):
    waiting_photo = State()
    waiting_bio = State()
    waiting_city = State()
    waiting_gender = State()
    waiting_looking_for = State()
    waiting_interests = State()
    waiting_age_range = State()


class VerifyForm(StatesGroup):
    waiting_video_note = State()


class SuperMsgForm(StatesGroup):
    waiting_text = State()


class ChatMsgForm(StatesGroup):
    waiting_text = State()


class SupportForm(StatesGroup):
    waiting_text = State()


class ReviewForm(StatesGroup):
    waiting_rating = State()
    waiting_text = State()


class AdminForm(StatesGroup):
    waiting_broadcast = State()
    waiting_gift_uid = State()
    waiting_search_uid = State()


class FilterForm(StatesGroup):
    waiting_age_min = State()
    waiting_age_max = State()


# =========================
# АВТО-ОЧИСТКА И ОТПРАВКА ЭКРАНОВ (ФОТО И ВИДЕО)
# =========================
async def clear_previous_bot_messages(bot: Bot, user_id: int) -> None:
    msg_ids = last_bot_messages.get(user_id, [])
    for mid in msg_ids:
        try:
            await bot.delete_message(chat_id=user_id, message_id=mid)
        except Exception:
            pass
    last_bot_messages[user_id] = []


async def delete_user_msg(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


async def send_screen(
    bot: Bot,
    user_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    photo_id: Optional[str] = None,
    media_type: str = "photo",
) -> Message:
    await clear_previous_bot_messages(bot, user_id)
    if photo_id:
        try:
            if media_type == "video":
                msg = await bot.send_video(
                    chat_id=user_id,
                    video=photo_id,
                    caption=text,
                    reply_markup=reply_markup,
                )
            else:
                msg = await bot.send_photo(
                    chat_id=user_id,
                    photo=photo_id,
                    caption=text,
                    reply_markup=reply_markup,
                )
            last_bot_messages[user_id] = [msg.message_id]
            return msg
        except Exception:
            pass

    msg = await bot.send_message(
        chat_id=user_id,
        text=text,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )
    last_bot_messages[user_id] = [msg.message_id]
    return msg


# =========================
# КЛАВИАТУРЫ И КАРТОЧКА ПРОФИЛЯ
# =========================
def kb_start_no_profile() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✨ Создать яркую анкету", callback_data="profile:create")],
            [
                InlineKeyboardButton(text="⭐️ Отзывы о боте", callback_data="reviews:menu"),
                InlineKeyboardButton(text="🛠 Нашли ошибку? Поддержка", url=SUPPORT_URL),
            ],
            [InlineKeyboardButton(text="💎 Velvet VPN › Доступно всё", url=VELVET_VPN_URL)],
        ]
    )


def kb_main_menu(db: Database, user_id: int) -> InlineKeyboardMarkup:
    """Главное меню бота — красивое и компактное."""
    p = db.get_profile(user_id)
    is_adult = bool(p and int(p["age"]) >= 18)
    liked_me_count = len(db.get_who_liked_me(user_id)) if p else 0

    rows: List[List[InlineKeyboardButton]] = [
        # ═══ ПОИСК И ЗНАКОМСТВА ═══
        [InlineKeyboardButton(text="🔥 Смотреть анкеты", callback_data="browse:next")],
        [InlineKeyboardButton(text=f"👀 Кто меня лайкнул ({liked_me_count})", callback_data="likes:list")],
        # ═══ ОБЩЕНИЕ ═══
        [
            InlineKeyboardButton(text="💌 Мэтчи и чат", callback_data="matches:list"),
            InlineKeyboardButton(text="⚙️ Анкета", callback_data="settings:open"),
        ],
        # ═══ БОНУСЫ И РЕФЕРАЛЫ ═══
        [
            InlineKeyboardButton(text="🎁 Бонус дня", callback_data="bonus:daily"),
            InlineKeyboardButton(text="👥 Рефералы", callback_data="ref:menu"),
        ],
    ]
    if p and int(p["verified"]) == 0:
        rows.append([InlineKeyboardButton(text="✅ Верификация (+1 💌)", callback_data="verify:start")])

    if is_adult and PAYMENTS_ENABLED:
        rows.append([InlineKeyboardButton(text="👑 Магазин VIP", callback_data="shop:menu")])

    rows.append([
        InlineKeyboardButton(text="⭐️ Отзывы", callback_data="reviews:menu"),
        InlineKeyboardButton(text="🆘 Поддержка", callback_data="support:menu"),
    ])
    rows.append([InlineKeyboardButton(text="💎 Velvet VPN", url=VELVET_VPN_URL)])

    if user_id == ADMIN_ID:
        rows.append([InlineKeyboardButton(text="🛠 Админ", callback_data="admin:menu")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_form_nav(
    back_step: Optional[str] = None,
    allow_skip_photo: bool = False,
    extra_rows: Optional[List[List[InlineKeyboardButton]]] = None,
) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    if extra_rows:
        rows.extend(extra_rows)
    if allow_skip_photo:
        rows.append([InlineKeyboardButton(text="⏭ Пропустить фото/видео", callback_data="form:skip_photo")])

    nav_row: List[InlineKeyboardButton] = []
    if back_step:
        nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"form:back:{back_step}"))
    nav_row.append(InlineKeyboardButton(text="❌ Отмена", callback_data="form:cancel"))
    rows.append(nav_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_interests_selector(selected: Set[str], done_callback: str, back_callback: Optional[str] = None) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    items = list(INTERESTS_MAP.items())
    for i in range(0, len(items), 2):
        row: List[InlineKeyboardButton] = []
        for k, label in items[i : i + 2]:
            mark = "✅ " if k in selected else ""
            row.append(InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"int_toggle:{k}"))
        rows.append(row)

    rows.append([InlineKeyboardButton(text=f"🚀 Готово (выбрано: {len(selected)})", callback_data=done_callback)])
    if back_callback:
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_candidate(target_id: int, show_supermsg: bool = True) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="❤️ Нравится", callback_data=f"react:like:{target_id}"),
            InlineKeyboardButton(text="👎 Дальше", callback_data=f"react:skip:{target_id}"),
        ]
    ]
    if show_supermsg:
        rows.append([InlineKeyboardButton(text="💌 Написать сразу (Супер-письмо)", callback_data=f"super:ask:{target_id}")])
    rows.append(
        [
            InlineKeyboardButton(text="⚠️ Пожаловаться", callback_data=f"safety:report:{target_id}"),
            InlineKeyboardButton(text="🚫 В ЧС", callback_data=f"safety:block:{target_id}"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="🔗 Поделиться анкетой", url=f"tg://user?id={target_id}"),
            InlineKeyboardButton(text="⬅️ В главное меню", callback_data="menu:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_shop_menu() -> InlineKeyboardMarkup:
    """Красивое меню магазина с категориями и ценниками (только ЮMoney)."""
    rows: List[List[InlineKeyboardButton]] = []

    # ═══ VIP ПОДПИСКИ ═══
    rows.append([InlineKeyboardButton(text="👑 VIP ПОДПИСКИ", callback_data="shop:info:vip")])
    for key in ("vip_3", "vip_7", "vip_30", "vip_90"):
        prod = PRODUCTS[key]
        _, _, total = calc_price_with_commission(prod.base_price_rub)
        rows.append([
            InlineKeyboardButton(
                text=f"  {prod.title} — {total} ₽",
                callback_data=f"shop:buy:{key}",
            )
        ])

    # ═══ ЛАЙКИ ═══
    rows.append([InlineKeyboardButton(text="❤️ ЛАЙКИ", callback_data="shop:info:likes")])
    for key in ("likes_unlimited", "likes_pack_50"):
        prod = PRODUCTS[key]
        _, _, total = calc_price_with_commission(prod.base_price_rub)
        rows.append([
            InlineKeyboardButton(
                text=f"  {prod.title} — {total} ₽",
                callback_data=f"shop:buy:{key}",
            )
        ])

    # ═══ БУСТЫ ═══
    rows.append([InlineKeyboardButton(text="🚀 БУСТЫ (ТОП)", callback_data="shop:info:boost")])
    for key in ("boost_24", "boost_72"):
        prod = PRODUCTS[key]
        _, _, total = calc_price_with_commission(prod.base_price_rub)
        rows.append([
            InlineKeyboardButton(
                text=f"  {prod.title} — {total} ₽",
                callback_data=f"shop:buy:{key}",
            )
        ])

    # ═══ СУПЕР-ПИСЬМА ═══
    rows.append([InlineKeyboardButton(text="💌 СУПЕР-ПИСЬМА", callback_data="shop:info:msg")])
    for key in ("super_1", "super_5", "super_15"):
        prod = PRODUCTS[key]
        _, _, total = calc_price_with_commission(prod.base_price_rub)
        rows.append([
            InlineKeyboardButton(
                text=f"  {prod.title} — {total} ₽",
                callback_data=f"shop:buy:{key}",
            )
        ])

    # ═══ ДРУГОЕ ═══
    rows.append([InlineKeyboardButton(text="🔧 ДОПОЛНИТЕЛЬНО", callback_data="shop:info:extra")])
    for key in ("reset_skips", "see_likes", "anonymous_view"):
        prod = PRODUCTS[key]
        _, _, total = calc_price_with_commission(prod.base_price_rub)
        rows.append([
            InlineKeyboardButton(
                text=f"  {prod.title} — {total} ₽",
                callback_data=f"shop:buy:{key}",
            )
        ])

    rows.append([InlineKeyboardButton(text="🎁 VIP бесплатно (пригласи друга)", callback_data="ref:menu")])
    rows.append(
        [
            InlineKeyboardButton(text="🛠 Поддержка", url=SUPPORT_URL),
            InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="menu:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_settings(active: bool, is_verified: bool, is_adult: bool) -> InlineKeyboardMarkup:
    toggle_text = "⏸ Скрыть мою анкету из поиска" if active else "▶️ Включить показ анкеты"
    rows: List[List[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="📸 Сменить фото/видео", callback_data="edit:photo"),
            InlineKeyboardButton(text="💬 Изменить «О себе»", callback_data="edit:bio"),
        ],
        [
            InlineKeyboardButton(text="🏙 Сменить город", callback_data="edit:city"),
            InlineKeyboardButton(text="🎯 Мои интересы", callback_data="edit:interests"),
        ],
        [
            InlineKeyboardButton(text="🚻 Пол / Кого ищу", callback_data="edit:prefs"),
        ],
        [
            InlineKeyboardButton(text="📸 Моя галерея фото", callback_data="gallery:my:menu"),
        ],
        [
            InlineKeyboardButton(text="🔍 Фильтры поиска", callback_data="filters:menu"),
        ],
    ]
    if is_adult:
        rows[-1].append(InlineKeyboardButton(text="🎂 Возраст поиска", callback_data="edit:agerange"))

    if not is_verified:
        rows.append([InlineKeyboardButton(text="✅ Верифицировать профиль кружочком", callback_data="verify:start")])

    rows.extend(
        [
            [InlineKeyboardButton(text="✏️ Заполнить всю анкету заново", callback_data="profile:create")],
            [InlineKeyboardButton(text=toggle_text, callback_data="settings:toggle_active")],
            [InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data="menu:home")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def card_divider() -> str:
    """Единый разделитель секций карточки."""
    return "━━━━━━━━━━━━━━━━━━━━"


def card_header(title: str, badges: List[str]) -> str:
    """Верх карточки сタイトル и бейджами."""
    badge_str = f" [{' | '.join(badges)}]" if badges else ""
    return f"💘 <b>{esc(title)}</b>{badge_str}"


def card_line(emoji: str, label: str, value: str) -> str:
    """Одна строка профиля в едином стиле."""
    return f"{emoji} <b>{label}:</b> {value}"


def card_section(title: str) -> str:
    """Заголовок раздела внутри карточки."""
    return f"\n📋 <b>{title}</b>\n"


def screen_footer(text: str) -> str:
    """Нижняя часть экрана с разделителем."""
    return f"\n{divider_line()}\n<i>{esc(text)}</i>"


def screen_section(title: str, content: str) -> str:
    """Секция внутри экрана с заголовком и содержимым."""
    return f"\n📂 <b>{title}</b>\n{content}"


def format_profile_card(
    p: sqlite3.Row,
    title: str = "Анкета участника",
    show_prefs: bool = False,
    viewer: Optional[sqlite3.Row] = None,
) -> str:
    now = int(time.time())
    badges: List[str] = []
    if int(p["verified"] or 0) == 1:
        badges.append("✅ Проверено")
    if int(p["vip_until"]) > now:
        badges.append("👑 VIP")
    if int(p["boost_until"]) > now:
        badges.append("⚡️ TOP")

    gender_str = format_gender_label(str(p["gender"] or "M"))
    online_str = format_online_status(int(p["last_active"] or 0))

    parts: List[str] = []
    parts.append(screen_header(title, "Знакомства «Лавчик»"))

    # Основная информация в боксе
    info_lines = [
        stat_row("👤", "Имя", f"{esc(p['name'])}, {int(p['age'])} лет", highlight=True),
        stat_row("🚻", "Пол", gender_str),
        stat_row("🌍", "Страна / Город", f"{esc(str(p['country'] or 'Россия'))} / {esc(p['city'])}  •  {online_str}"),
    ]
    if show_prefs:
        age_range_str = f"{p['min_pref_age']}–{p['max_pref_age']} лет"
        info_lines.append(stat_row("🔍", "Ищу", f"{format_looking_for_label(str(p['looking_for'] or 'A'))} ({age_range_str})"))
    parts.append(screen_box("📋 Профиль", info_lines))

    # Интересы
    interests_raw = str(p["interests"] or "")
    if interests_raw:
        parts.append(screen_section("🎯 Интересы", format_interests_list(interests_raw)))

    # Совместимость
    if viewer and int(viewer["user_id"]) != int(p["user_id"]):
        score, common = calc_compatibility(viewer, p)
        common_str = f" (Общее: {', '.join(common[:3])})" if common else ""
        parts.append(screen_section("🔥 Совместимость", f"{score}% с тобой{common_str}"))

    # О себе
    parts.append(screen_section("💬 О себе", f"<blockquote>{esc(p['bio'])}</blockquote>"))

    parts.append(random_divider())
    return "\n".join(parts)


# =========================
# РОУТЕР И ГЛАВНОЕ МЕНЮ
# =========================
router = Router()


async def render_home(bot: Bot, db: Database, user_id: int, username: Optional[str] = None) -> None:
    p = db.get_profile(user_id)
    if not p:
        welcome_lines = [
            stat_row("🎯", "Подбор", "По полу, возрасту, городу и интересам"),
            stat_row("💞", "Мэтч", "Взаимная симпатия — только если понравились друг другу"),
            stat_row("💬", "Чат", "Встроенная переписка и ледоколы"),
            stat_row("👤", "Верификация", "Защита от фейков и ботов"),
            stat_row("👑", "VIP", "Статус, бусты и супер-письма"),
        ]
        welcome_box = screen_box("💎 Что внутри «Лавчик»", welcome_lines)
        text = (
            f"{screen_header('Лавчик 💘', 'Знакомства нового поколения')}\n\n"
            f"<b>«Лавчик»</b> — сервис знакомств для поиска друзей, общения и серьезных отношений.\n\n"
            f"{welcome_box}\n\n"
            f"🌍 Доступно: Россия, Беларусь, Казахстан, Украина и другие страны\n\n"
            f"👇 <b>Создай анкету за 30 секунд и начни знакомства:</b>"
        )
        await send_chat_action(bot, user_id, "typing")
        await send_sticker(bot, user_id, "welcome")
        await send_screen(bot, user_id, text, reply_markup=kb_start_no_profile())
        return

    if int(p["banned"] or 0) == 1:
        await send_screen(
            bot,
            user_id,
            "🚫 <b>Твоя анкета заблокирована модератором за нарушение правил сообщества.</b>\n"
            f"Если ты считаешь, что произошла ошибка, напиши в поддержку: {SUPPORT_URL}",
        )
        return

    db.touch_user(user_id, username=username)
    p = db.get_profile(user_id) or p

    now = int(time.time())
    vip_active = int(p["vip_until"]) > now
    boost_active = int(p["boost_until"]) > now
    active_status = "🟢 В поиске" if int(p["active"]) == 1 else "⏸ Скрыта"

    vip_status = (
        f"👑 VIP до {(int(p['vip_until']) - now) // 86400} дн."
        if vip_active else "👑 VIP не подключён"
    )
    boost_status = "🚀 ТОП активно" if boost_active else ""

    # Счётчик бесплатных лайков
    if vip_active:
        likes_line = "♾️ Безлимит (VIP)"
    else:
        used = db.count_today_likes(user_id)
        remaining = max(0, FREE_DAILY_LIKES - used)
        likes_line = f"{remaining}/{FREE_DAILY_LIKES} сегодня"

    # Красивый блок профиля
    profile_lines = [
        stat_row("📡", "Статус", active_status),
        stat_row("👑", "VIP", vip_status),
        stat_row("❤️", "Лайки", likes_line),
        stat_row("💌", "Супер-писем", str(p["superlikes"])),
        stat_row("👥", "Рефералов", str(p["ref_count"])),
    ]
    if boost_status:
        profile_lines.append(stat_row("🚀", "Буст", boost_status))

    profile_box = screen_box("📊 Моя статистика", profile_lines)

    text = (
        f"{format_profile_card(p, title='Моя анкета', show_prefs=True)}\n"
        f"{profile_box}\n"
        f"{screen_footer('Нажми «🔥 Смотреть анкеты», чтобы начать знакомства!')}"
    )
    await send_chat_action(bot, user_id, "typing")
    await send_sticker(bot, user_id, "welcome")
    await send_screen(
        bot,
        user_id,
        text,
        reply_markup=kb_main_menu(db, user_id),
        photo_id=p["photo_id"] if p["photo_id"] else None,
        media_type=str(p["media_type"] or "photo"),
    )


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_msg(message)
    await state.clear()
    uid = message.from_user.id

    args = (command.args or "").strip()
    if args.startswith("ref_"):
        try:
            inviter_id = int(args.replace("ref_", ""))
            db.set_pending_referral(uid, inviter_id)
        except ValueError:
            pass

    await render_home(bot, db, uid, username=message.from_user.username)


@router.callback_query(F.data == "menu:home")
async def cb_menu_home(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    await state.clear()
    await call.answer()
    await send_transition_sticker(bot, call.from_user.id, "home")
    await render_home(bot, db, call.from_user.id, username=call.from_user.username)


# =========================
# ЕЖЕДНЕВНЫЙ БОНУС И ВЕРИФИКАЦИЯ КРУЖОЧКОМ
# =========================
@router.callback_query(F.data == "bonus:daily")
async def cb_daily_bonus(call: CallbackQuery, db: Database, bot: Bot) -> None:
    uid = call.from_user.id
    ok, hours_left = db.claim_daily_bonus(uid)
    if not ok:
        await call.answer(
            f"⏳ Ты уже забирал сегодняшний подарок! Следующий бонус будет доступен через {hours_left} ч.",
            show_alert=True,
        )
        return

    await call.answer("🎁 Бонус получен: +2 часа ТОП-продвижения!", show_alert=True)
    await send_sticker(bot, uid, "daily_bonus")
    await send_screen(
        bot,
        uid,
        f"{celebration_line('Бонус дня получен!')}\n"
        f"⏰ +2 часа ТОП-продвижения начислены.\n"
        f"🔥 Заходи завтра за следующим подарком!",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home")]]
        ),
    )


@router.callback_query(F.data == "verify:start")
async def cb_verify_start(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    uid = call.from_user.id
    p = db.get_profile(uid)
    if not p:
        await call.answer("Сначала создай анкету!", show_alert=True)
        return
    if int(p["verified"] or 0) == 1:
        await call.answer("Твоя анкета уже верифицирована ✅", show_alert=True)
        return

    await call.answer()
    await state.set_state(VerifyForm.waiting_video_note)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data="menu:home")]]
    )
    await send_screen(
        bot,
        uid,
        "✅ <b>Верификация анкеты (Значок «Проверено»)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Подтверждённые анкеты вызывают максимум доверия, показываются выше в поиске и получают <b>+1 💌 Супер-письмо в подарок</b>!\n\n"
        "📹 <b>Как пройти за 5 секунд:</b>\n"
        "Зажми иконку видеосообщения («кружочка») в Telegram, покажи лицо и помаши рукой в камеру 👇",
        reply_markup=kb,
    )


@router.message(VerifyForm.waiting_video_note, F.video_note)
async def msg_verify_video_note(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_msg(message)
    uid = message.from_user.id
    await state.clear()

    first_time = db.set_verified(uid, True)
    bonus_str = "\n🎁 Тебе начислено <b>+1 💌 Супер-письмо</b> в подарок!" if first_time else ""

    if ADMIN_ID > 0:
        try:
            p = db.get_profile(uid)
            await bot.send_video_note(ADMIN_ID, video_note=message.video_note.file_id)
            await bot.send_message(
                ADMIN_ID,
                f"✅ <b>Верификация кружочком:</b> {esc(p['name'] if p else uid)} (ID: <code>{uid}</code>)",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="❌ Снять галочку", callback_data=f"adminmod:unverify:{uid}")],
                        [InlineKeyboardButton(text="🚫 Забанить", callback_data=f"adminmod:ban:{uid}")],
                    ]
                ),
            )
        except Exception:
            pass

    await send_screen(
        bot,
        uid,
        f"🎉 <b>Твоя анкета успешно подтверждена и получила значок «✅ Проверено»!</b>{bonus_str}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔥 Смотреть анкеты", callback_data="browse:next")],
                [InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home")],
            ]
        ),
    )


@router.message(VerifyForm.waiting_video_note)
async def msg_verify_invalid(message: Message, bot: Bot) -> None:
    await delete_user_msg(message)
    await send_screen(
        bot,
        message.from_user.id,
        "⚠️ <b>Пожалуйста, запиши именно круглое видеосообщение («кружочек») через камеру Telegram:</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Отмена", callback_data="menu:home")]]
        ),
    )


# =========================
# РЕФЕРАЛЬНАЯ СИСТЕМА
# =========================
@router.callback_query(F.data == "ref:menu")
async def cb_ref_menu(call: CallbackQuery, db: Database, bot: Bot) -> None:
    uid = call.from_user.id
    p = db.get_profile(uid)
    if not p:
        await call.answer("Сначала создай анкету!", show_alert=True)
        return

    await call.answer()
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
    share_text = "💘 Зацени крутой бот для знакомств Лавчик! Нашёл тут много интересных людей из нашего города 🔥"
    share_url = f"https://t.me/share/url?{urlencode({'url': ref_link, 'text': share_text})}"

    ref_count = int(p["ref_count"])
    progress = ref_count % 3
    bar = "🟩" * progress + "⬜️" * (3 - progress)
    left_for_vip = 3 - progress

    friend_box = screen_box("💎 Твой друг получит", [
        stat_row("💌", "Супер-писем", "2 шт. при регистрации (вместо 1)"),
    ])
    rewards_box = screen_box("🔥 Твои награды", [
        stat_row("1️⃣", "Регистрация друга", "+1 💌 Супер-письмо + 6 часов 🚀 ТОП-продвижения"),
        stat_row("2️⃣", "За каждых 3-х друзей", "👑 VIP на 24 часа + бонусное Супер-письмо"),
        stat_row("3️⃣", "Покупка другом товара", "+24 часа VIP и +2 💌 Супер-письма"),
    ])
    stats_box = screen_box("📊 Твоя статистика", [
        stat_row("👥", "Приглашено", f"{ref_count} чел.", highlight=True),
        stat_row("👑", "До следующего VIP", f"[{bar}] — осталось {left_for_vip} др."),
    ])
    link_box = screen_box("🔗 Твоя ссылка", [
        stat_row("📋", "Ссылка", f"<code>{ref_link}</code>"),
    ])
    text = (
        f"{screen_header('Партнёрская программа', 'Приглашай друзей и получай привилегии')}\n"
        "Приглашай друзей по своей личной ссылке и получай платные привилегии <b>абсолютно бесплатно</b>!\n\n"
        f"{friend_box}\n\n"
        f"{rewards_box}\n\n"
        f"{stats_box}\n\n"
        f"{link_box}"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📲 Поделиться ссылкой в 1 клик", url=share_url)],
            [InlineKeyboardButton(text="🏆 ТОП-10 лучших приглашающих", callback_data="ref:leaderboard")],
            [InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data="menu:home")],
        ]
    )
    await send_screen(bot, uid, text, reply_markup=kb)


@router.callback_query(F.data == "ref:leaderboard")
async def cb_ref_leaderboard(call: CallbackQuery, db: Database, bot: Bot) -> None:
    await call.answer()
    top = db.get_top_referrers(limit=10)
    lines = [
        "🏆 <b>ТОП-10 амбассадоров «Лавчик»</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    if not top:
        lines.append("Список пока пуст. Пригласи первого друга и займи 1-е место! 🥇")
    else:
        medals = ["🥇", "🥈", "🥉"]
        for idx, r in enumerate(top, start=1):
            medal = medals[idx - 1] if idx <= 3 else f"<b>{idx}.</b>"
            lines.append(f"{medal} <b>{esc(r['name'])}</b> ({esc(r['city'])}) — 👥 <b>{r['ref_count']}</b> друзей")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад к моей ссылке", callback_data="ref:menu")],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home")],
        ]
    )
    await send_screen(bot, call.from_user.id, "\n".join(lines), reply_markup=kb)


# =========================
# СИСТЕМА ОТЗЫВОВ (⭐️ ОТЗЫВЫ)
# =========================
async def render_reviews_screen(bot: Bot, db: Database, user_id: int, offset: int = 0) -> None:
    total_cnt, avg_rating = db.get_reviews_stats()
    reviews = db.list_reviews(limit=5, offset=offset)

    stars_header = "⭐️" * int(round(avg_rating)) if total_cnt > 0 else "⭐️⭐️⭐️⭐️⭐️"
    header = (
        f"{screen_header('Отзывы «Лавчик»', f'Средняя оценка: {avg_rating} / 5.0 {stars_header}')}\n"
        f"💬 <b>Всего отзывов:</b> {total_cnt}\n"
        f"🎁 <i>За свой первый отзыв ты получишь +1 💌 Супер-письмо бесплатно!</i>\n"
    )

    if not reviews:
        no_reviews_box = screen_box("Пока нет отзывов", [
            stat_row("✨", "Будь первым!", "Оставь отзыв и получи +1 💌 Супер-письмо"),
        ])
        body = f"\n{no_reviews_box}"
    else:
        blocks: List[str] = []
        for r in reviews:
            st = "⭐️" * int(r["rating"])
            date_str = time.strftime("%d.%m.%Y", time.localtime(int(r["created_at"])))
            blocks.append(
                f"👤 <b>{esc(r['author_name'])}</b> ({date_str}) — {st}\n"
                f"<blockquote>{esc(r['text'])}</blockquote>"
            )
        body = "\n\n".join(blocks)

    nav_row: List[InlineKeyboardButton] = []
    if offset > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Пред.", callback_data=f"reviews:page:{max(0, offset - 5)}"))
    if offset + 5 < total_cnt:
        nav_row.append(InlineKeyboardButton(text="След. ➡️", callback_data=f"reviews:page:{offset + 5}"))

    kb_rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="✍️ Оставить отзыв (+1 💌 в подарок)", callback_data="reviews:add")]
    ]
    if nav_row:
        kb_rows.append(nav_row)
    kb_rows.append([InlineKeyboardButton(text="🛠 Нашли ошибку? Написать в поддержку", url=SUPPORT_URL)])
    kb_rows.append([InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data="menu:home")])

    await send_screen(bot, user_id, header + body, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@router.callback_query(F.data == "reviews:menu")
async def cb_reviews_menu(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    await state.clear()
    await call.answer()
    await render_reviews_screen(bot, db, call.from_user.id, offset=0)


@router.callback_query(F.data.startswith("reviews:page:"))
async def cb_reviews_page(call: CallbackQuery, db: Database, bot: Bot) -> None:
    await call.answer()
    offset = int(call.data.split(":")[2])
    await render_reviews_screen(bot, db, call.from_user.id, offset=offset)


@router.callback_query(F.data == "reviews:add")
async def cb_reviews_add(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    uid = call.from_user.id
    p = db.get_profile(uid)
    if not p:
        await call.answer("Сначала создай анкету, чтобы оставить отзыв!", show_alert=True)
        return

    await call.answer()
    await state.set_state(ReviewForm.waiting_rating)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐️⭐️⭐️⭐️⭐️ Отлично (5)", callback_data="reviews:rate:5")],
            [InlineKeyboardButton(text="⭐️⭐️⭐️⭐️ Хорошо (4)", callback_data="reviews:rate:4")],
            [InlineKeyboardButton(text="⭐️⭐️⭐️ Нормально (3)", callback_data="reviews:rate:3")],
            [InlineKeyboardButton(text="⭐️⭐️ Плохо (2)", callback_data="reviews:rate:2")],
            [InlineKeyboardButton(text="⭐️ Ужасно (1)", callback_data="reviews:rate:1")],
            [
                InlineKeyboardButton(text="⬅️ Назад к отзывам", callback_data="reviews:menu"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="menu:home"),
            ],
        ]
    )
    await send_screen(
        bot,
        uid,
        "✍️ <b>Оставить отзыв о боте «Лавчик»</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Шаг 1 из 2: Выбери оценку работы нашего бота от 1 до 5 звёзд 👇",
        reply_markup=kb,
    )


@router.callback_query(ReviewForm.waiting_rating, F.data.startswith("reviews:rate:"))
async def cb_reviews_rate(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await call.answer()
    rating = int(call.data.split(":")[2])
    await state.update_data(rating=rating)
    await state.set_state(ReviewForm.waiting_text)

    stars = "⭐️" * rating
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Изменить оценку", callback_data="reviews:add"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="reviews:menu"),
            ]
        ]
    )
    await send_screen(
        bot,
        call.from_user.id,
        f"✍️ <b>Твоя оценка:</b> {stars}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Шаг 2 из 2: Напиши текст своего отзыва (от 5 до 400 символов) и отправь в чат 👇\n\n"
        "<i>Расскажи, что тебе нравится в боте или кого удалось найти!</i>",
        reply_markup=kb,
    )


@router.message(ReviewForm.waiting_text)
async def msg_reviews_text(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_msg(message)
    uid = message.from_user.id
    text = (message.text or "").strip()

    kb_err = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Изменить оценку", callback_data="reviews:add"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="reviews:menu"),
            ]
        ]
    )
    if len(text) < 5 or len(text) > 400:
        await send_screen(
            bot,
            uid,
            "⚠️ <b>Текст отзыва должен быть от 5 до 400 символов.</b>\nПопробуй написать чуть подробнее:",
            reply_markup=kb_err,
        )
        return

    data = await state.get_data()
    rating = int(data.get("rating", 5))
    await state.clear()

    p = db.get_profile(uid)
    author_name = f"{p['name']}, {p['city']}" if p else (message.from_user.full_name or "Участник")

    rev_id, is_first = db.add_or_update_review(uid, author_name, rating, text)
    bonus_line = ""
    if is_first:
        db.add_superlikes(uid, 1)
        bonus_line = "\n🎁 <b>Бонус начислен:</b> Тебе выдано <b>+1 💌 Супер-письмо</b> за первый отзыв!"

    if ADMIN_ID > 0:
        try:
            adm_kb = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🗑 Удалить отзыв", callback_data=f"adminrev:del:{rev_id}")]]
            )
            await bot.send_message(
                ADMIN_ID,
                f"⭐️ <b>Новый отзыв #{rev_id}</b>\n"
                f"👤 Автор: {esc(author_name)} (ID: <code>{uid}</code>)\n"
                f"Оценка: {'⭐️' * rating}\n\n"
                f"<blockquote>{esc(text)}</blockquote>",
                reply_markup=adm_kb,
            )
        except Exception:
            pass

    kb_done = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐️ Посмотреть все отзывы", callback_data="reviews:menu")],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home")],
        ]
    )
    await send_screen(
        bot,
        uid,
        f"✅ <b>Спасибо! Твой отзыв успешно опубликован!</b>{bonus_line}",
        reply_markup=kb_done,
    )


@router.callback_query(F.data.startswith("adminrev:del:"))
async def cb_admin_delete_review(call: CallbackQuery, db: Database) -> None:
    if call.from_user.id != ADMIN_ID:
        return
    rev_id = int(call.data.split(":")[2])
    deleted = db.delete_review(rev_id)
    await call.answer("Отзыв удалён!" if deleted else "Отзыв уже был удалён.", show_alert=True)
    try:
        await call.message.edit_text(f"🗑 <b>Отзыв #{rev_id} был удалён администратором.</b>")
    except Exception:
        pass


# =========================
# СОЗДАНИЕ АНКЕТЫ (ИМЯ, ПОЛ, КОГО ИЩУ, ВОЗРАСТ, ГОРОД, ИНТЕРЕСЫ, О СЕБЕ, ФОТО/ВИДЕО)
# =========================
async def ask_step_name(bot: Bot, user_id: int, state: FSMContext) -> None:
    await state.set_state(ProfileForm.name)
    bar = progress_bar(1, 8, length=8)
    await send_screen(
        bot,
        user_id,
        f"{screen_header('Создание анкеты', 'Шаг 1 из 8 — Имя')}\n"
        f"{bar}\n\n"
        f"Как тебя зовут? Напиши своё имя в чат:",
        reply_markup=kb_form_nav(back_step=None),
    )


async def ask_step_gender(bot: Bot, user_id: int, state: FSMContext) -> None:
    await state.set_state(ProfileForm.gender)
    gender_buttons = [
        [
            InlineKeyboardButton(text="👨 Я парень", callback_data="form:gender:M"),
            InlineKeyboardButton(text="👩 Я девушка", callback_data="form:gender:F"),
        ]
    ]
    bar = progress_bar(2, 8, length=8)
    await send_screen(
        bot,
        user_id,
        f"{screen_header('Создание анкеты', 'Шаг 2 из 8 — Пол')}\n"
        f"{bar}\n\n"
        f"Укажи свой пол (нажми на кнопку ниже):",
        reply_markup=kb_form_nav(back_step="name", extra_rows=gender_buttons),
    )


async def ask_step_looking_for(bot: Bot, user_id: int, state: FSMContext) -> None:
    await state.set_state(ProfileForm.looking_for)
    looking_buttons = [
        [
            InlineKeyboardButton(text="👩 Ищу девушку", callback_data="form:looking:F"),
            InlineKeyboardButton(text="👨 Ищу парня", callback_data="form:looking:M"),
        ],
        [InlineKeyboardButton(text="👥 Ищу всех (Дружба и общение)", callback_data="form:looking:A")],
    ]
    bar = progress_bar(3, 8, length=8)
    await send_screen(
        bot,
        user_id,
        f"{screen_header('Создание анкеты', 'Шаг 3 из 8 — Кого ищу')}\n"
        f"{bar}\n\n"
        f"Кого ты хочешь найти в боте?",
        reply_markup=kb_form_nav(back_step="gender", extra_rows=looking_buttons),
    )


async def ask_step_age(bot: Bot, user_id: int, state: FSMContext) -> None:
    await state.set_state(ProfileForm.age)
    bar = progress_bar(4, 8, length=8)
    await send_screen(
        bot,
        user_id,
        f"{screen_header('Создание анкеты', 'Шаг 4 из 8 — Возраст')}\n"
        f"{bar}\n\n"
        f"Сколько тебе лет? Отправь число от <b>14</b> до <b>80</b>:",
        reply_markup=kb_form_nav(back_step="looking_for"),
    )


async def ask_step_country(bot: Bot, user_id: int, state: FSMContext) -> None:
    await state.set_state(ProfileForm.country)
    await state.update_data(choosing_country=True)
    quick_countries = [
        [
            InlineKeyboardButton(text="🇷🇺 Россия", callback_data="form:country:Россия"),
            InlineKeyboardButton(text="🇧🇾 Беларусь", callback_data="form:country:Беларусь"),
        ],
        [
            InlineKeyboardButton(text="🇰🇿 Казахстан", callback_data="form:country:Казахстан"),
            InlineKeyboardButton(text="🇺🇦 Украина", callback_data="form:country:Украина"),
        ],
        [InlineKeyboardButton(text="🌍 Другая страна", callback_data="form:country:other")],
    ]
    bar = progress_bar(4, 8, length=8)
    await send_screen(
        bot,
        user_id,
        f"{screen_header('Создание анкеты', 'Шаг 5 из 8 — Страна')}\n"
        f"{bar}\n\n"
        f"Выбери свою страну или напиши название в чат:",
        reply_markup=kb_form_nav(back_step="age", extra_rows=quick_countries),
    )


async def ask_step_city(bot: Bot, user_id: int, state: FSMContext) -> None:
    await state.set_state(ProfileForm.city)
    await state.update_data(choosing_country=False)
    data = await state.get_data()
    selected_country = data.get("country", "Россия")
    
    # Показываем города выбранной страны (для России — большой список, для других — ручной ввод)
    if selected_country == "Россия":
        quick_cities = []
        chunk = RUSSIAN_CITIES[:12]
        for i in range(0, len(chunk), 2):
            row = []
            row.append(InlineKeyboardButton(text=f"📍 {chunk[i]}", callback_data=f"form:city:{chunk[i]}"))
            if i + 1 < len(chunk):
                row.append(InlineKeyboardButton(text=f"📍 {chunk[i+1]}", callback_data=f"form:city:{chunk[i+1]}"))
            quick_cities.append(row)
        quick_cities.append([InlineKeyboardButton(text="📝 Написать свой город", callback_data="form:city:manual")])
    else:
        quick_cities = [
            [InlineKeyboardButton(text="📝 Написать свой город", callback_data="form:city:manual")],
        ]

    bar = progress_bar(5, 8, length=8)
    await send_screen(
        bot,
        user_id,
        f"{screen_header('Создание анкеты', 'Шаг 6 из 8 — Город')}\n"
        f"{bar}\n\n"
        f"Страна: <b>{esc(selected_country)}</b>\n"
        f"Из какого ты города? Выбери кнопкой или нажми «Написать свой город»:",
        reply_markup=kb_form_nav(back_step="country", extra_rows=quick_cities),
    )


async def ask_step_interests(bot: Bot, user_id: int, state: FSMContext) -> None:
    await state.set_state(ProfileForm.interests)
    data = await state.get_data()
    selected = parse_interests(str(data.get("interests", "")))
    bar = progress_bar(6, 8, length=8)
    await send_screen(
        bot,
        user_id,
        f"{screen_header('Создание анкеты', 'Шаг 6 из 8 — Интересы')}\n"
        f"{bar}\n\n"
        f"Выбери до 5 своих интересов (по ним бот будет считать <b>% совместимости</b> с другими людьми), затем нажми «🚀 Готово»:",
        reply_markup=kb_interests_selector(selected, done_callback="form:interests_done", back_callback="form:back:city"),
    )


async def ask_step_bio(bot: Bot, user_id: int, state: FSMContext) -> None:
    await state.set_state(ProfileForm.bio)
    bar = progress_bar(7, 8, length=8)
    await send_screen(
        bot,
        user_id,
        f"{screen_header('Создание анкеты', 'Шаг 7 из 8 — О себе')}\n"
        f"{bar}\n\n"
        f"Расскажи немного о себе: чем увлекаешься, кого хочешь найти?",
        reply_markup=kb_form_nav(back_step="interests"),
    )


async def ask_step_photo(bot: Bot, user_id: int, state: FSMContext) -> None:
    await state.set_state(ProfileForm.photo)
    bar = progress_bar(8, 8, length=8)
    await send_screen(
        bot,
        user_id,
        f"{screen_header('Создание анкеты', 'Шаг 8 из 8 — Фото/Видео')}\n"
        f"{bar}\n\n"
        f"Отправь своё лучшее <b>фото</b> или <b>короткое видео (до 15 сек)</b> для анкеты!\n\n"
        f"<i>С фото или видео ты получишь в 5 раз больше симпатий!</i>",
        reply_markup=kb_form_nav(back_step="bio", allow_skip_photo=True),
    )


@router.callback_query(F.data == "profile:create")
async def cb_profile_create(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await call.answer()
    await state.clear()
    await ask_step_name(bot, call.from_user.id, state)


@router.callback_query(F.data == "form:cancel")
async def cb_form_cancel(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    await state.clear()
    await call.answer("Действие отменено")
    await send_sticker(bot, call.from_user.id, "bye")
    await render_home(bot, db, call.from_user.id, username=call.from_user.username)


@router.callback_query(F.data.startswith("form:back:"))
async def cb_form_back(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await call.answer()
    step = call.data.split(":", 2)[2]
    uid = call.from_user.id
    if step == "name":
        await ask_step_name(bot, uid, state)
    elif step == "gender":
        await ask_step_gender(bot, uid, state)
    elif step == "looking_for":
        await ask_step_looking_for(bot, uid, state)
    elif step == "age":
        await ask_step_age(bot, uid, state)
    elif step == "country":
        await ask_step_country(bot, uid, state)
    elif step == "city":
        await ask_step_city(bot, uid, state)
    elif step == "interests":
        await ask_step_interests(bot, uid, state)
    elif step == "bio":
        await ask_step_bio(bot, uid, state)


@router.message(ProfileForm.name)
async def form_name(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_msg(message)
    name = (message.text or "").strip()
    if not Database.validate_name(name):
        await send_screen(
            bot,
            message.from_user.id,
            "⚠️ <b>Имя должно быть от 2 до 32 символов и содержать только буквы, пробелы, дефис или апостроф.</b>\nПопробуй ввести имя ещё раз:",
            reply_markup=kb_form_nav(back_step=None),
        )
        return
    await state.update_data(name=name)
    await ask_step_gender(bot, message.from_user.id, state)


@router.callback_query(ProfileForm.gender, F.data.startswith("form:gender:"))
async def form_gender_cb(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await call.answer()
    gender = call.data.split(":")[2]
    await state.update_data(gender=gender)
    await ask_step_looking_for(bot, call.from_user.id, state)


@router.callback_query(ProfileForm.looking_for, F.data.startswith("form:looking:"))
async def form_looking_cb(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await call.answer()
    looking_for = call.data.split(":")[2]
    await state.update_data(looking_for=looking_for)
    await ask_step_age(bot, call.from_user.id, state)


@router.message(ProfileForm.age)
async def form_age(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_msg(message)
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (14 <= int(raw) <= 80):
        await send_screen(
            bot,
            message.from_user.id,
            "⚠️ <b>Введи корректный возраст числом от 14 до 80:</b>",
            reply_markup=kb_form_nav(back_step="looking_for"),
        )
        return
    await state.update_data(age=int(raw))
    await ask_step_country(bot, message.from_user.id, state)


@router.callback_query(ProfileForm.country, F.data.startswith("form:country:"))
async def form_country_cb(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await call.answer()
    country = call.data.split(":", 2)[2]
    if country == "other":
        await state.update_data(choosing_country=True, country="")
        await send_screen(
            bot,
            call.from_user.id,
            f"{screen_header('Создание анкеты', 'Шаг 5 из 8 — Страна')}\n"
            f"{progress_bar(4, 8, length=8)}\n\n"
            f"Напиши название своей страны в чат:",
            reply_markup=kb_form_nav(back_step="age"),
        )
        return
    await state.update_data(choosing_country=False, country=country)
    await ask_step_city(bot, call.from_user.id, state)


@router.message(ProfileForm.country, F.text.startswith("/"))
async def form_country_ignore_commands(message: Message, state: FSMContext, bot: Bot) -> None:
    pass


@router.message(ProfileForm.country)
async def form_country_manual(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_msg(message)
    data = await state.get_data()
    if not data.get("choosing_country"):
        return
    raw_country = (message.text or "").strip()
    if len(raw_country) < 2 or len(raw_country) > 40:
        await send_screen(
            bot,
            message.from_user.id,
            "⚠️ <b>Название страны должно быть от 2 до 40 символов.</b>",
            reply_markup=kb_form_nav(back_step="age"),
        )
        return
    await state.update_data(choosing_country=False, country=normalize_country(raw_country))
    await ask_step_city(bot, message.from_user.id, state)


@router.message(ProfileForm.city, F.text.startswith("/"))
async def form_city_ignore_commands(message: Message, state: FSMContext, bot: Bot) -> None:
    pass


@router.callback_query(ProfileForm.city, F.data == "form:city:manual")
async def form_city_manual_cb(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await call.answer()
    data = await state.get_data()
    if data.get("choosing_country"):
        await send_screen(
            bot,
            call.from_user.id,
            f"{screen_header('Создание анкеты', 'Шаг 5 из 8 — Страна')}\n"
            f"{progress_bar(4, 8, length=8)}\n\n"
            f"Напиши название своей страны в чат:",
            reply_markup=kb_form_nav(back_step="age"),
        )
    else:
        await send_screen(
            bot,
            call.from_user.id,
            f"{screen_header('Создание анкеты', 'Шаг 6 из 8 — Город')}\n"
            f"{progress_bar(5, 8, length=8)}\n\n"
            f"Напиши название своего города в чат:",
            reply_markup=kb_form_nav(back_step="country"),
        )


@router.callback_query(ProfileForm.city, F.data.startswith("form:city:"))
async def form_city_cb(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await call.answer()
    city = normalize_city(call.data.split(":", 2)[2])
    await state.update_data(city=city)
    await ask_step_interests(bot, call.from_user.id, state)


@router.message(ProfileForm.city)
async def form_city(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_msg(message)
    data = await state.get_data()
    if data.get("choosing_country"):
        raw_country = (message.text or "").strip()
        if len(raw_country) < 2 or len(raw_country) > 40:
            await send_screen(
                bot,
                message.from_user.id,
                "⚠️ <b>Название страны должно быть от 2 до 40 символов.</b>",
                reply_markup=kb_form_nav(back_step="age"),
            )
            return
        await state.update_data(choosing_country=False, country=normalize_country(raw_country))
        await ask_step_city(bot, message.from_user.id, state)
        return

    raw_city = (message.text or "").strip()
    if not Database.validate_city(raw_city):
        await send_screen(
            bot,
            message.from_user.id,
            "⚠️ <b>Название города должно быть от 2 до 40 символов и содержать только буквы, пробелы, дефис или апостроф:</b>",
            reply_markup=kb_form_nav(back_step="country"),
        )
        return
    await state.update_data(city=normalize_city(raw_city))
    await ask_step_interests(bot, message.from_user.id, state)


@router.callback_query(ProfileForm.interests, F.data.startswith("int_toggle:"))
async def form_interests_toggle(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    key = call.data.split(":")[1]
    data = await state.get_data()
    selected = parse_interests(str(data.get("interests", "")))
    if key in selected:
        selected.remove(key)
    else:
        if len(selected) >= 5:
            await call.answer("Можно выбрать максимум 5 интересов!", show_alert=True)
            return
        selected.add(key)

    await state.update_data(interests=",".join(selected))
    await call.answer()
    await ask_step_interests(bot, call.from_user.id, state)


@router.callback_query(ProfileForm.interests, F.data == "form:interests_done")
async def form_interests_done(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await call.answer()
    await ask_step_bio(bot, call.from_user.id, state)


@router.message(ProfileForm.bio)
async def form_bio(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_msg(message)
    bio = (message.text or "").strip()
    if not Database.validate_bio(bio):
        await send_screen(
            bot,
            message.from_user.id,
            "⚠️ <b>Описание должно быть от 10 до 500 символов.</b>\nНапиши чуть подробнее о себе:",
            reply_markup=kb_form_nav(back_step="interests"),
        )
        return
    await state.update_data(bio=bio)
    await ask_step_photo(bot, message.from_user.id, state)


async def finalize_profile_creation(
    bot: Bot,
    db: Database,
    user_id: int,
    username: Optional[str],
    data: dict,
    photo_id: Optional[str],
    media_type: str = "photo",
) -> None:
    is_new = db.upsert_profile(
        user_id=user_id,
        username=username,
        name=str(data["name"]),
        gender=str(data.get("gender", "M")),
        looking_for=str(data.get("looking_for", "A")),
        age=int(data["age"]),
        city=str(data["city"]),
        bio=str(data["bio"]),
        interests=str(data.get("interests", "")),
        photo_id=photo_id,
        media_type=media_type,
        country=str(data.get("country", "Россия")),
    )

    if is_new:
        inviter_id = db.pop_pending_referral(user_id)
        if inviter_id > 0:
            new_cnt, got_vip = db.reward_referral_signup(inviter_id, user_id)
            if new_cnt > 0:
                milestone_text = (
                    "\n👑 <b>СУПЕР-БОНУС:</b> Ты пригласил ещё 3-х друзей и получаешь <b>+24 часа VIP-статуса</b> и бонусное Супер-письмо!"
                    if got_vip
                    else ""
                )
                try:
                    await bot.send_message(
                        inviter_id,
                        f"🎉 <b>По твоей ссылке зарегистрировался новый друг ({esc(data['name'])})!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎁 Тебе начислено:\n"
                        f"• <b>+1 💌 Супер-письмо</b>\n"
                        f"• <b>+6 часов 🚀 ТОП-продвижения</b>\n"
                        f"👥 Всего приглашено друзей: <b>{new_cnt}</b>{milestone_text}",
                    )
                except Exception:
                    pass

    await render_home(bot, db, user_id, username=username)


@router.callback_query(ProfileForm.photo, F.data == "form:skip_photo")
async def form_skip_photo(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    await call.answer("Анкета сохранена!")
    data = await state.get_data()
    await state.clear()
    await finalize_profile_creation(bot, db, call.from_user.id, call.from_user.username, data, photo_id=None)


@router.message(ProfileForm.photo, F.photo)
async def form_photo_ok(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_msg(message)
    photo_id = message.photo[-1].file_id
    data = await state.get_data()
    await state.clear()
    await finalize_profile_creation(
        bot, db, message.from_user.id, message.from_user.username, data, photo_id=photo_id, media_type="photo"
    )


@router.message(ProfileForm.photo, F.video)
async def form_video_ok(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_msg(message)
    video_id = message.video.file_id
    data = await state.get_data()
    await state.clear()
    await finalize_profile_creation(
        bot, db, message.from_user.id, message.from_user.username, data, photo_id=video_id, media_type="video"
    )


@router.message(ProfileForm.photo)
async def form_photo_invalid(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_msg(message)
    await send_screen(
        bot,
        message.from_user.id,
        "⚠️ <b>Пожалуйста, отправь фотографию или видео</b> (либо нажми кнопку «Пропустить» ниже):",
        reply_markup=kb_form_nav(back_step="bio", allow_skip_photo=True),
    )


# =========================
# БЫСТРОЕ РЕДАКТИРОВАНИЕ ПО ЧАСТЯМ
# =========================
@router.callback_query(F.data == "edit:photo")
async def cb_edit_photo(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await call.answer()
    await state.set_state(EditProfileForm.waiting_photo)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить текущее фото/видео", callback_data="edit:remove_photo")],
            [InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data="settings:open")],
        ]
    )
    await send_screen(
        bot,
        call.from_user.id,
        "📸 <b>Смена фото или видео анкеты</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Отправь новую <b>фотографию</b> или <b>короткое видео</b> в чат:",
        reply_markup=kb,
    )


@router.callback_query(EditProfileForm.waiting_photo, F.data == "edit:remove_photo")
async def cb_edit_remove_photo(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    await state.clear()
    db.update_media(call.from_user.id, None, "photo")
    await call.answer("Медиа удалено!")
    await render_home(bot, db, call.from_user.id)


@router.message(EditProfileForm.waiting_photo, F.photo)
async def msg_edit_photo_ok(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_msg(message)
    await state.clear()
    db.update_media(message.from_user.id, message.photo[-1].file_id, "photo")
    await render_home(bot, db, message.from_user.id)


@router.message(EditProfileForm.waiting_photo, F.video)
async def msg_edit_video_ok(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_msg(message)
    await state.clear()
    db.update_media(message.from_user.id, message.video.file_id, "video")
    await render_home(bot, db, message.from_user.id)


@router.callback_query(F.data == "edit:bio")
async def cb_edit_bio(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await call.answer()
    await state.set_state(EditProfileForm.waiting_bio)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data="settings:open")]]
    )
    await send_screen(
        bot,
        call.from_user.id,
        "💬 <b>Изменение описания «О себе»</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Напиши новый текст о себе (от 10 до 500 символов) и отправь в чат:",
        reply_markup=kb,
    )


@router.message(EditProfileForm.waiting_bio)
async def msg_edit_bio_ok(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_msg(message)
    bio = (message.text or "").strip()
    if not Database.validate_bio(bio):
        await send_screen(
            bot,
            message.from_user.id,
            "⚠️ <b>Описание должно быть от 10 до 500 символов.</b> Попробуй ещё раз:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Отмена", callback_data="settings:open")]]
            ),
        )
        return
    await state.clear()
    db.update_bio(message.from_user.id, bio)
    await render_home(bot, db, message.from_user.id)


@router.callback_query(F.data == "edit:city")
async def cb_edit_city(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await call.answer()
    await state.set_state(EditProfileForm.waiting_city)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📍 Москва", callback_data="editcity:set:Москва"),
                InlineKeyboardButton(text="📍 Санкт-Петербург", callback_data="editcity:set:Санкт-Петербург"),
            ],
            [
                InlineKeyboardButton(text="📍 Екатеринбург", callback_data="editcity:set:Екатеринбург"),
                InlineKeyboardButton(text="📍 Новосибирск", callback_data="editcity:set:Новосибирск"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data="settings:open")],
        ]
    )
    await send_screen(
        bot,
        call.from_user.id,
        "🏙 <b>Смена города</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Выбери город кнопкой или напиши название своего города в чат:",
        reply_markup=kb,
    )


@router.callback_query(EditProfileForm.waiting_city, F.data.startswith("editcity:set:"))
async def cb_edit_city_set(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    await state.clear()
    city = call.data.split(":", 2)[2]
    db.update_city(call.from_user.id, city)
    await call.answer(f"Город изменён на {city}!")
    await render_home(bot, db, call.from_user.id)


@router.message(EditProfileForm.waiting_city)
async def msg_edit_city_ok(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_msg(message)
    raw = (message.text or "").strip()
    if not Database.validate_city(raw):
        await send_screen(
            bot,
            message.from_user.id,
            "⚠️ <b>Название города должно быть от 2 до 40 символов и содержать только буквы, пробелы, дефис или апостроф:</b>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Отмена", callback_data="settings:open")]]
            ),
        )
        return
    await state.clear()
    db.update_city(message.from_user.id, raw)
    await render_home(bot, db, message.from_user.id)


@router.callback_query(F.data == "edit:interests")
async def cb_edit_interests(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    uid = call.from_user.id
    p = db.get_profile(uid)
    if not p:
        return
    await call.answer()
    await state.set_state(EditProfileForm.waiting_interests)
    await state.update_data(edit_interests=str(p["interests"] or ""))
    selected = parse_interests(str(p["interests"] or ""))
    await send_screen(
        bot,
        uid,
        "🎯 <b>Редактирование твоих интересов</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Отметь до 5 близких тебе тем и нажми «🚀 Готово»:",
        reply_markup=kb_interests_selector(selected, done_callback="edit:interests_save", back_callback="settings:open"),
    )


@router.callback_query(EditProfileForm.waiting_interests, F.data.startswith("int_toggle:"))
async def cb_edit_interests_toggle(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    key = call.data.split(":")[1]
    data = await state.get_data()
    selected = parse_interests(str(data.get("edit_interests", "")))
    if key in selected:
        selected.remove(key)
    else:
        if len(selected) >= 5:
            await call.answer("Максимум 5 интересов!", show_alert=True)
            return
        selected.add(key)
    new_raw = ",".join(selected)
    await state.update_data(edit_interests=new_raw)
    await call.answer()
    await send_screen(
        bot,
        call.from_user.id,
        "🎯 <b>Редактирование твоих интересов</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Отметь до 5 близких тебе тем и нажми «🚀 Готово»:",
        reply_markup=kb_interests_selector(selected, done_callback="edit:interests_save", back_callback="settings:open"),
    )


@router.callback_query(EditProfileForm.waiting_interests, F.data == "edit:interests_save")
async def cb_edit_interests_save(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    data = await state.get_data()
    await state.clear()
    db.update_interests(call.from_user.id, str(data.get("edit_interests", "")))
    await call.answer("Интересы сохранены! 🎯")
    await render_home(bot, db, call.from_user.id)


@router.callback_query(F.data == "edit:agerange")
async def cb_edit_agerange(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    uid = call.from_user.id
    p = db.get_profile(uid)
    if not p or int(p["age"]) < 18:
        await call.answer("Доступно для анкет 18+", show_alert=True)
        return
    await call.answer()
    await state.set_state(EditProfileForm.waiting_age_range)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎂 18 – 23 года", callback_data="agerange:18:23"),
                InlineKeyboardButton(text="🎂 20 – 27 лет", callback_data="agerange:20:27"),
            ],
            [
                InlineKeyboardButton(text="🎂 24 – 32 года", callback_data="agerange:24:32"),
                InlineKeyboardButton(text="🎂 28 – 40 лет", callback_data="agerange:28:40"),
            ],
            [
                InlineKeyboardButton(text="🎂 35 – 55 лет", callback_data="agerange:35:55"),
                InlineKeyboardButton(text="🎂 18 – 80 (Все)", callback_data="agerange:18:80"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data="settings:open")],
        ]
    )
    await send_screen(
        bot,
        uid,
        f"🎂 <b>Возрастной диапазон поиска</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Сейчас выбран диапазон: <b>от {p['min_pref_age']} до {p['max_pref_age']} лет</b>.\n"
        f"Выбери подходящий возраст кнопкой ниже:",
        reply_markup=kb,
    )


@router.callback_query(EditProfileForm.waiting_age_range, F.data.startswith("agerange:"))
async def cb_edit_agerange_set(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    _, min_s, max_s = call.data.split(":")
    await state.clear()
    db.update_age_range(call.from_user.id, int(min_s), int(max_s))
    await call.answer(f"Возраст поиска: {min_s}–{max_s} лет ✅")
    await render_home(bot, db, call.from_user.id)


@router.callback_query(F.data == "edit:prefs")
async def cb_edit_prefs(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await call.answer()
    await state.set_state(EditProfileForm.waiting_gender)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👨 Я парень", callback_data="editprefs:gender:M"),
                InlineKeyboardButton(text="👩 Я девушка", callback_data="editprefs:gender:F"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data="settings:open")],
        ]
    )
    await send_screen(
        bot,
        call.from_user.id,
        "🚻 <b>Изменение пола и предпочтений</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Шаг 1 из 2: Укажи свой пол:",
        reply_markup=kb,
    )


@router.callback_query(EditProfileForm.waiting_gender, F.data.startswith("editprefs:gender:"))
async def cb_edit_prefs_gender(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await call.answer()
    gender = call.data.split(":")[2]
    await state.update_data(edit_gender=gender)
    await state.set_state(EditProfileForm.waiting_looking_for)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👩 Ищу девушек", callback_data="editprefs:looking:F"),
                InlineKeyboardButton(text="👨 Ищу парней", callback_data="editprefs:looking:M"),
            ],
            [InlineKeyboardButton(text="👥 Ищу всех (Дружба и общение)", callback_data="editprefs:looking:A")],
            [InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data="settings:open")],
        ]
    )
    await send_screen(
        bot,
        call.from_user.id,
        "🔍 <b>Изменение пола и предпочтений</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Шаг 2 из 2: Кого ты хочешь видеть в поиске?",
        reply_markup=kb,
    )


@router.callback_query(EditProfileForm.waiting_looking_for, F.data.startswith("editprefs:looking:"))
async def cb_edit_prefs_looking(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    looking = call.data.split(":")[2]
    data = await state.get_data()
    gender = str(data.get("edit_gender", "M"))
    await state.clear()
    db.update_preferences(call.from_user.id, gender, looking)
    await call.answer("Предпочтения поиска обновлены! ✅")
    await render_home(bot, db, call.from_user.id)


# =========================
# ПРОСМОТР АНКЕТ И ЛАЙКИ
# =========================
async def show_next_candidate(bot: Bot, db: Database, user_id: int) -> None:
    me = db.get_profile(user_id)
    if not me:
        await render_home(bot, db, user_id)
        return
    if int(me["banned"] or 0) == 1:
        await render_home(bot, db, user_id)
        return

    db.touch_user(user_id)
    await send_chat_action(bot, user_id, "typing")
    await asyncio.sleep(0.4)  # небольшая задержка для эффекта «подбора анкеты»
    candidate = db.find_candidate(user_id, city_prioritized=True)
    if not candidate:
        kb_rows: List[List[InlineKeyboardButton]] = [
            [InlineKeyboardButton(text="🔄 Смотреть пропущенные анкеты заново", callback_data="browse:free_reset")],
            [InlineKeyboardButton(text="🚻 Изменить фильтр поиска", callback_data="settings:open")],
            [InlineKeyboardButton(text="🎁 Пригласить друзей (+VIP)", callback_data="ref:menu")],
            [InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data="menu:home")],
        ]

        await send_screen(
            bot,
            user_id,
            "🔍 <b>Новые анкеты по твоим параметрам пока закончились!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Ты просмотрел всех доступных участников.\n\n"
            "💡 <i>Нажми кнопку ниже, чтобы сбросить пропущенные анкеты («Дальше 👎») и посмотреть их снова!</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        )
        return

    text = format_profile_card(candidate, title="Найдена анкета", viewer=me)
    await send_screen(
        bot,
        user_id,
        text,
        reply_markup=kb_candidate(int(candidate["user_id"])),
        photo_id=candidate["photo_id"] if candidate["photo_id"] else None,
        media_type=str(candidate["media_type"] or "photo"),
    )


@router.callback_query(F.data == "browse:next")
async def cb_browse_next(call: CallbackQuery, db: Database, bot: Bot) -> None:
    await call.answer()
    await send_transition_sticker(bot, call.from_user.id, "browse")
    await show_next_candidate(bot, db, call.from_user.id)


@router.callback_query(F.data == "browse:free_reset")
async def cb_browse_free_reset(call: CallbackQuery, db: Database, bot: Bot) -> None:
    cnt = db.reset_user_skips(call.from_user.id)
    await call.answer(f"Пропущенные анкеты восстановлены ({cnt} шт.)! 🔄")
    await show_next_candidate(bot, db, call.from_user.id)


@router.callback_query(F.data.startswith("react:"))
async def cb_react(call: CallbackQuery, db: Database, bot: Bot) -> None:
    user_id = call.from_user.id
    me = db.get_profile(user_id)
    if not me or int(me["banned"] or 0) == 1:
        await call.answer()
        await render_home(bot, db, user_id)
        return

    _, action, raw_target = call.data.split(":", 2)
    target_id = int(raw_target)
    target = db.get_profile(target_id)

    if not target or int(target["active"]) == 0 or int(target["banned"] or 0) == 1:
        await call.answer("Эта анкета больше не активна.")
        await show_next_candidate(bot, db, user_id)
        return

    if (int(me["age"]) < 18) != (int(target["age"]) < 18):
        await call.answer("Анкета недоступна по возрастному ограничению.", show_alert=True)
        await show_next_candidate(bot, db, user_id)
        return

    if action == "skip":
        db.record_reaction(user_id, target_id, "skip")
        await call.answer("Дальше 👎")
        await send_sticker(bot, user_id, "skip")
        await show_next_candidate(bot, db, user_id)
        return

    if action == "like":
        await send_transition_sticker(bot, user_id, "like")
        if not db.is_vip(user_id) and db.count_today_likes(user_id) >= FREE_DAILY_LIKES:
            await call.answer("Дневной лимит бесплатных лайков исчерпан!", show_alert=True)
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="👑 Подключить VIP (Безлимит)", callback_data="shop:buy:vip_7")],
                    [InlineKeyboardButton(text="🎁 Получить VIP за друзей", callback_data="ref:menu")],
                    [InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data="menu:home")],
                ]
            )
            await send_screen(
                bot,
                user_id,
                f"🔥 <b>Ты поставил максимум бесплатных лайков за сегодня ({FREE_DAILY_LIKES} шт.)!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "Чтобы ставить лайки без ограничений и сразу видеть всех, кому ты понравился, подключи <b>👑 VIP-статус</b> или пригласи друзей!",
                reply_markup=kb,
            )
            return

        matched = db.atomic_register_like(user_id, target_id)

        if matched:
            await send_chat_action(bot, user_id, "typing")
            await send_sticker(bot, user_id, "match")
            my_link = (
                f"@{esc(me['username'])}"
                if me["username"]
                else f"<a href='tg://user?id={user_id}'>{esc(me['name'])}</a>"
            )
            target_link = (
                f"@{esc(target['username'])}"
                if target["username"]
                else f"<a href='tg://user?id={target_id}'>{esc(target['name'])}</a>"
            )
            compat_score, _ = calc_compatibility(me, target)

            match_box_target = screen_box("💬 Что дальше?", [
                stat_row("🎲", "Ледокол", "Отправь случайный вопрос для старта"),
                stat_row("✍️", "Сообщение", "Напиши своё через бота"),
            ])

            try:
                await bot.send_message(
                    target_id,
                    f"{celebration_line('💞 У тебя взаимный Мэтч!')}\n"
                    f"{random_divider()}\n"
                f"Ты и <b>{esc(me['name'])}</b> ({me['age']}, {esc(me['city'])}) понравились друг другу!\n"
                f"🔥 Ваша совместимость: <b>{compat_score}%</b>\n"
                f"🔗 Контакт в Telegram: {my_link}\n\n"
                f"{match_box_target}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text=f"💬 Написать {me['name']}", callback_data=f"chat:write:{user_id}")],
                        [InlineKeyboardButton(text="🎲 Отправить Ледокол (Идея для старта)", callback_data=f"chat:ice:{user_id}")],
                        [InlineKeyboardButton(text="🔥 Продолжить поиск", callback_data="browse:next")],
                    ]
                ),
                )
            except Exception:
                pass

            await call.answer("Взаимная симпатия! 💞")

            match_box_user = screen_box("💬 Что дальше?", [
                stat_row("🎲", "Ледокол", "Отправь случайный вопрос для старта"),
                stat_row("✍️", "Сообщение", "Напиши своё через бота"),
                stat_row("🔥", "Поиск", "Продолжи смотреть анкеты"),
            ])

            await send_screen(
                bot,
                user_id,
                f"{celebration_line('💞 Взаимный Мэтч!')}\n"
                f"{random_divider()}\n"
                f"Ты понравился(ась) <b>{esc(target['name'])}</b> ({target['age']}, {esc(target['city'])})!\n"
                f"🔥 Ваша совместимость: <b>{compat_score}%</b>\n"
                f"🔗 Контакт в Telegram: {target_link}\n\n"
                f"{match_box_user}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text=f"💬 Написать {target['name']} через бота", callback_data=f"chat:write:{target_id}")],
                        [InlineKeyboardButton(text="🎲 Отправить Ледокол (Случайный вопрос)", callback_data=f"chat:ice:{target_id}")],
                        [InlineKeyboardButton(text="🔥 Смотреть анкеты дальше", callback_data="browse:next")],
                        [InlineKeyboardButton(text="⬅️ В главное меню", callback_data="menu:home")],
                    ]
                ),
                photo_id=target["photo_id"] if target["photo_id"] else None,
                media_type=str(target["media_type"] or "photo"),
            )
            return
        else:
            try:
                await bot.send_message(
                    target_id,
                    "🔥 <b>Кому-то понравилась твоя анкета!</b>\n"
                    "Загляни в раздел «👀 Кто меня лайкнул» или продолжай смотреть анкеты!",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="👀 Посмотреть, кто лайкнул", callback_data="likes:list")],
                            [InlineKeyboardButton(text="🔥 Смотреть анкеты", callback_data="browse:next")],
                        ]
                    ),
                )
            except Exception:
                pass

            await call.answer("Лайк отправлен ❤️")
            await send_sticker(bot, user_id, "like")
            await hearts_animation(bot, user_id, count=3)
            await show_next_candidate(bot, db, user_id)


# =========================
# ВСТРОЕННЫЙ ЧАТ И "ЛЕДОКОЛЫ"
# =========================
@router.callback_query(F.data.startswith("chat:ice:"))
async def cb_chat_icebreaker(call: CallbackQuery, db: Database, bot: Bot) -> None:
    uid = call.from_user.id
    me = db.get_profile(uid)
    target_id = int(call.data.split(":")[2])
    target = db.get_profile(target_id)

    if not me or not target or db.is_blocked_between(uid, target_id):
        await call.answer("Недоступно.", show_alert=True)
        return

    if (int(me["age"]) < 18) != (int(target["age"]) < 18):
        await call.answer("Переписка недоступна по возрастному ограничению.", show_alert=True)
        return

    # Анимация броска кубика перед ледоколом
    await dice_animation(bot, uid, duration_seconds=1.2)

    ice_text = random.choice(ICEBREAKERS)
    db.log_chat_message(uid, target_id, ice_text)
    my_link = f" (@{esc(me['username'])})" if me["username"] else ""

    try:
        await bot.send_message(
            target_id,
            f"💬 <b>Сообщение от {esc(me['name'])}{my_link}</b> ({me['age']}, {esc(me['city'])}):\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<blockquote>{esc(ice_text)}</blockquote>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=f"↩️ Ответить {me['name']}", callback_data=f"chat:write:{uid}")],
                    [InlineKeyboardButton(text="🎲 Ответить своим Ледоколом", callback_data=f"chat:ice:{uid}")],
                ]
            ),
        )
    except Exception:
        await call.answer("Не удалось доставить сообщение.", show_alert=True)
        return

    await call.answer("🎲 Ледокол отправлен!")
    await send_screen(
        bot,
        uid,
        f"✅ <b>Ты отправил(а) вопрос-ледокол для {esc(target['name'])}:</b>\n\n"
        f"<blockquote>{esc(ice_text)}</blockquote>\n\n"
        f"<i>Как только собеседник ответит, сообщение придёт прямо сюда!</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"✍️ Написать ещё {target['name']}", callback_data=f"chat:write:{target_id}")],
                [InlineKeyboardButton(text="🔥 Смотреть анкеты", callback_data="browse:next")],
                [InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("chat:write:"))
async def cb_chat_write(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    uid = call.from_user.id
    me = db.get_profile(uid)
    if not me:
        await call.answer("Сначала создай анкету!", show_alert=True)
        return

    target_id = int(call.data.split(":")[2])
    target = db.get_profile(target_id)
    if not target or int(target["active"]) == 0 or int(target["banned"] or 0) == 1:
        await call.answer("Пользователь больше не активен.", show_alert=True)
        return
    await send_transition_sticker(bot, uid, "message")

    if db.is_blocked_between(uid, target_id):
        await call.answer("Переписка с этим пользователем недоступна (ЧС).", show_alert=True)
        return

    if (int(me["age"]) < 18) != (int(target["age"]) < 18):
        await call.answer("Переписка недоступна по возрастному ограничению.", show_alert=True)
        return

    await call.answer()
    await state.set_state(ChatMsgForm.waiting_text)
    await state.update_data(chat_target_id=target_id)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎲 Отправить случайный вопрос (Ледокол)", callback_data=f"chat:ice:{target_id}")],
            [
                InlineKeyboardButton(text="💌 К списку мэтчей", callback_data="matches:list"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="menu:home"),
            ],
        ]
    )
    await send_screen(
        bot,
        uid,
        f"💬 <b>Сообщение для {esc(target['name'])} ({target['age']}, {esc(target['city'])})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Напиши своё сообщение в чат (или нажми кнопку «🎲 Ледокол» ниже):",
        reply_markup=kb,
    )


@router.message(ChatMsgForm.waiting_text)
async def msg_chat_send(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_msg(message)
    uid = message.from_user.id
    text = (message.text or "").strip()

    if len(text) < 1 or len(text) > 600:
        await send_screen(
            bot,
            uid,
            "⚠️ <b>Сообщение должно быть от 1 до 600 символов.</b> Напиши ещё раз:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="matches:list")]]
            ),
        )
        return

    data = await state.get_data()
    target_id = int(data.get("chat_target_id", 0))
    await state.clear()

    me = db.get_profile(uid)
    target = db.get_profile(target_id)
    if not me or not target or db.is_blocked_between(uid, target_id):
        await render_home(bot, db, uid)
        return

    db.log_chat_message(uid, target_id, text)
    my_link = f" (@{esc(me['username'])})" if me["username"] else ""

    delivered = False
    try:
        await bot.send_message(
            target_id,
            f"💬 <b>Новое сообщение от {esc(me['name'])}{my_link}</b> ({me['age']}, {esc(me['city'])}):\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<blockquote>{esc(text)}</blockquote>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=f"↩️ Ответить {me['name']}", callback_data=f"chat:write:{uid}")],
                    [InlineKeyboardButton(text="🚫 Заблокировать", callback_data=f"safety:block:{uid}")],
                ]
            ),
        )
        delivered = True
    except Exception:
        delivered = False

    status_msg = (
        f"✅ <b>Сообщение для {esc(target['name'])} успешно доставлено!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Твой текст:\n<blockquote>{esc(text)}</blockquote>"
        if delivered
        else "⚠️ <b>Не удалось доставить сообщение:</b> пользователь остановил бота."
    )

    await send_screen(
        bot,
        uid,
        status_msg,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"✍️ Написать ещё {target['name']}", callback_data=f"chat:write:{target_id}")],
                [InlineKeyboardButton(text="💌 Мои мэтчи", callback_data="matches:list")],
                [InlineKeyboardButton(text="🔥 Смотреть анкеты", callback_data="browse:next")],
                [InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home")],
            ]
        ),
    )


# =========================
# КТО МЕНЯ ЛАЙКНУЛ (VIP-ФУНКЦИЯ)
# =========================
@router.callback_query(F.data == "likes:list")
async def cb_likes_list(call: CallbackQuery, db: Database, bot: Bot) -> None:
    uid = call.from_user.id
    me = db.get_profile(uid)
    if not me:
        await call.answer()
        await render_home(bot, db, uid)
        return

    admirers = db.get_who_liked_me(uid)
    if not admirers:
        await call.answer()
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔥 Смотреть анкеты", callback_data="browse:next")],
                [InlineKeyboardButton(text="🚀 Поднять анкету в ТОП", callback_data="shop:buy:boost_24")],
                [InlineKeyboardButton(text="🎁 Бесплатный VIP и ТОП", callback_data="ref:menu")],
                [InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data="menu:home")],
            ]
        )
        await send_screen(
            bot,
            uid,
            "👀 <b>Новых входящих лайков пока нет</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Лайкай других участников первым или подними свою анкету в 🚀 <b>ТОП</b>, чтобы получать в 10 раз больше внимания!",
            reply_markup=kb,
        )
        return

    is_vip = db.is_vip(uid)
    is_adult = int(me["age"]) >= 18

    if not is_vip and is_adult and PAYMENTS_ENABLED:
        await call.answer()
        count = len(admirers)
        cities = list({esc(a["city"]) for a in admirers[:3]})
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="👑 Открыть список (Подключить VIP)", callback_data="shop:buy:vip_7")],
                [InlineKeyboardButton(text="🎁 Получить VIP бесплатно за друзей", callback_data="ref:menu")],
                [InlineKeyboardButton(text="🔥 Угадать в общем поиске", callback_data="browse:next")],
                [InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data="menu:home")],
            ]
        )
        cities_box = screen_box("📍 Города поклонников", [
            stat_row("🏙", "Города", ", ".join(cities)),
        ])
        await send_screen(
            bot,
            uid,
            f"{screen_header('Кто меня лайкнул', f'{count} человек')}\n"
            f"{cities_box}\n\n"
            f"🔒 <i>Прямой просмотр списка доступен с 👑 VIP-статусом!</i>\n"
            f"Подключи VIP в магазине или пригласи друзей, чтобы открыть все анкеты!",
            reply_markup=kb,
        )
        return

    await call.answer()
    first = admirers[0]
    text = format_profile_card(first, title=f"Тебя лайкнули! (Ещё {len(admirers)} чел.)", viewer=me)
    await send_screen(
        bot,
        uid,
        text,
        reply_markup=kb_candidate(int(first["user_id"])),
        photo_id=first["photo_id"] if first["photo_id"] else None,
        media_type=str(first["media_type"] or "photo"),
    )


# =========================
# СУПЕР-ПИСЬМО ("НАПИСАТЬ СРАЗУ")
# =========================
@router.callback_query(F.data.startswith("super:ask:"))
async def cb_super_ask(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    uid = call.from_user.id
    me = db.get_profile(uid)
    if not me:
        await call.answer()
        return

    target_id = int(call.data.split(":")[2])
    target = db.get_profile(target_id)
    if not target or int(target["active"]) == 0 or int(target["banned"] or 0) == 1:
        await call.answer("Пользователь недоступен.", show_alert=True)
        return

    if (int(me["age"]) < 18) != (int(target["age"]) < 18):
        await call.answer("Супер-письмо недоступно по возрастному ограничению.", show_alert=True)
        return

    if int(me["superlikes"]) <= 0:
        await call.answer("У тебя закончились Супер-письма!", show_alert=True)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💌 Купить 5 Супер-писем", callback_data="shop:buy:super_5")],
                [InlineKeyboardButton(text="🎁 Получить бесплатно за друга", callback_data="ref:menu")],
                [InlineKeyboardButton(text="⬅️ Вернуться к анкетам", callback_data="browse:next")],
            ]
        )
        await send_screen(
            bot,
            uid,
            "💌 <b>У тебя 0 Супер-писем!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Супер-письмо позволяет отправить личное сообщение и твой контакт любому человеку сразу, без ожидания взаимного лайка.\n\n"
            "Пополни баланс в магазине или пригласи друга (за каждого друга даётся <b>+1 Супер-письмо</b> бесплатно)!",
            reply_markup=kb,
        )
        return

    await call.answer()
    await state.set_state(SuperMsgForm.waiting_text)
    await state.update_data(target_id=target_id)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Назад к анкетам", callback_data="browse:next"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="menu:home"),
            ]
        ]
    )
    super_msg_box = screen_box("📝 Инструкция", [
        stat_row("✍️", "Сообщение", "Напиши текст, который доставим адресату"),
        stat_row("🚀", "Доставка", "Мгновенно, без ожидания взаимного лайка"),
    ])
    superlikes_count = me["superlikes"]
    await send_screen(
        bot,
        uid,
        f"{screen_header('Супер-письмо', f'Доступно: {superlikes_count} шт.')}\n"
        f"{super_msg_box}\n\n"
        f"Отправь сообщение в чат:",
        reply_markup=kb,
    )


@router.message(SuperMsgForm.waiting_text)
async def msg_super_send(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_msg(message)
    uid = message.from_user.id
    text = (message.text or "").strip()

    if len(text) < 2 or len(text) > 400:
        await send_screen(
            bot,
            uid,
            "⚠️ <b>Сообщение должно быть от 2 до 400 символов.</b> Напиши ещё раз:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="browse:next")]]
            ),
        )
        return

    data = await state.get_data()
    target_id = int(data.get("target_id", 0))
    await state.clear()

    me = db.get_profile(uid)
    target = db.get_profile(target_id)
    if not me or not target or not db.use_superlike(uid):
        await render_home(bot, db, uid)
        return

    db.record_reaction(uid, target_id, "like")
    my_link = f"@{esc(me['username'])}" if me["username"] else f"<a href='tg://user?id={uid}'>{esc(me['name'])}</a>"

    try:
        await bot.send_message(
            target_id,
            f"💌 <b>Тебе пришло Супер-письмо!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"От: <b>{esc(me['name'])}</b> ({me['age']} лет, {esc(me['city'])})\n\n"
            f"💬 <b>Сообщение:</b>\n"
            f"<blockquote>{esc(text)}</blockquote>\n\n"
            f"🔗 Профиль: {my_link}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=f"💬 Ответить {me['name']} через бота", callback_data=f"chat:write:{uid}")],
                    [InlineKeyboardButton(text="❤️ Ответить взаимностью (Мэтч)", callback_data=f"react:like:{uid}")],
                ]
            ),
        )
    except Exception:
        pass

    await show_next_candidate(bot, db, uid)


# =========================
# МЭТЧИ, МОДЕРАЦИЯ ЖАЛОБ И НАСТРОЙКИ
# =========================
@router.callback_query(F.data == "matches:list")
async def cb_matches_list(call: CallbackQuery, db: Database, bot: Bot) -> None:
    await call.answer()
    await send_sticker(bot, call.from_user.id, "match")
    uid = call.from_user.id
    matches = db.list_matches(uid, limit=15)

    kb_rows: List[List[InlineKeyboardButton]] = []

    if not matches:
        text = (
            "💌 <b>У тебя пока нет взаимных симпатий (мэтчей).</b>\n"
            f"{random_divider()}\n"
            "Ставь лайки в разделе «Смотреть анкеты», и как только симпатия совпадёт — здесь появится список людей и кнопки для переписки!"
        )
    else:
        lines = [
            f"{screen_header('Твои Мэтчи', 'Взаимные симпатии')}\n",
            "<i>Нажми на кнопку с именем ниже, чтобы написать или отправить ледокол:</i>\n",
        ]
        for m in matches:
            mid = int(m["user_id"])
            link = f"@{esc(m['username'])}" if m["username"] else "пиши кнопкой ниже"
            lines.append(
                f"💌 <b>{esc(m['name'])}</b>, {m['age']} ({esc(m['city'])})\n"
                f"   🔗 {link}"
            )
            kb_rows.append(
                [
                    InlineKeyboardButton(text=f"💬 Написать {m['name']}", callback_data=f"chat:write:{mid}"),
                    InlineKeyboardButton(text="🎲 Ледокол", callback_data=f"chat:ice:{mid}"),
                ]
            )
        text = "\n".join(lines)
        text += f"\n{screen_footer('Всего мэтчей: ' + str(len(matches)))}"

    kb_rows.append([InlineKeyboardButton(text="🔥 Смотреть анкеты", callback_data="browse:next")])
    kb_rows.append([InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data="menu:home")])

    await send_screen(bot, uid, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@router.callback_query(F.data.startswith("chat:history:"))
async def cb_chat_history(call: CallbackQuery, db: Database, bot: Bot) -> None:
    uid = call.from_user.id
    partner_id = int(call.data.split(":")[2])
    partner = db.get_profile(partner_id)
    if not partner:
        await call.answer("Пользователь не найден.", show_alert=True)
        return

    messages = db.get_chat_history(uid, partner_id, limit=30)
    messages = list(reversed(messages))

    partner_age = partner["age"]
    lines = [
        f"{screen_header('Чат', esc(partner['name']) + f', {partner_age}')}\n",
        f"<i>Переписка с {esc(partner['name'])} ({esc(partner['city'])})</i>\n",
        random_divider(),
    ]

    if not messages:
        lines.append("<i>Пока нет сообщений. Начните общение!</i>")
    else:
        for msg in messages[-20:]:
            is_me = int(msg["sender_id"]) == uid
            prefix = "🟢 Ты:" if is_me else f"💌 {esc(partner['name'])}:"
            text_content = esc(msg["text"] or "")
            if msg["media_type"] == "photo":
                text_content = "[📷 Фото]"
            elif msg["media_type"] == "video":
                text_content = "[🎥 Видео]"
            lines.append(f"\n{prefix} {text_content}")

    kb_rows = [
        [
            InlineKeyboardButton(text="✍️ Написать сообщение", callback_data=f"chat:write:{partner_id}"),
            InlineKeyboardButton(text="🎲 Отправить Ледокол", callback_data=f"chat:ice:{partner_id}"),
        ],
        [
            InlineKeyboardButton(text="📸 Галерея фото", callback_data=f"gallery:view:{partner_id}"),
        ],
        [
            InlineKeyboardButton(text="💌 К списку мэтчей", callback_data="matches:list"),
            InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="menu:home"),
        ],
    ]

    await send_screen(
        bot,
        uid,
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )


@router.callback_query(F.data.startswith("safety:"))
async def cb_safety(call: CallbackQuery, db: Database, bot: Bot) -> None:
    uid = call.from_user.id
    _, kind, raw_target = call.data.split(":", 2)
    target_id = int(raw_target)

    if kind == "block":
        db.block_user(uid, target_id)
        db.record_reaction(uid, target_id, "skip")
        await call.answer("Пользователь добавлен в ЧС 🚫")
        await send_sticker(bot, uid, "bye")
    elif kind == "report":
        # Жалоба не авто-блокирует, только фиксируется и уходит админу
        db.report_user(uid, target_id)
        await send_sticker(bot, uid, "error")
        if ADMIN_ID > 0:
            target_p = db.get_profile(target_id)
            if target_p:
                mod_kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="🚫 Забанить нарушителя", callback_data=f"adminmod:ban:{target_id}")],
                        [InlineKeyboardButton(text="✅ Оставить анкету", callback_data="adminmod:ignore")],
                    ]
                )
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        f"⚠️ <b>Жалоба на анкету!</b>\n"
                        f"Отправитель жалобы: <code>{uid}</code>\n"
                        f"Нарушитель: <code>{target_id}</code> (@{esc(target_p['username'] or 'нет')})\n\n"
                        f"{format_profile_card(target_p, title='Подозрительная анкета')}",
                        reply_markup=mod_kb,
                    )
                except Exception:
                    pass
        await call.answer("Жалоба отправлена модераторам ⚠️")

    await show_next_candidate(bot, db, uid)


@router.callback_query(F.data.startswith("adminmod:"))
async def cb_admin_moderation(call: CallbackQuery, db: Database) -> None:
    if call.from_user.id != ADMIN_ID:
        return
    parts = call.data.split(":")
    action = parts[1]
    if action == "ignore":
        await call.answer("Жалоба отклонена")
        try:
            await call.message.edit_text("✅ Жалоба рассмотрена (анкета оставлена).")
        except Exception:
            pass
        return

    target_uid = int(parts[2])
    if action == "ban":
        db.set_banned(target_uid, True)
        await call.answer("Пользователь заблокирован в боте! 🚫", show_alert=True)
        try:
            await call.message.edit_text(f"🚫 Пользователь <code>{target_uid}</code> заблокирован навсегда.")
        except Exception:
            pass
    elif action == "unverify":
        db.set_verified(target_uid, False)
        await call.answer("Галочка верификации снята!", show_alert=True)


@router.callback_query(F.data == "settings:open")
async def cb_settings_open(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    await state.clear()
    uid = call.from_user.id
    p = db.get_profile(uid)
    if not p:
        await call.answer()
        await render_home(bot, db, uid)
        return
    await call.answer()
    await send_transition_sticker(bot, uid, "settings")
    active = int(p["active"]) == 1
    is_verified = int(p["verified"] or 0) == 1
    is_adult = int(p["age"]) >= 18
    status_str = "🟢 Видна в поиске" if active else "⏸ Скрыта"
    gender_str = format_gender_label(str(p["gender"] or "M"))
    looking_str = format_looking_for_label(str(p["looking_for"] or "A"))

    settings_box = screen_box("📋 Основное", [
        stat_row("📡", "Статус", status_str, highlight=True),
        stat_row("🚻", "Твой пол", gender_str),
        stat_row("🔍", "Кого ищешь", f"{looking_str} ({p['min_pref_age']}–{p['max_pref_age']} лет)"),
        stat_row("📍", "Город", esc(p['city'])),
        stat_row("🎯", "Интересы", format_interests_list(str(p['interests'] or ''))),
    ])
    await send_screen(
        bot,
        uid,
        f"{screen_header('Настройки анкеты', 'Фильтры и параметры')}\n"
        f"{settings_box}\n\n"
        f"👇 <i>Выбери, что хочешь настроить:</i>",
        reply_markup=kb_settings(active, is_verified, is_adult),
    )


@router.callback_query(F.data == "settings:toggle_active")
async def cb_settings_toggle(call: CallbackQuery, db: Database, bot: Bot) -> None:
    uid = call.from_user.id
    p = db.get_profile(uid)
    if not p:
        await call.answer()
        return
    new_active = not (int(p["active"]) == 1)
    db.set_active(uid, new_active)
    await call.answer("Статус анкеты изменён!")
    p = db.get_profile(uid) or p
    is_verified = int(p["verified"] or 0) == 1
    is_adult = int(p["age"]) >= 18
    status_str = "🟢 Видна в поиске" if new_active else "⏸ Скрыта"
    gender_str = format_gender_label(str(p["gender"] or "M"))
    looking_str = format_looking_for_label(str(p["looking_for"] or "A"))

    settings_box = screen_box("📋 Основное", [
        stat_row("📡", "Статус", status_str, highlight=True),
        stat_row("🚻", "Твой пол", gender_str),
        stat_row("🔍", "Кого ищешь", f"{looking_str} ({p['min_pref_age']}–{p['max_pref_age']} лет)"),
        stat_row("📍", "Город", esc(p['city'])),
        stat_row("🎯", "Интересы", format_interests_list(str(p['interests'] or ''))),
    ])
    await send_screen(
        bot,
        uid,
        f"{screen_header('Настройки анкеты', 'Фильтры и параметры')}\n"
        f"{settings_box}\n\n"
        f"👇 <i>Выбери, что хочешь настроить:</i>",
        reply_markup=kb_settings(new_active, is_verified, is_adult),
    )


# =========================
# ФИЛЬТРЫ ПОИСКА
# =========================
@router.callback_query(F.data == "filters:menu")
async def cb_filters_menu(call: CallbackQuery, db: Database, bot: Bot) -> None:
    uid = call.from_user.id
    p = db.get_profile(uid)
    if not p:
        await call.answer("Сначала создай анкету!", show_alert=True)
        return

    await call.answer()
    min_age = int(p["min_pref_age"] or 18)
    max_age = int(p["max_pref_age"] or 45)
    looking = str(p["looking_for"] or "A")
    looking_label = format_looking_for_label(looking)
    city_name = esc(p["city"])

    text = (
        f"{screen_header('Фильтры поиска', 'Кого показывать в анкетах')}\n\n"
        f"📋 <b>Текущие фильтры:</b>\n"
        f"{stat_row('🎂', 'Возраст', f'{min_age}–{max_age} лет')}\n"
        f"{stat_row('🚻', 'Кого ищешь', looking_label)}\n"
        f"{stat_row('📍', 'Город', f'Твой город ({city_name})')}\n\n"
        f"<i>Измени фильтры, чтобы видеть более подходящие анкеты.</i>"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"🔻 Мин. возраст: {min_age}", callback_data="filter:agemin"),
                InlineKeyboardButton(text=f"🔺 Макс. возраст: {max_age}", callback_data="filter:agemax"),
            ],
            [
                InlineKeyboardButton(text="🚻 Кого ищешь", callback_data="edit:prefs"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data="settings:open"),
            ],
        ]
    )
    await send_screen(bot, uid, text, reply_markup=kb)


@router.callback_query(F.data == "filter:agemin")
async def cb_filter_agemin(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await call.answer()
    await state.set_state(FilterForm.waiting_age_min)
    await send_screen(
        bot,
        call.from_user.id,
        "🔻 <b>Минимальный возраст поиска</b>\n\n"
        "Введи число от 14 до 80:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="filters:menu")]]
        ),
    )


@router.callback_query(F.data == "filter:agemax")
async def cb_filter_agemax(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await call.answer()
    await state.set_state(FilterForm.waiting_age_max)
    await send_screen(
        bot,
        call.from_user.id,
        "🔺 <b>Максимальный возраст поиска</b>\n\n"
        "Введи число от 14 до 80:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="filters:menu")]]
        ),
    )


@router.message(FilterForm.waiting_age_min)
async def form_filter_age_min(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_msg(message)
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (14 <= int(raw) <= 80):
        await send_screen(
            bot,
            message.from_user.id,
            "⚠️ <b>Введи корректный возраст числом от 14 до 80:</b>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="filters:menu")]]
            ),
        )
        return

    uid = message.from_user.id
    p = db.get_profile(uid)
    max_age = int(p["max_pref_age"] or 45)
    if int(raw) > max_age:
        await send_screen(
            bot,
            uid,
            f"⚠️ Минимальный возраст не может быть больше максимального ({max_age}).",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="filters:menu")]]
            ),
        )
        return

    with db._session() as conn:
        conn.execute("UPDATE profiles SET min_pref_age = ? WHERE user_id = ?", (int(raw), uid))

    await state.clear()
    await send_screen(
        bot,
        uid,
        f"✅ Минимальный возраст поиска установлен: {raw} лет",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ К фильтрам", callback_data="filters:menu")]]
        ),
    )


@router.message(FilterForm.waiting_age_max)
async def form_filter_age_max(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_msg(message)
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (14 <= int(raw) <= 80):
        await send_screen(
            bot,
            message.from_user.id,
            "⚠️ <b>Введи корректный возраст числом от 14 до 80:</b>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="filters:menu")]]
            ),
        )
        return

    uid = message.from_user.id
    p = db.get_profile(uid)
    min_age = int(p["min_pref_age"] or 18)
    if int(raw) < min_age:
        await send_screen(
            bot,
            uid,
            f"⚠️ Максимальный возраст не может быть меньше минимального ({min_age}).",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="filters:menu")]]
            ),
        )
        return

    with db._session() as conn:
        conn.execute("UPDATE profiles SET max_pref_age = ? WHERE user_id = ?", (int(raw), uid))

    await state.clear()
    await send_screen(
        bot,
        uid,
        f"✅ Максимальный возраст поиска установлен: {raw} лет",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ К фильтрам", callback_data="filters:menu")]]
        ),
    )


# =========================
# МАГАЗИН И ОПЛАТА ЮMONEY
# =========================
async def complete_and_reward_order(bot: Bot, db: Database, order: sqlite3.Row, charge_source: str) -> str:
    uid = int(order["user_id"])
    prod_key = str(order["product"])
    db.mark_order_paid(str(order["order_id"]), charge_source)
    await send_transition_sticker(bot, uid, "success")

    if prod_key == "vip_3":
        db.add_vip(uid, days=3, bonus_superlikes=2)
        reward_text = "👑 <b>VIP на 3 дня</b> и <b>+2 💌 Супер-письма</b> начислены!"
    elif prod_key == "vip_7":
        db.add_vip(uid, days=7, bonus_superlikes=3)
        reward_text = "👑 <b>VIP на 7 дней</b> и <b>+3 💌 Супер-письма</b> начислены!"
    elif prod_key == "vip_30":
        db.add_vip(uid, days=30, bonus_superlikes=10)
        reward_text = "👑 <b>VIP на 30 дней</b> и <b>+10 💌 Супер-писем</b> начислены!"
    elif prod_key == "vip_90":
        db.add_vip(uid, days=90, bonus_superlikes=25)
        reward_text = "👑 <b>VIP на 90 дней</b> и <b>+25 💌 Супер-писем</b> начислены!"
    elif prod_key == "likes_unlimited":
        db.add_vip(uid, days=7, bonus_superlikes=0)
        reward_text = "❤️ <b>Безлимитные лайки на 7 дней</b> активированы!"
    elif prod_key == "likes_pack_50":
        # Добавляем 50 лайков через супер-письма как счетчик (или просто супер-письма)
        db.add_superlikes(uid, count=50)
        reward_text = "❤️ <b>+50 лайков</b> добавлены на твой баланс!"
    elif prod_key == "boost_24":
        db.add_boost(uid, hours=24)
        reward_text = "🚀 <b>Буст в ТОП на 24 часа</b> активирован!"
    elif prod_key == "boost_72":
        db.add_boost(uid, hours=72)
        reward_text = "🚀 <b>Буст в ТОП на 72 часа</b> активирован!"
    elif prod_key == "super_1":
        db.add_superlikes(uid, count=1)
        reward_text = "💌 <b>+1 Супер-письмо</b> добавлено!"
    elif prod_key == "super_5":
        db.add_superlikes(uid, count=5)
        reward_text = "💌 <b>+5 Супер-писем</b> добавлено!"
    elif prod_key == "super_15":
        db.add_superlikes(uid, count=15)
        reward_text = "💌 <b>+15 Супер-писем</b> добавлено!"
    elif prod_key == "reset_skips":
        cnt = db.reset_user_skips(uid)
        reward_text = f"🔄 <b>История пропусков очищена</b> (восстановлено анкет: {cnt})!"
    elif prod_key == "see_likes":
        db.add_vip(uid, days=3, bonus_superlikes=0)
        reward_text = "👀 <b>Доступ к входящим лайкам на 3 дня</b> активирован!"
    elif prod_key == "anonymous_view":
        db.add_vip(uid, days=7, bonus_superlikes=0)
        reward_text = "👤 <b>Анонимный просмотр на 7 дней</b> активирован!"
    else:
        reward_text = "✅ <b>Услуга успешно активирована!</b>"

    inviter_id = db.reward_inviter_on_purchase(uid)
    if inviter_id:
        try:
            await bot.send_message(
                inviter_id,
                "💎 <b>Приглашённый тобой друг совершил покупку в магазине!</b>\n"
                "🎁 Тебе начислен партнёрский бонус: <b>👑 +24 часа VIP-статуса</b> и <b>+2 💌 Супер-письма</b>!",
            )
        except Exception:
            pass

    return reward_text


@router.callback_query(F.data.startswith("shop:info:"))
async def cb_shop_info(call: CallbackQuery, bot: Bot) -> None:
    category = call.data.split(":", 2)[2]
    texts = {
        "vip": "👑 <b>VIP-подписки</b>\n\nОткройте привилегии: просмотр лайков, безлимитные лайки, супер-письма, приоритет в поиске.",
        "likes": "❤️ <b>Лайки</b>\n\nДополнительные лайки сверх дневного лимита или безлимит на неделю.",
        "boost": "🚀 <b>Бусты</b>\n\nПоднимите анкету в ТОП, чтобы вас видели больше людей.",
        "msg": "💌 <b>Супер-письма</b>\n\nНапишите любому человеку сразу, без ожидания взаимной симпатии.",
        "extra": "🔧 <b>Другое</b>\n\nСброс пропусков, просмотр лайков, анонимный просмотр анкет.",
    }
    await call.answer(texts.get(category, "Раздел магазина"), show_alert=True)


@router.callback_query(F.data == "shop:menu")
async def cb_shop_menu(call: CallbackQuery, db: Database, bot: Bot) -> None:
    uid = call.from_user.id
    me = db.get_profile(uid)
    if not me or int(me["age"]) < 18:
        await call.answer("Платные услуги доступны только пользователям 18+.", show_alert=True)
        return

    await call.answer()
    await send_transition_sticker(bot, uid, "shop")
    
    now = int(time.time())
    vip_active = int(me["vip_until"]) > now
    vip_line = f"👑 <b>Ваш VIP:</b> активен до {(int(me['vip_until']) - now) // 86400} дн." if vip_active else "👑 <b>Ваш VIP:</b> не подключён"

    payment_box = screen_box("💳 Способ оплаты", [
        stat_row("🏦", "ЮMoney", "Банковские карты РФ (Mir, Visa, Mastercard)"),
        stat_row("⭐️", "Звёзды Telegram", "Оплата внутри Telegram (если подключено)"),
    ])
    text = (
        f"{screen_header('👑 Магазин привилегий', 'VIP и услуги для анкеты')}\n"
        f"{vip_line}\n\n"
        "Выдели свою анкету среди тысяч других, открой список всех, кто тебя лайкнул, и пиши любому человеку напрямую без ожидания взаимности!\n\n"
        f"{payment_box}\n\n"
        "👇 <b>Выбери нужную привилегию:</b>"
    )
    await send_screen(bot, uid, text, reply_markup=kb_shop_menu())


@router.callback_query(F.data.startswith("shop:buy:"))
async def cb_shop_buy(call: CallbackQuery, db: Database, bot: Bot) -> None:
    uid = call.from_user.id
    me = db.get_profile(uid)
    if not me or int(me["age"]) < 18:
        await call.answer("Доступно только с 18 лет.", show_alert=True)
        return

    prod_key = call.data.split(":", 2)[2]
    prod = PRODUCTS.get(prod_key)
    await send_transition_sticker(bot, uid, "coins")
    if not prod:
        await call.answer("Товар не найден", show_alert=True)
        return

    await call.answer()
    await send_chat_action(bot, uid, "typing")

    # Если есть неоплаченный заказ на этот товар — используем его, чтобы не дублировать заказы
    existing = db.get_active_created_order(uid, prod_key)
    if existing:
        order_id = str(existing["order_id"])
        total_rub = int(existing["amount"])
    else:
        base_rub, comm_rub, total_rub = calc_price_with_commission(prod.base_price_rub)
        order_id = f"ym_{uid}_{uuid.uuid4().hex[:10]}"

        db.create_order(
            order_id=order_id,
            user_id=uid,
            product=prod.key,
            base_amount=base_rub,
            commission=comm_rub,
            total_amount=total_rub,
        )

    pay_url = create_yoomoney_pay_link(order_id, total_rub, f"{prod.title} (ID {uid})")

    pay_box = screen_box("💰 К оплате", [
        stat_row("💵", "Сумма", f"{total_rub} ₽", highlight=True),
    ])
    instruction_box = screen_box("📋 Инструкция", [
        stat_row("1️⃣", "Оплата", "Выбери способ ниже и заверши перевод"),
        stat_row("2️⃣", "Проверка", "После оплаты нажми «🔄 Проверить оплату»"),
    ])
    text = (
        f"{screen_header('Оформление заказа', prod.title)}\n"
        f"{prod.description}\n\n"
        f"{pay_box}\n\n"
        f"{instruction_box}"
    )

    kb_rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=f"💳 Оплатить {total_rub} ₽ через ЮMoney", url=pay_url)],
    ]

    kb_rows.extend([
        [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"shop:check:{order_id}")],
        [InlineKeyboardButton(text="🛠 Помощь / Написать в поддержку", url=SUPPORT_URL)],
        [
            InlineKeyboardButton(text="⬅️ Назад в магазин", callback_data="shop:menu"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="menu:home"),
        ],
    ])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await send_screen(bot, uid, text, reply_markup=kb)


@router.callback_query(F.data.startswith("shop:check:"))
async def cb_shop_check(call: CallbackQuery, db: Database, bot: Bot) -> None:
    uid = call.from_user.id
    order_id = call.data.split(":", 2)[2]
    order = db.get_order(order_id)

    if not order or int(order["user_id"]) != uid:
        await call.answer("Заказ не найден.", show_alert=True)
        return

    if order["status"] == "paid":
        await call.answer("Заказ уже оплачен и активирован! ✅", show_alert=True)
        await render_home(bot, db, uid)
        return

    # Автопроверка оплаты через ЮMoney API
    result = await async_check_yoomoney_payment(order_id)
    if result.get("success"):
        reward_text = await complete_and_reward_order(bot, db, order, f"yoomoney_auto_{order_id}")
        await call.answer("Оплата подтверждена! Привилегии начислены ✅", show_alert=True)
        try:
            await call.message.edit_text(
                f"{esc(call.message.text)}\n\n✅ <b>ОПЛАТА ПОДТВЕРЖДЕНА АВТОМАТИЧЕСКИ — ПРИВИЛЕГИИ ВЫДАНЫ</b>"
            )
        except Exception:
            pass
        try:
            await send_screen(
                bot,
                uid,
                f"🎉 <b>Оплата подтверждена!</b>\n━━━━━━━━━━━━━━━━━━━━\n{reward_text}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="🔥 Смотреть анкеты", callback_data="browse:next")],
                        [InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home")],
                    ]
                ),
            )
        except Exception:
            pass
    else:
        await call.answer("Оплата пока не найдена. Повторите перевод или проверьте позже.", show_alert=True)


@router.callback_query(F.data.startswith("adminpay:"))
async def cb_admin_payment_decision(call: CallbackQuery, db: Database, bot: Bot) -> None:
    if call.from_user.id != ADMIN_ID:
        await call.answer("Доступ запрещён", show_alert=True)
        return

    _, action, order_id = call.data.split(":", 2)
    order = db.get_order(order_id)
    if not order:
        await call.answer("Заказ не найден в БД", show_alert=True)
        return

    buyer_id = int(order["user_id"])

    if order["status"] == "paid":
        await call.answer("Заказ уже был подтверждён ранее!", show_alert=True)
        return

    if action == "approve":
        reward_text = await complete_and_reward_order(bot, db, order, f"admin_approved_{order_id}")
        await call.answer("Привилегия выдана пользователю! ✅", show_alert=True)
        try:
            await call.message.edit_text(
                f"{esc(call.message.text)}\n\n✅ <b>ПОДТВЕРЖДЕНО АДМИНИСТРАТОРОМ — ПРИВИЛЕГИИ ВЫДАНЫ</b>"
            )
        except Exception:
            pass

        try:
            await send_screen(
                bot,
                buyer_id,
                f"🎉 <b>Твоя оплата подтверждена!</b>\n━━━━━━━━━━━━━━━━━━━━\n{reward_text}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="🔥 Смотреть анкеты", callback_data="browse:next")],
                        [InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home")],
                    ]
                ),
            )
        except Exception:
            pass

    elif action == "reject":
        await call.answer("Отклонено ❌", show_alert=True)
        try:
            await call.message.edit_text(
                f"{esc(call.message.text)}\n\n❌ <b>ОТКЛОНЕНО (ОПЛАТА НЕ ПОСТУПИЛА)</b>"
            )
        except Exception:
            pass
        try:
            await bot.send_message(
                buyer_id,
                f"⚠️ <b>Платёж по заказу <code>{order_id}</code> не найден.</b>\n"
                f"Если вы уже совершили перевод, напишите напрямую в поддержку: {SUPPORT_URL}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="🛠 Написать в поддержку", url=SUPPORT_URL)]]
                ),
            )
        except Exception:
            pass


# =========================
# Оплата только через ЮMoney (автопроверка через API)
# =========================


# =========================
# ПОДДЕРЖКА И СООБЩЕНИЕ ОБ ОШИБКЕ
# =========================
async def open_support_screen(bot: Bot, user_id: int) -> None:
    support_bug_box = screen_box("🛠 Сообщить об ошибке", [
        stat_row("🐛", "Баг", "Нашли неисправность? Опиши проблему"),
    ])
    support_pay_box = screen_box("💳 Оплата и VIP", [
        stat_row("💳", "Проблема", "Вопрос по оплате или статусу?"),
    ])
    text = (
        f"{screen_header('Центр поддержки', 'Помощь и обратная связь')}\n"
        f"👨‍💻 <b>Официальная поддержка:</b> <a href='{SUPPORT_URL}'>{SUPPORT_USERNAME}</a>\n\n"
        f"{support_bug_box}\n\n"
        f"{support_pay_box}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛠 Сообщить об ошибке / Неисправности", url=SUPPORT_URL)],
            [InlineKeyboardButton(text="💬 Написать в поддержку (@vladofix97)", url=SUPPORT_URL)],
            [InlineKeyboardButton(text="📝 Отправить обращение через бота", callback_data="support:ticket")],
            [InlineKeyboardButton(text="⭐️ Отзывы о боте", callback_data="reviews:menu")],
            [InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data="menu:home")],
        ]
    )
    await send_screen(bot, user_id, text, reply_markup=kb)


@router.message(Command("paysupport"))
async def cmd_paysupport(message: Message, state: FSMContext, bot: Bot) -> None:
    await delete_user_msg(message)
    await state.clear()
    await open_support_screen(bot, message.from_user.id)


@router.callback_query(F.data == "support:menu")
async def cb_support_menu(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    await call.answer()
    await open_support_screen(bot, call.from_user.id)


@router.callback_query(F.data == "support:ticket")
async def cb_support_ticket(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await call.answer()
    await state.set_state(SupportForm.waiting_text)
    await send_screen(
        bot,
        call.from_user.id,
        "📝 <b>Отправка обращения в поддержку</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Опиши свой вопрос, проблему с оплатой или найденную ошибку одним сообщением и отправь в чат (или напиши напрямую "
        f"<a href='{SUPPORT_URL}'>{SUPPORT_USERNAME}</a>):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💬 Открыть чат с @vladofix97", url=SUPPORT_URL)],
                [
                    InlineKeyboardButton(text="⬅️ Назад", callback_data="support:menu"),
                    InlineKeyboardButton(text="❌ Отмена", callback_data="menu:home"),
                ],
            ]
        ),
    )


@router.message(SupportForm.waiting_text)
async def msg_support_send(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_msg(message)
    uid = message.from_user.id
    text = (message.text or "").strip()
    await state.clear()

    if ADMIN_ID > 0 and len(text) > 2:
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🆘 <b>Обращение в поддержку</b>\n"
                f"От: <code>{uid}</code> (@{esc(message.from_user.username or 'нет')})\n\n"
                f"<blockquote>{esc(text)}</blockquote>",
            )
        except Exception:
            pass

    await send_screen(
        bot,
        uid,
        "✅ <b>Твоё обращение передано администратору!</b>\n"
        f"Для максимально быстрого ответа ты также можешь написать напрямую: <a href='{SUPPORT_URL}'>{SUPPORT_USERNAME}</a>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💬 Написать @vladofix97", url=SUPPORT_URL)],
                [InlineKeyboardButton(text="⬅️ В главное меню", callback_data="menu:home")],
            ]
        ),
    )


# =========================
# АДМИН-ПАНЕЛЬ (/admin)
# =========================
async def render_admin_panel(bot: Bot, db: Database, user_id: int) -> None:
    if user_id != ADMIN_ID:
        return
    stats = db.get_admin_stats()
    ym_status = "🟢 Подключён (Авто-проверка)" if is_yoomoney_token_configured() else "🟡 Ручной режим (Кнопки в боте)"
    admin_stats_box = screen_box("📊 Статистика", [
        stat_row("👥", "Всего анкет", f"{stats['users']} чел.", highlight=True),
        stat_row("✅", "Верифицировано", f"{stats['verified']} чел."),
        stat_row("👑", "Активных VIP", f"{stats['vips']} чел."),
        stat_row("💞", "Мэтчей", f"{stats['matches']} шт."),
        stat_row("🎁", "Рефералов", f"{stats['refs']} чел."),
        stat_row("🧾", "Оплаченных заказов", f"{stats['orders']} шт."),
        stat_row("💰", "Общий оборот", f"{stats['revenue']} ₽"),
        stat_row("📈", "Комиссия", f"{stats['commission']} ₽ ({COMMISSION_PERCENT}%)"),
        stat_row("⭐️", "Отзывов", f"{stats['reviews']} шт."),
    ])
    admin_pay_box = screen_box("💳 Платежи", [
        stat_row("🔗", "ЮMoney API", ym_status),
    ])
    text = (
        f"{screen_header('Панель администратора', 'Управление ботом «Лавчик»')}\n"
        f"{admin_stats_box}\n\n"
        f"{admin_pay_box}\n\n"
        f"{screen_footer(f'🆘 Поддержка: {SUPPORT_URL}')}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Сделать рассылку по базе", callback_data="admin:broadcast")],
            [InlineKeyboardButton(text="🎁 Выдать VIP на 7 дней по ID", callback_data="admin:gift_vip")],
            [InlineKeyboardButton(text="🔍 Найти пользователя по ID", callback_data="admin:search_user")],
            [InlineKeyboardButton(text="⭐️ Управление отзываами", callback_data="reviews:menu")],
            [InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data="menu:home")],
        ]
    )
    await send_screen(bot, user_id, text, reply_markup=kb)


@router.message(Command("admin"))
async def cmd_admin(message: Message, db: Database, bot: Bot) -> None:
    await delete_user_msg(message)
    await render_admin_panel(bot, db, message.from_user.id)


@router.callback_query(F.data == "admin:menu")
async def cb_admin_menu(call: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    await state.clear()
    await call.answer()
    await render_admin_panel(bot, db, call.from_user.id)


@router.callback_query(F.data == "admin:broadcast")
async def cb_admin_broadcast(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if call.from_user.id != ADMIN_ID:
        return
    await call.answer()
    await state.set_state(AdminForm.waiting_broadcast)
    await send_screen(
        bot,
        call.from_user.id,
        "📢 <b>Массовая рассылка всем пользователям</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Отправь текст рассылки (поддерживается HTML-разметка):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin:menu")]]
        ),
    )


@router.message(AdminForm.waiting_broadcast)
async def msg_admin_broadcast(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_msg(message)
    if message.from_user.id != ADMIN_ID:
        return
    text = (message.text or "").strip()
    await state.clear()

    users = db.get_all_user_ids()
    sent = 0
    for u in users:
        try:
            await bot.send_message(
                u,
                text,
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="🔥 Смотреть анкеты", callback_data="browse:next")],
                    ]
                ),
            )
            sent += 1
            await asyncio.sleep(0.04)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 0.1)
            try:
                await bot.send_message(
                    u,
                    text,
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="🔥 Смотреть анкеты", callback_data="browse:next")],
                        ]
                    ),
                )
                sent += 1
            except Exception:
                pass
        except (TelegramForbiddenError, TelegramBadRequest):
            pass
        except Exception:
            pass

    await send_screen(
        bot,
        ADMIN_ID,
        f"✅ <b>Рассылка завершена!</b>\nУспешно доставлено: <b>{sent}</b> из {len(users)} чел.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin:menu")]]
        ),
    )


@router.callback_query(F.data == "admin:gift_vip")
async def cb_admin_gift_vip(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if call.from_user.id != ADMIN_ID:
        return
    await call.answer()
    await state.set_state(AdminForm.waiting_gift_uid)
    await send_screen(
        bot,
        call.from_user.id,
        "🎁 <b>Выдача VIP-статуса на 7 дней</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Отправь Telegram ID пользователя числом:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin:menu")]]
        ),
    )


@router.message(AdminForm.waiting_gift_uid)
async def msg_admin_gift_uid(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_msg(message)
    if message.from_user.id != ADMIN_ID:
        return
    raw = (message.text or "").strip()
    await state.clear()

    if not raw.isdigit():
        await render_admin_panel(bot, db, ADMIN_ID)
        return

    target_uid = int(raw)
    p = db.get_profile(target_uid)
    if not p:
        await send_screen(
            bot,
            ADMIN_ID,
            f"⚠️ Пользователь с ID <code>{target_uid}</code> не найден в базе анкет.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu")]]
            ),
        )
        return

    db.add_vip(target_uid, days=7, bonus_superlikes=3)
    try:
        await bot.send_message(
            target_uid,
            "🎁 <b>Администратор подарил тебе 👑 VIP-статус на 7 дней и +3 💌 Супер-письма!</b>",
        )
    except Exception:
        pass

    await send_screen(
        bot,
        ADMIN_ID,
        f"✅ Пользователю <b>{esc(p['name'])}</b> (<code>{target_uid}</code>) выдан VIP на 7 дней!",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin:menu")]]
        ),
    )


@router.callback_query(F.data == "admin:search_user")
async def cb_admin_search_user(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if call.from_user.id != ADMIN_ID:
        return
    await call.answer()
    await state.set_state(AdminForm.waiting_search_uid)
    await send_screen(
        bot,
        call.from_user.id,
        "🔍 <b>Поиск пользователя по ID</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Отправь Telegram ID пользователя числом:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin:menu")]]
        ),
    )


@router.message(AdminForm.waiting_search_uid)
async def msg_admin_search_uid(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    await delete_user_msg(message)
    if message.from_user.id != ADMIN_ID:
        return
    raw = (message.text or "").strip()
    await state.clear()

    if not raw.isdigit():
        await render_admin_panel(bot, db, ADMIN_ID)
        return

    target_uid = int(raw)
    p = db.get_profile(target_uid)
    if not p:
        await send_screen(
            bot,
            ADMIN_ID,
            f"⚠️ Пользователь с ID <code>{target_uid}</code> не найден в базе анкет.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu")]]
            ),
        )
        return

    now = int(time.time())
    is_banned = int(p["banned"]) == 1
    is_vip = int(p["vip_until"]) > now
    is_verified = int(p["verified"]) == 1
    today_likes = db.count_today_likes(target_uid)
    remaining_likes = max(0, FREE_DAILY_LIKES - today_likes) if not is_vip else 0

    text = (
        f"👤 <b>Анкета пользователя</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID: <code>{target_uid}</code>\n"
        f"👤 Имя: {esc(p['name'])}, {int(p['age'])} лет\n"
        f"🏙 Город: {esc(p['city'])}\n"
        f"🚻 Пол: {format_gender_label(str(p['gender'] or 'M'))}\n"
        f"🔍 Ищет: {format_looking_for_label(str(p['looking_for'] or 'A'))}\n"
        f"✅ Верифицирован: {'Да' if is_verified else 'Нет'}\n"
        f"👑 VIP: {'Да' if is_vip else 'Нет'}\n"
        f"🚫 Бан: {'Да' if is_banned else 'Нет'}\n"
        f"💌 Супер-писем: {p['superlikes']}\n"
        f"👥 Приглашено друзей: {p['ref_count']}\n"
        f"💝 Бесплатных лайков осталось today: {remaining_likes if not is_vip else '∞ (VIP)'}\n"
        f"📅 Создан: {time.strftime('%d.%m.%Y', time.localtime(int(p['created_at'])))}\n"
    )

    ban_btn_text = "🟢 Разбанировать" if is_banned else "🔴 Забанить"
    verify_btn_text = "❌ Снять верификацию" if is_verified else "✅ Выдать верификацию"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=ban_btn_text, callback_data=f"adminmod:toggle_ban:{target_uid}"),
                InlineKeyboardButton(text=verify_btn_text, callback_data=f"adminmod:toggle_verify:{target_uid}"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin:menu")],
        ]
    )
    await send_screen(bot, ADMIN_ID, text, reply_markup=kb)


@router.callback_query(F.data.startswith("adminmod:toggle_ban:"))
async def cb_admin_toggle_ban(call: CallbackQuery, db: Database, bot: Bot) -> None:
    if call.from_user.id != ADMIN_ID:
        return
    target_uid = int(call.data.split(":")[2])
    p = db.get_profile(target_uid)
    if not p:
        await call.answer("Пользователь не найден", show_alert=True)
        return
    is_banned = int(p["banned"]) == 1
    db.set_banned(target_uid, not is_banned)
    status = "разбанен" if not is_banned else "забанен"
    await call.answer(f"Пользователь {status}!", show_alert=True)
    await _render_admin_user_card(bot, db, call.from_user.id, target_uid, call.message.message_id)


@router.callback_query(F.data.startswith("adminmod:toggle_verify:"))
async def cb_admin_toggle_verify(call: CallbackQuery, db: Database, bot: Bot) -> None:
    if call.from_user.id != ADMIN_ID:
        return
    target_uid = int(call.data.split(":")[2])
    p = db.get_profile(target_uid)
    if not p:
        await call.answer("Пользователь не найден", show_alert=True)
        return
    is_verified = int(p["verified"]) == 1
    db.set_verified(target_uid, not is_verified)
    status = "выдана" if not is_verified else "снята"
    await call.answer(f"Верификация {status}!", show_alert=True)
    await _render_admin_user_card(bot, db, call.from_user.id, target_uid, call.message.message_id)


async def _render_admin_user_card(bot: Bot, db: Database, admin_id: int, target_uid: int, message_id: Optional[int] = None) -> None:
    """Перерисовка карточки пользователя в админке после изменения статуса."""
    p = db.get_profile(target_uid)
    if not p:
        return
    now = int(time.time())
    is_banned = int(p["banned"]) == 1
    is_vip = int(p["vip_until"]) > now
    is_verified = int(p["verified"]) == 1
    today_likes = db.count_today_likes(target_uid)
    remaining_likes = max(0, FREE_DAILY_LIKES - today_likes) if not is_vip else 0

    text = (
        f"👤 <b>Анкета пользователя</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID: <code>{target_uid}</code>\n"
        f"👤 Имя: {esc(p['name'])}, {int(p['age'])} лет\n"
        f"🏙 Город: {esc(p['city'])}\n"
        f"🚻 Пол: {format_gender_label(str(p['gender'] or 'M'))}\n"
        f"🔍 Ищет: {format_looking_for_label(str(p['looking_for'] or 'A'))}\n"
        f"✅ Верифицирован: {'Да' if is_verified else 'Нет'}\n"
        f"👑 VIP: {'Да' if is_vip else 'Нет'}\n"
        f"🚫 Бан: {'Да' if is_banned else 'Нет'}\n"
        f"💌 Супер-писем: {p['superlikes']}\n"
        f"👥 Приглашено друзей: {p['ref_count']}\n"
        f"💝 Бесплатных лайков осталось today: {remaining_likes if not is_vip else '∞ (VIP)'}\n"
        f"📅 Создан: {time.strftime('%d.%m.%Y', time.localtime(int(p['created_at'])))}\n"
    )

    ban_btn_text = "🟢 Разбанировать" if is_banned else "🔴 Забанить"
    verify_btn_text = "❌ Снять верификацию" if is_verified else "✅ Выдать верификацию"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=ban_btn_text, callback_data=f"adminmod:toggle_ban:{target_uid}"),
                InlineKeyboardButton(text=verify_btn_text, callback_data=f"adminmod:toggle_verify:{target_uid}"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin:menu")],
        ]
    )

    if message_id:
        try:
            await bot.edit_message_text(
                chat_id=admin_id,
                message_id=message_id,
                text=text,
                reply_markup=kb,
            )
        except Exception:
            pass


# Перехват любых случайных сообщений вне форм — удаляем и возвращаем чистое меню
@router.message()
async def fallback_cleanup(message: Message, db: Database, bot: Bot) -> None:
    await delete_user_msg(message)
    await render_home(bot, db, message.from_user.id, username=message.from_user.username)


# =========================
# HEALTH-CHECK ВЕБ-СЕРВЕР (ДЛЯ ХОСТИНГА)
# =========================
async def health_handler(request: web.Request) -> web.Response:
    return web.Response(text="Dating Bot is running OK!", status=200)


async def start_dummy_webserver() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Health-check сервер запущен на порту {port}")
    return runner


# =========================
# ТОЧКА ВХОДА (MAIN)
# =========================
async def main() -> None:
    global BOT_USERNAME
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    if not BOT_TOKEN or ":" not in BOT_TOKEN:
        logging.error("КРИТИЧЕСКАЯ ОШИБКА: BOT_TOKEN не задан или имеет неверный формат в .env!")
        return

    db = Database(DB_PATH)
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp["db"] = db
    dp.include_router(router)

    web_runner = await start_dummy_webserver()

    try:
        me = await bot.get_me()
        BOT_USERNAME = me.username or "lovchik_tg_bot"
        logging.info(f"Бот успешно авторизован: @{BOT_USERNAME} (ID: {me.id})")

        await bot.set_my_commands(
            [
                BotCommand(command="start", description="🏠 Главное меню"),
                BotCommand(command="paysupport", description="🛠 Поддержка и сообщение об ошибке"),
            ]
        )
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except TelegramUnauthorizedError:
        logging.error(
            "КРИТИЧЕСКАЯ ОШИБКА: Токен бота недействителен (Unauthorized)! "
            "Создайте новый токен в @BotFather и обновите BOT_TOKEN в .env."
        )
    finally:
        await web_runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
