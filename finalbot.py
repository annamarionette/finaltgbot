import logging
import time
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from datetime import datetime

TELEGRAM_BOT_TOKEN = "8688437860:AAHPJMAongZ_5mfkCBh-ASHW2iW2lORPPaY"

COINGECKO_URL = "https://api.coingecko.com/api/v3"
EXCHANGERATE_URL = "https://open.er-api.com/v6/latest"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_coin_map: dict | None = None
_coin_map_time: float = 0

_fiat_currencies: set | None = None
_fiat_cache_time: float = 0

_fiat_rates_cache: dict = {}  # {base_upper: {data, timestamp}}
_crypto_price_cache: dict = {}  # {cache_key: {price, timestamp}}

CACHE_TTL_COINS = 3600       # список монет — 1 час
CACHE_TTL_FIAT = 600         # фиатные курсы — 10 мин
CACHE_TTL_CRYPTO = 60        # крипто-цены — 1 мин

EMOJI_MAP = {
    "btc": "₿", "eth": "⟠", "ton": "💎", "usdt": "💵", "usdc": "💵",
    "bnb": "🔶", "sol": "🟣", "xrp": "⚫", "ada": "🔵", "doge": "🐕",
    "dot": "⬤", "matic": "🟪", "shib": "🐶", "trx": "🔺", "ltc": "🥈",
    "avax": "🔺", "link": "🔗", "atom": "⚛️", "near": "🌐", "apt": "🟦",
    "sui": "🌊", "arb": "🔵", "op": "🔴", "not": "🖤",
    "usd": "🇺🇸", "eur": "🇪🇺", "rub": "🇷🇺", "gbp": "🇬🇧",
    "jpy": "🇯🇵", "cny": "🇨🇳", "krw": "🇰🇷", "try": "🇹🇷",
    "uah": "🇺🇦", "kzt": "🇰🇿", "byn": "🇧🇾", "gel": "🇬🇪",
    "aed": "🇦🇪", "inr": "🇮🇳", "chf": "🇨🇭", "cad": "🇨🇦",
    "aud": "🇦🇺", "brl": "🇧🇷", "pln": "🇵🇱", "thb": "🇹🇭",
}

POPULAR_CRYPTO = ["btc", "eth", "ton", "usdt", "sol", "bnb", "xrp", "doge"]
POPULAR_FIAT = ["usd", "eur", "rub", "gbp", "cny", "jpy", "try", "uah"]

PRIORITY_COINS = {
    "btc": "bitcoin", "eth": "ethereum", "ton": "the-open-network",
    "usdt": "tether", "usdc": "usd-coin", "bnb": "binancecoin",
    "sol": "solana", "xrp": "ripple", "ada": "cardano",
    "doge": "dogecoin", "dot": "polkadot", "matic": "matic-network",
    "shib": "shiba-inu", "trx": "tron", "ltc": "litecoin",
    "avax": "avalanche-2", "link": "chainlink", "atom": "cosmos",
    "xlm": "stellar", "near": "near", "apt": "aptos", "sui": "sui",
    "arb": "arbitrum", "op": "optimism", "not": "notcoin",
    "pepe": "pepe", "wbtc": "wrapped-bitcoin", "dai": "dai",
    "uni": "uniswap", "aave": "aave", "fil": "filecoin",
    "algo": "algorand", "ftm": "fantom", "mana": "decentraland",
    "sand": "the-sandbox", "axs": "axie-infinity",
}


def get_coin_map() -> dict:
    global _coin_map, _coin_map_time
    now = time.time()
    if _coin_map is not None and (now - _coin_map_time) < CACHE_TTL_COINS:
        return _coin_map

    logger.info("📥 Загружаю список монет CoinGecko...")
    try:
        resp = requests.get(
            f"{COINGECKO_URL}/coins/list",
            params={"include_platform": "false"},
            timeout=30,
        )
        resp.raise_for_status()
        coins = resp.json()

        mapping = {}
        mapping.update(PRIORITY_COINS)
        for coin in coins:
            sym = coin["symbol"].lower()
            if sym not in mapping:
                mapping[sym] = coin["id"]

        _coin_map = mapping
        _coin_map_time = now
        logger.info(f"✅ Загружено {len(_coin_map)} монет")
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки монет: {e}")
        if _coin_map is None:
            _coin_map = dict(PRIORITY_COINS)
            _coin_map_time = now

    return _coin_map


def get_fiat_currencies() -> set:
    global _fiat_currencies, _fiat_cache_time
    now = time.time()
    if _fiat_currencies and (now - _fiat_cache_time) < CACHE_TTL_COINS:
        return _fiat_currencies

    logger.info("📥 Загружаю фиатные валюты...")
    try:
        resp = requests.get(f"{EXCHANGERATE_URL}/USD", timeout=15)
        resp.raise_for_status()
        data = resp.json()
        _fiat_currencies = {c.lower() for c in data["rates"]}
        _fiat_cache_time = now
        logger.info(f"✅ Загружено {len(_fiat_currencies)} фиатных валют")
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки фиатных валют: {e}")
        if _fiat_currencies is None:
            _fiat_currencies = {
                "usd", "eur", "rub", "gbp", "jpy", "cny",
                "krw", "try", "uah", "kzt", "byn", "gel",
                "aed", "inr", "chf", "cad", "aud", "brl",
            }

    return _fiat_currencies


def is_fiat(symbol: str) -> bool:
    return symbol.lower() in get_fiat_currencies()


def is_crypto(symbol: str) -> bool:
    return symbol.lower() in get_coin_map()


def get_fiat_rate(from_cur: str, to_cur: str) -> float:
    base = from_cur.upper()
    now = time.time()

    if base in _fiat_rates_cache:
        cached = _fiat_rates_cache[base]
        if (now - cached["timestamp"]) < CACHE_TTL_FIAT:
            return float(cached["data"][to_cur.upper()])

    resp = requests.get(f"{EXCHANGERATE_URL}/{base}", timeout=15)
    resp.raise_for_status()
    data = resp.json()

    _fiat_rates_cache[base] = {"data": data["rates"], "timestamp": now}

    target = to_cur.upper()
    if target not in data["rates"]:
        raise ValueError(f"Валюта {target} не найдена")
    return float(data["rates"][target])


def get_crypto_price(coin_symbol: str, vs_currency: str) -> float:
    coin_map = get_coin_map()
    coin_id = coin_map.get(coin_symbol.lower())
    if not coin_id:
        raise ValueError(f"Криптовалюта «{coin_symbol}» не найдена")

    cache_key = f"{coin_id}_{vs_currency.lower()}"
    now = time.time()

    if cache_key in _crypto_price_cache:
        cached = _crypto_price_cache[cache_key]
        if (now - cached["timestamp"]) < CACHE_TTL_CRYPTO:
            return cached["price"]

    resp = requests.get(
        f"{COINGECKO_URL}/simple/price",
        params={"ids": coin_id, "vs_currencies": vs_currency.lower()},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    vs = vs_currency.lower()
    if coin_id not in data or vs not in data[coin_id]:
        raise ValueError(f"Не удалось получить курс {coin_symbol} → {vs_currency}")

    price = float(data[coin_id][vs])
    _crypto_price_cache[cache_key] = {"price": price, "timestamp": now}
    return price


def convert(amount: float, from_sym: str, to_sym: str) -> float:
    f = from_sym.lower()
    t = to_sym.lower()

    f_fiat = is_fiat(f)
    t_fiat = is_fiat(t)
    f_crypto = is_crypto(f)
    t_crypto = is_crypto(t)

    if f_fiat and t_fiat:
        return amount * get_fiat_rate(f, t)

    if f_crypto and t_fiat:
        return amount * get_crypto_price(f, t)

    if f_fiat and t_crypto:
        price = get_crypto_price(t, f)
        if price == 0:
            raise ValueError("Цена равна нулю")
        return amount / price

    if f_crypto and t_crypto:
        price_from = get_crypto_price(f, "usd")
        price_to = get_crypto_price(t, "usd")
        if price_to == 0:
            raise ValueError("Цена равна нулю")
        return amount * price_from / price_to

    unknown = []
    if not f_fiat and not f_crypto:
        unknown.append(from_sym)
    if not t_fiat and not t_crypto:
        unknown.append(to_sym)
    raise ValueError(f"Не удалось распознать: {', '.join(unknown)}")


def get_emoji(symbol: str) -> str:
    return EMOJI_MAP.get(symbol.lower(), "🪙")


def format_number(value: float) -> str:
    if value == 0:
        return "0"
    abs_val = abs(value)
    if abs_val >= 1_000_000:
        return f"{value:,.2f}"
    elif abs_val >= 1:
        return f"{value:,.6f}".rstrip("0").rstrip(".")
    elif abs_val >= 0.0001:
        return f"{value:.8f}".rstrip("0").rstrip(".")
    else:
        return f"{value:.12f}".rstrip("0").rstrip(".")


def get_type_label(symbol: str) -> str:
    if is_fiat(symbol.lower()):
        return "фиат"
    elif is_crypto(symbol.lower()):
        return "крипто"
    return "?"


def build_result_message(
    amount: float, from_sym: str, to_sym: str, result: float
) -> str:
    e_from = get_emoji(from_sym)
    e_to = get_emoji(to_sym)
    f_upper = from_sym.upper()
    t_upper = to_sym.upper()

    if amount != 0:
        rate = result / amount
    else:
        rate = 0

    now_str = datetime.now().strftime("%H:%M:%S  %d.%m.%Y")

    type_from = get_type_label(from_sym)
    type_to = get_type_label(to_sym)

    if type_from == "крипто" and type_to == "фиат":
        direction = "🔄 Крипто → Фиат"
    elif type_from == "фиат" and type_to == "крипто":
        direction = "🔄 Фиат → Крипто"
    elif type_from == "крипто" and type_to == "крипто":
        direction = "🔄 Крипто → Крипто"
    else:
        direction = "🔄 Фиат → Фиат"

    msg = (
        f"{'━' * 28}\n"
        f"  💱  <b>КОНВЕРТАЦИЯ ВАЛЮТ</b>\n"
        f"{'━' * 28}\n"
        f"\n"
        f"  {e_from}  <b>{format_number(amount)} {f_upper}</b>\n"
        f"\n"
        f"        ⬇️\n"
        f"\n"
        f"  {e_to}  <b>{format_number(result)} {t_upper}</b>\n"
        f"\n"
        f"{'─' * 28}\n"
        f"  📊  <b>Курс:</b>  1 {f_upper} = {format_number(rate)} {t_upper}\n"
        f"\n"
        f"  {direction}\n"
        f"{'─' * 28}\n"
        f"  🕐  {now_str}\n"
        f"{'━' * 28}"
    )
    return msg


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [
            InlineKeyboardButton("📖 Как пользоваться", callback_data="help"),
            InlineKeyboardButton("🪙 Популярные", callback_data="popular"),
        ],
        [
            InlineKeyboardButton("📋 Примеры", callback_data="examples"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"{'━' * 28}\n"
        f"  💱  <b>КОНВЕРТЕР ВАЛЮТ</b>\n"
        f"{'━' * 28}\n"
        f"\n"
        f"  Добро пожаловать! 👋\n"
        f"\n"
        f"  Я конвертирую <b>фиатные</b> и\n"
        f"  <b>криптовалюты</b> в реальном времени.\n"
        f"\n"
        f"{'─' * 28}\n"
        f"  📝  <b>Формат запроса:</b>\n"
        f"\n"
        f"  Количество / Валюта-1 / Валюта-2\n"
        f"\n"
        f"  Например: 1 ton rub\n"
        f"{'─' * 28}\n"
        f"\n"
        f"  🌐  <b>150+</b> фиатных валют\n"
        f"  🪙  <b>10 000+</b> криптовалют\n"
        f"  ⚡  Курсы обновляются в реальном времени\n"
        f"\n"
        f"{'━' * 28}"
    )
    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=reply_markup
    )


async def button_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == "help":
        text = (
            f"{'━' * 28}\n"
            f"  📖  <b>КАК ПОЛЬЗОВАТЬСЯ</b>\n"
            f"{'━' * 28}\n"
            f"\n"
            f"  Отправьте сообщение в формате:\n"
            f"\n"
            f"  ЧИСЛО  ИЗ  В\n"
            f"\n"
            f"{'─' * 28}\n"
            f"  <b>Поддерживаемые направления:</b>\n"
            f"\n"
            f"  🔹 Фиат → Фиат\n"
            f"  🔹 Крипто → Фиат\n"
            f"  🔹 Фиат → Крипто\n"
            f"  🔹 Крипто → Крипто\n"
            f"\n"
            f"{'─' * 28}\n"
            f"  💡 <b>Советы:</b>\n"
            f"\n"
            f"  • Регистр не важен\n"
            f"  • Используйте тикеры валют\n"
            f"  • Дробные числа через точку\n"
            f"    или запятую\n"
            f"\n"
            f"{'━' * 28}"
        )

    elif query.data == "popular":
        crypto_lines = []
        for sym in POPULAR_CRYPTO:
            e = get_emoji(sym)
            crypto_lines.append(f"  {e}  <code>{sym.upper()}</code>")

        fiat_lines = []
        for sym in POPULAR_FIAT:
            e = get_emoji(sym)
            fiat_lines.append(f"  {e}  <code>{sym.upper()}</code>")

        text = (
            f"{'━' * 28}\n"
            f"  🪙  <b>ПОПУЛЯРНЫЕ ВАЛЮТЫ</b>\n"
            f"{'━' * 28}\n"
            f"\n"
            f"  <b>Криптовалюты:</b>\n"
            f"\n"
            + "\n".join(crypto_lines)
            + f"\n\n"
            f"{'─' * 28}\n"
            f"\n"
            f"  <b>Фиатные валюты:</b>\n"
            f"\n"
            + "\n".join(fiat_lines)
            + f"\n\n"
            f"{'━' * 28}"
        )

    elif query.data == "examples":
        text = (
            f"{'━' * 28}\n"
            f"  📋  <b>ПРИМЕРЫ ЗАПРОСОВ</b>\n"
            f"{'━' * 28}\n"
            f"\n"
            f"  <b>Крипто → Фиат:</b>\n"
            f"  1 ton rub\n"
            f"  0.5 btc usd\n"
            f"  10 eth eur\n"
            f"\n"
            f"  <b>Фиат → Крипто:</b>\n"
            f"  1000 rub ton\n"
            f"  100 usd btc\n"
            f"  500 eur eth\n"
            f"\n"
            f"  <b>Крипто → Крипто:</b>\n"
            f"  1 btc eth\n"
            f"  100 sol ton\n"
            f"  5000 doge usdt\n"
            f"\n"
            f"  <b>Фиат → Фиат:</b>\n"
            f"  100 usd rub\n"
            f"  50 eur gbp\n"
            f"  1000 uah kzt\n"
            f"\n"
            f"{'━' * 28}"
        )
    else:
        return

    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text, parse_mode="HTML", reply_markup=reply_markup
    )


async def back_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query.data != "back":
        return
    await query.answer()

    keyboard = [
        [
            InlineKeyboardButton("📖 Как пользоваться", callback_data="help"),
            InlineKeyboardButton("🪙 Популярные", callback_data="popular"),
        ],
        [
            InlineKeyboardButton("📋 Примеры", callback_data="examples"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"{'━' * 28}\n"
        f"  💱  <b>КОНВЕРТЕР ВАЛЮТ</b>\n"
        f"{'━' * 28}\n"
        f"\n"
        f"  Я конвертирую <b>фиатные</b> и\n"
        f"  <b>криптовалюты</b> в реальном времени.\n"
        f"\n"
        f"{'─' * 28}\n"
        f"  📝  <b>Формат запроса:</b>\n"
        f"\n"
        f"  Количество / Валюта-1 / Валюта-2\n"
        f"\n"
        f"  Например: 1 ton rub\n"
        f"{'━' * 28}"
    )
    await query.edit_message_text(
        text, parse_mode="HTML", reply_markup=reply_markup
    )


async def handle_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    parts = text.split()

    if len(parts) != 3:
        err = (
            f"  ❌  <b>Неверный формат</b>\n\n"
            f"  Используйте:\n"
            f"  Количество / Валюта-1 / Валюта-2\n\n"
            f"  Пример: 1 ton rub"
        )
        await update.message.reply_text(err, parse_mode="HTML")
        return

    raw_amount, from_sym, to_sym = parts

    try:
        amount = float(raw_amount.replace(",", "."))
    except ValueError:
        err = f"  ❌  <b>«{raw_amount}»</b> — не число\n\n  Введите корректное количество."
        await update.message.reply_text(err, parse_mode="HTML")
        return

    if amount <= 0:
        await update.message.reply_text(
            "  ❌  Количество должно быть больше нуля",
            parse_mode="HTML",
        )
        return

    if from_sym.lower() == to_sym.lower():
        msg = build_result_message(amount, from_sym, to_sym, amount)
        await update.message.reply_text(msg, parse_mode="HTML")
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    try:
        result = convert(amount, from_sym, to_sym)
    except ValueError as e:
        err = f"  ⚠️  <b>Ошибка:</b> {e}"
        await update.message.reply_text(err, parse_mode="HTML")
        return
    except requests.exceptions.Timeout:
        await update.message.reply_text(
            "  ⏳  <b>Таймаут.</b> API не отвечает, попробуйте позже.",
            parse_mode="HTML",
        )
        return
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка сети: {e}")
        await update.message.reply_text(
            "  🌐  <b>Ошибка сети.</b> Попробуйте через минуту.",
            parse_mode="HTML",
        )
        return
    except Exception as e:
        logger.exception("Непредвиденная ошибка")
        await update.message.reply_text(
            f"  ❌  <b>Ошибка:</b> {e}", parse_mode="HTML"
        )
        return

    reverse_text = f"{format_number(result)} {to_sym.lower()} {from_sym.lower()}"
    keyboard = [
        [
            InlineKeyboardButton(
                f"🔄 Обратно: {result:.4g} {to_sym.upper()} → {from_sym.upper()}",
                callback_data=f"conv:{reverse_text}",
            )
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    msg = build_result_message(amount, from_sym, to_sym, result)
    await update.message.reply_text(
        msg, parse_mode="HTML", reply_markup=reply_markup
    )


async def inline_convert(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query.data.startswith("conv:"):
        return
    await query.answer()

    data = query.data[5:]  # убираем "conv:"
    parts = data.split()
    if len(parts) != 3:
        return

    raw_amount, from_sym, to_sym = parts

    try:
        amount = float(raw_amount.replace(",", ""))
    except ValueError:
        return

    try:
        result = convert(amount, from_sym, to_sym)
    except Exception as e:
        await query.answer(f"Ошибка: {e}", show_alert=True)
        return

    msg = build_result_message(amount, from_sym, to_sym, result)

    reverse_text = f"{format_number(result)} {to_sym.lower()} {from_sym.lower()}"
    keyboard = [
        [
            InlineKeyboardButton(
                f"🔄 Обратно: {result:.4g} {to_sym.upper()} → {from_sym.upper()}",
                callback_data=f"conv:{reverse_text}",
            )
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        msg, parse_mode="HTML", reply_markup=reply_markup
    )


async def error_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    logger.error(f"Ошибка: {context.error}", exc_info=context.error)

def main() -> None:
    logger.info("🚀 Предзагрузка данных...")
    try:
        get_fiat_currencies()
        get_coin_map()
        logger.info("✅ Данные загружены")
    except Exception as e:
        logger.warning(f"⚠️  Предзагрузка не удалась: {e}")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))

    app.add_handler(CallbackQueryHandler(back_handler, pattern="^back$"))
    app.add_handler(
        CallbackQueryHandler(
            button_handler, pattern="^(help|popular|examples)$"
        )
    )
    app.add_handler(CallbackQueryHandler(inline_convert, pattern="^conv:"))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    app.add_error_handler(error_handler)

    logger.info("✅ Бот запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()