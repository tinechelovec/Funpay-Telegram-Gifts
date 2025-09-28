import os
import re
import json
import time
import asyncio
import logging
import threading
import colorlog
from typing import Optional, Tuple, List, Any

from dotenv import load_dotenv
from pyrogram import Client
from FunPayAPI import Account
from FunPayAPI.updater.runner import Runner
from FunPayAPI.updater.events import NewOrderEvent, NewMessageEvent

if not hasattr(Client, "send_gift"):
    raise RuntimeError(
        "Установлен неподдерживаемый пакет 'pyrogram'. Нужен форк с поддержкой Stars.\n"
        "Используйте: pip uninstall -y pyrogram && pip install -U pyrofork tgcrypto"
    )

# ---------- ENV ----------
load_dotenv()
GOLDEN_KEY = os.getenv("FUNPAY_AUTH_TOKEN")
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
API_ID = int(API_ID) if API_ID and API_ID.isdigit() else None

def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")

def _env_raw(name: str):
    return os.getenv(name)

def _parse_id_list(val: Optional[str], default: str = "3064,2418") -> Tuple[List[int], List[str], str]:
    raw = (val or default).strip()
    tokens = re.split(r"[,\s;]+", raw)
    ok: List[int] = []
    bad: List[str] = []
    for t in tokens:
        if not t:
            continue
        try:
            ok.append(int(t))
        except Exception:
            bad.append(t)
    if not ok:
        ok = [3064, 2418]
    return ok, bad, raw

RAW_IDS = os.getenv("CATEGORY_IDS") or os.getenv("CATEGORY_ID")
CATEGORY_IDS_LIST, BAD_TOKENS, RAW_IDS_STR = _parse_id_list(RAW_IDS)
ALLOWED_CATEGORY_IDS = set(CATEGORY_IDS_LIST)
PRIMARY_CATEGORY_ID = CATEGORY_IDS_LIST[0]

COOLDOWN_SECONDS = float(os.getenv("REPLY_COOLDOWN_SECONDS", "1.0"))
AUTO_REFUND_RAW = _env_raw("AUTO_REFUND")
AUTO_DEACTIVATE_RAW = _env_raw("AUTO_DEACTIVATE")
AUTO_REFUND = _env_bool("AUTO_REFUND", True)
AUTO_DEACTIVATE = _env_bool("AUTO_DEACTIVATE", True)

ANONYMOUS_GIFTS_RAW = _env_raw("ANONYMOUS_GIFTS")
ANONYMOUS_GIFTS = _env_bool("ANONYMOUS_GIFTS", False)

LOG_NAME = "FunPay-Gifts"

# ---------- ЛОГИ ----------
try:
    handler = colorlog.StreamHandler()
    color_formatter = colorlog.ColoredFormatter(
        fmt="%(log_color)s[%(levelname)-5s]%(reset)s %(blue)s" + LOG_NAME + "%(reset)s: %(message)s",
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "red,bg_white",
        },
    )
    handler.setFormatter(color_formatter)
    logger = colorlog.getLogger(LOG_NAME)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
except Exception:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-5s] " + LOG_NAME + ": %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger(LOG_NAME)

def log_info(ctx: str, msg: str):
    logger.info(f"{ctx + ' | ' if ctx else ''}{msg}")

def log_warn(ctx: str, msg: str):
    logger.warning(f"{ctx + ' | ' if ctx else ''}{msg}")

def log_error(ctx: str, msg: str):
    logger.error(f"{ctx + ' | ' if ctx else ''}{msg}")

# ---------- ПОДАРКИ ----------
with open("gifts.json", "r", encoding="utf-8") as f:
    GIFTS = json.load(f)

loop = asyncio.new_event_loop()
app: Optional[Client] = None
_app_started = threading.Event()

def _build_client() -> Client:
    if API_ID and API_HASH:
        return Client("stars", api_id=API_ID, api_hash=API_HASH, workdir="sessions")
    else:
        return Client("stars", workdir="sessions")

async def _runner_start():
    global app
    app = _build_client()
    await app.start()
    log_info("", "Pyrogram запущен — готов отправлять подарки.")
    _app_started.set()

def _thread_target():
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_runner_start())
    loop.run_forever()

threading.Thread(target=_thread_target, daemon=True).start()

_app_started.wait(timeout=10.0)
if not _app_started.is_set():
    log_warn("", "Pyrogram не успел стартовать за 10 сек. Продолжаю — вызовы подождут через future.")

waiting: dict[int, dict] = {}
_last_reply_by_buyer: dict[int, float] = {}

def _safe_attr(o: Any, *names: str, default: Any = None):
    for n in names:
        try:
            v = getattr(o, n, None)
            if v is not None:
                return v
        except Exception:
            pass
    return default

def short_text(s: Any, n: int = 180) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: n - 1] + "…"

def parse_gift_num(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"gift_tg\s*:\s*([0-9,\s]+)", text, flags=re.IGNORECASE)
    if not m:
        return None
    nums = [x.strip() for x in m.group(1).split(",") if x.strip().isdigit()]
    return nums[0] if nums else None

def nick_looks_valid(txt: str) -> bool:
    if not txt:
        return False
    t = txt.strip()
    if t.startswith("@"):
        t = t[1:]
    return bool(re.fullmatch(r"[A-Za-z0-9_]{5,32}", t))

def get_subcategory_id_safe(order, account) -> Tuple[Optional[int], Optional[object]]:
    subcat = getattr(order, "subcategory", None) or getattr(order, "sub_category", None)
    if subcat and hasattr(subcat, "id"):
        return subcat.id, subcat
    try:
        full_order = account.get_order(order.id)
        subcat = getattr(full_order, "subcategory", None) or getattr(full_order, "sub_category", None)
        if subcat and hasattr(subcat, "id"):
            return subcat.id, subcat
    except Exception as e:
        logger.debug(f"Не удалось загрузить полный заказ: {e}", exc_info=True)
    return None, None

def pretty_order_context(order_obj=None, buyer_id=None, gift=None):
    try:
        buyer_username = getattr(order_obj, "buyer_username", None) or getattr(order_obj, "buyer_name", None)
    except Exception:
        buyer_username = None

    if buyer_id is None:
        buyer_id = getattr(order_obj, "buyer_id", None) if order_obj is not None else buyer_id

    parts = []
    parts.append(f"Buyer {buyer_id or '?'}")
    if buyer_username:
        parts[-1] += f" @{buyer_username}"
    if gift:
        title = gift.get("title", "?")
        price = gift.get("price", "?")
        gid = gift.get("id", "?")
        parts.append(f"Gift {title} ({price}⭐, id={gid})")
    return " | ".join(parts)

# ---------- ОТПРАВКА ПОДАРКА ----------
async def _send_gift_once(username: str, gift_id: int) -> bool:
    uname = username.lstrip("@")
    await asyncio.sleep(0.05)

    attempts = [
        {"gift_id": gift_id},
        {"star_gift_id": gift_id},
    ]

    last_err: Optional[Exception] = None
    for i, extra in enumerate(attempts, 1):
        try:
            log_info("send_gift", f"Попытка {i}: keys={list(extra.keys())}, anon={ANONYMOUS_GIFTS}")
            res = await app.send_gift(chat_id=uname, hide_my_name=ANONYMOUS_GIFTS, **extra)
            if isinstance(res, bool):
                return res
            return True
        except TypeError as e:
            s = str(e)
            last_err = e
            if "unexpected keyword argument" in s:
                log_warn("send_gift", f"Сигнатура не приняла {list(extra.keys())}: {e}")
                continue
            if "NoneType" in s and "len()" in s:
                log_warn("send_gift", f"Баг len(None) в реализации: {e} — пробую следующий вариант")
                continue
            log_warn("send_gift", f"TypeError: {e} — пробую следующий вариант")
            continue
        except Exception as e:
            last_err = e
            log_warn("send_gift", f"Неизвестная ошибка: {e} — короткий повтор")
            await asyncio.sleep(0.4)
            try:
                res = await app.send_gift(chat_id=uname, hide_my_name=ANONYMOUS_GIFTS, **extra)
                return bool(res)
            except Exception as e2:
                last_err = e2
                log_error("send_gift", f"Повтор не помог: {e2}")
                continue

    if last_err:
        raise last_err
    raise RuntimeError("Не удалось подобрать сигнатуру send_gift для этой сборки.")

def send_gift_sync(username: str, gift_id: int, timeout: float = 30.0) -> Tuple[bool, str]:
    if app is None:
        return False, "Pyrogram app is not started"
    fut = asyncio.run_coroutine_threadsafe(_send_gift_once(username, gift_id), loop)
    try:
        res = fut.result(timeout=timeout)
        return True, str(res)
    except Exception as e:
        return False, str(e)

async def _get_stars_balance_once() -> int:
    try:
        bal = await app.get_stars_balance()
        if isinstance(bal, (int, float)):
            return int(bal)
        if isinstance(bal, dict):
            for k in ("balance", "stars", "stars_balance", "count"):
                if k in bal:
                    try:
                        return int(bal[k])
                    except Exception:
                        pass
        try:
            return int(bal)
        except Exception:
            return 0
    except Exception as e:
        logger.debug(f"get_stars_balance failed: {e}", exc_info=True)
        return 0

def get_stars_balance_sync(timeout: float = 10.0) -> int:
    if app is None:
        return 0
    fut = asyncio.run_coroutine_threadsafe(_get_stars_balance_once(), loop)
    try:
        return fut.result(timeout=timeout)
    except Exception as e:
        logger.debug(f"get_stars_balance_sync error: {e}", exc_info=True)
        return 0

# ---------- FUNPAY-ПРОЦЕДУРЫ ----------
def refund_order(account: Account, order_id: int, chat_id: int, ctx: str = "") -> bool:
    try:
        account.refund(order_id)
        log_info(ctx, f"Refund done for order {order_id}")
        try:
            account.send_message(chat_id, "✅ Средства возвращены.")
        except Exception:
            pass
        return True
    except Exception as e:
        log_error(ctx, f"Refund failed for order {order_id}: {short_text(e)}")
        logger.debug("Refund details:", exc_info=True)
        try:
            account.send_message(chat_id, "❌ Ошибка возврата. Свяжитесь с админом.")
        except Exception:
            pass
        return False

def _list_my_subcat_lots(account: Account, subcat_id: int):
    try:
        lots = account.get_my_subcategory_lots(subcat_id)
        log_info("", f"Найдено {len(lots)} лотов в подкатегории {subcat_id}.")
        return lots
    except Exception:
        logger.debug("get_my_subcategory_lots failed, пробую запасной путь", exc_info=True)

    try:
        categories = account.get_categories()
        result = []
        for cat in categories:
            for subcat in getattr(cat, "subcategories", []) or []:
                if getattr(subcat, "id", None) == subcat_id:
                    result.extend(getattr(subcat, "lots", []) or [])
        log_info("", f"Запасной путь: найдено {len(result)} лотов в subcat_id={subcat_id}.")
        return result
    except Exception as e:
        log_error("", f"Не удалось получить список лотов: {short_text(e)}")
        logger.debug("Подробности получения лотов:", exc_info=True)
        return []

def update_lot_state(account: Account, lot, active: bool) -> bool:
    attempts = 3
    while attempts:
        try:
            lot_fields = account.get_lot_fields(lot.id)
            if getattr(lot_fields, "active", None) == active:
                log_info("", f"Лот {getattr(lot,'id', '?')} уже active={active}")
                return True
            lot_fields.active = active
            account.save_lot(lot_fields)
            log_warn("", f"Лот {getattr(lot,'id','?')} изменён: active={active}")
            return True
        except Exception as e:
            log_error("", f"Ошибка при изменении лота {getattr(lot,'id','?')}: {short_text(e)}")
            logger.debug("Подробности update_lot_state:", exc_info=True)
            attempts -= 1
            time.sleep(min(0.5 * (3 - attempts), 1.5))
    log_error("", f"Не удалось изменить лот {getattr(lot,'id','?')} (исчерпаны попытки)")
    return False

def deactivate_lots(account: Account, subcat_id: int):
    log_warn("", f"Запускаю деактивацию лотов в подкатегории {subcat_id}...")
    lots = _list_my_subcat_lots(account, subcat_id)
    if not lots:
        log_info("", "Лоты не найдены — пропускаю деактивацию.")
        return
    affected: List[str] = []
    consecutive_errors = 0
    for lot in lots:
        try:
            fields = account.get_lot_fields(lot.id)
            is_active = bool(getattr(fields, "active", False))
            title = short_text(_safe_attr(lot, "title", "description", default=str(getattr(lot, "id", "?"))), 80)
            if not is_active:
                log_info("", f"Лот уже выключен: {title} (id={lot.id})")
                continue
            ok = update_lot_state(account, lot, active=False)
            if ok:
                affected.append(f"{title} (id={lot.id})")
                consecutive_errors = 0
        except Exception as e:
            log_error("", f"Ошибка при деактивации лота {getattr(lot,'id','?')}: {short_text(e)}")
            logger.debug("Подробности деактивации лота:", exc_info=True)
            consecutive_errors += 1
        if consecutive_errors:
            pause = min(0.2 * consecutive_errors, 1.5)
            time.sleep(pause)
    if affected:
        log_warn("", "Деактивированы лоты:\n- " + "\n- ".join(affected))
    else:
        log_info("", "Не было активных лотов для деактивации.")

def classify_send_error(info: str) -> str:
    if not info:
        return "other"
    lower = info.lower()

    if "balance_too_low" in lower or "balance too low" in lower or "not enough stars" in lower or "недостат" in lower:
        return "balance_low"
    if "400 balance_too_low" in lower or "payments.sendstarsform" in lower:
        return "balance_low"

    if "username_not_occupied" in lower or "contacts.resolveusername" in lower or "provided username is not occupied" in lower:
        return "username_not_found"
    if "peer_id_invalid" in lower:
        return "username_not_found"

    if "flood" in lower or "too many requests" in lower or "slowmode" in lower:
        return "flood"

    return "other"

# ---------- ОСНОВНОЙ ЦИКЛ ----------
def main():
    if not GOLDEN_KEY:
        log_error("", "В .env должен быть FUNPAY_AUTH_TOKEN")
        return

    if BAD_TOKENS:
        log_warn("", f"CATEGORY_ID(S) содержит нечисловые значения и они будут проигнорированы: {BAD_TOKENS}")
    log_info("", f"Категории: {CATEGORY_IDS_LIST} (primary={PRIMARY_CATEGORY_ID})")

    if AUTO_REFUND_RAW is None:
        log_warn("", "AUTO_REFUND не задан в .env → использую по умолчанию: ON")
    else:
        log_info("", f"AUTO_REFUND задан пользователем: {AUTO_REFUND_RAW} → эффективно: {AUTO_REFUND}")
    if AUTO_DEACTIVATE_RAW is None:
        log_warn("", "AUTO_DEАКТИВATE не задан в .env → использую по умолчанию: ON")
    else:
        log_info("", f"AUTO_DEACTIVATE задан пользователем: {AUTO_DEACTIVATE_RAW} → эффективно: {AUTO_DEACTIVATE}")

    if ANONYMOUS_GIFTS_RAW is None:
        log_warn("", "ANONYMOUS_GIFTS не задан в .env → использую по умолчанию: OFF (не анонимно)")
    else:
        log_info("", f"ANONYMOUS_GIFTS задан пользователем: {ANONYMOUS_GIFTS_RAW} → эффективно: {ANONYMOUS_GIFTS}")

    log_info("", f"Итого настройки: AUTO_REFUND={AUTO_REFUND}, AUTO_DEACTIVATE={AUTO_DEACTIVATE}, "
                 f"ANONYMOUS_GIFTS={ANONYMOUS_GIFTS}, COOLDOWN={COOLDOWN_SECONDS}")

    account = Account(GOLDEN_KEY)
    account.get()
    if not getattr(account, "username", None):
        log_error("", "Не удалось авторизоваться в FunPay. Проверьте ключ.")
        return

    log_info("", f"Авторизован на FunPay как @{account.username}")
    runner = Runner(account)
    log_info("", "Ожидаю события от FunPay...")

    for event in runner.listen(requests_delay=3.0):
        try:
            now = time.time()

            if isinstance(event, NewOrderEvent):
                order = account.get_order(event.order.id)
                buyer_id = getattr(order, "buyer_id", None)
                if buyer_id is None:
                    continue

                if now - _last_reply_by_buyer.get(buyer_id, 0.0) < COOLDOWN_SECONDS:
                    continue

                subcat_id, _ = get_subcategory_id_safe(order, account)
                if subcat_id not in ALLOWED_CATEGORY_IDS:
                    continue

                desc = (
                    getattr(order, "full_description", None)
                    or getattr(order, "short_description", None)
                    or getattr(order, "title", None)
                    or ""
                )
                gift_num = parse_gift_num(desc)
                if not gift_num:
                    ctx = f"Buyer {buyer_id} @{getattr(order, 'buyer_username', '')}"
                    account.send_message(
                        order.chat_id,
                        "❌ В описании заказа отсутствует обязательный параметр gift_tg. "
                        "Произошла ошибка при обработке заказа — сейчас оформляем возврат средств.",
                    )
                    log_warn(ctx, "Отсутствует gift_tg в описании → возврат (если включён)")
                    if AUTO_REFUND:
                        try:
                            refund_order(account, order.id, order.chat_id, ctx=ctx)
                        except Exception as e:
                            log_error(ctx, f"Ошибка при оформлении возврата: {short_text(e)}")
                    _last_reply_by_buyer[buyer_id] = now
                    continue

                gift = GIFTS.get(gift_num)
                if not gift:
                    account.send_message(order.chat_id, f"Номер gift_tg:{gift_num} отсутствует. Укажите корректный номер.")
                    log_info(f"Buyer {buyer_id}", f"gift_tg:{gift_num} не найден в gifts.json.")
                    _last_reply_by_buyer[buyer_id] = now
                    continue

                waiting[buyer_id] = {
                    "chat_id": order.chat_id,
                    "order_id": order.id,
                    "gift_num": gift_num,
                    "gift": gift,
                    "state": "awaiting_nick",
                    "temp_nick": None,
                    "subcat_id": subcat_id,
                }
                account.send_message(
                    order.chat_id,
                    f"Спасибо за покупку! К выдаче: {gift['title']} ({gift['price']}⭐).\n"
                    f"Пришлите ваш Telegram-тег (пример: @username).",
                )
                log_info(pretty_order_context(order, gift=gift), "Ждём ник.")
                _last_reply_by_buyer[buyer_id] = now

            elif isinstance(event, NewMessageEvent):
                msg = event.message
                chat_id = msg.chat_id
                author_id = msg.author_id
                text = (msg.text or "").strip()

                if now - _last_reply_by_buyer.get(author_id, 0.0) < COOLDOWN_SECONDS:
                    continue

                if author_id == getattr(account, "id", None) or author_id not in waiting:
                    continue

                st = waiting[author_id]
                gift = st["gift"]
                order_id = st["order_id"]

                if st["state"] == "awaiting_nick":
                    if not nick_looks_valid(text):
                        account.send_message(chat_id, "Неверный тег. Отправьте в виде @username (5–32 символа).")
                        _last_reply_by_buyer[author_id] = now
                        continue
                    st["temp_nick"] = text.strip()
                    st["state"] = "awaiting_confirmation"
                    account.send_message(
                        chat_id,
                        f'Вы указали {st["temp_nick"]} для получения {gift["title"]} ({gift["price"]}⭐).\n'
                        f'Если верно — напишите "+"'
                    )
                    log_info(pretty_order_context(None, buyer_id=author_id, gift=gift), "Ник получен — ждём подтверждение.")
                    _last_reply_by_buyer[author_id] = now
                    continue

                if st["state"] == "awaiting_confirmation":
                    if text == "+":
                        username = st["temp_nick"]
                        account.send_message(chat_id, f"Отправляю {gift['title']} пользователю {username}...")
                        log_info(pretty_order_context(None, buyer_id=author_id, gift=gift), f"Отправка -> {username}")

                        ok, info = send_gift_sync(username, gift_id=gift["id"])
                        ctx = pretty_order_context(None, buyer_id=author_id, gift=gift)

                        if ok:
                            time.sleep(0.4)
                            account.send_message(chat_id, f"🎉 Подарок успешно отправлен на {username}!")
                            order_url = f"https://funpay.com/orders/{order_id}/"
                            account.send_message(
                                chat_id,
                                "🙏 Пожалуйста, подтвердите выполнение заказа и оставьте отзыв — это очень помогает! "
                                f"Ссылка на заказ: {order_url}"
                            )
                            log_info(ctx, f"УСПЕХ -> {username} (review requested)")
                        else:
                            full_err = str(info)
                            log_error(ctx, f"Полная ошибка отправки: {full_err}")
                            kind = classify_send_error(full_err)

                            if kind == "balance_low":
                                account.send_message(chat_id, "❌ Ошибка при отправке подарка: недостаточно звёзд.")
                                log_warn(ctx, "BALANCE_TOO_LOW")
                                if AUTO_DEACTIVATE:
                                    for cid in CATEGORY_IDS_LIST:
                                        try:
                                            deactivate_lots(account, cid)
                                        except Exception as e:
                                            log_error(ctx, f"Ошибка деактивации в {cid}: {e}")
                                if AUTO_REFUND:
                                    try:
                                        refund_order(account, order_id, chat_id, ctx=ctx)
                                    except Exception as e:
                                        log_error(ctx, f"Ошибка при возврате: {short_text(e)}")
                                else:
                                    account.send_message(chat_id, "⚠️ Автоматический возврат выключен. Свяжитесь с админом для возврата.")
                            elif kind == "username_not_found":
                                account.send_message(chat_id, "❌ Неверный ник или такой пользователь не найден.")
                                log_warn(ctx, "USERNAME_NOT_OCCUPIED")
                                if AUTO_REFUND:
                                    try:
                                        refund_order(account, order_id, chat_id, ctx=ctx)
                                    except Exception as e:
                                        log_error(ctx, f"Ошибка при возврате: {short_text(e)}")
                                else:
                                    account.send_message(chat_id, "⚠️ Автоматический возврат выключен. Свяжитесь с админом для возврата.")
                            elif kind == "flood":
                                account.send_message(chat_id, "⚠️ Слишком много запросов. Выполню отправку чуть позже.")
                                log_warn(ctx, "FLOOD_WAIT")
                            else:
                                account.send_message(chat_id, "❌ Неизвестная ошибка при отправке подарка.")
                                log_warn(ctx, "UNKNOWN_ERROR")
                                if AUTO_REFUND:
                                    try:
                                        refund_order(account, order_id, chat_id, ctx=ctx)
                                    except Exception as e:
                                        log_error(ctx, f"Ошибка при возврате: {short_text(e)}")

                        waiting.pop(author_id, None)
                        _last_reply_by_buyer[author_id] = now
                    else:
                        if nick_looks_valid(text):
                            st["temp_nick"] = text.strip()
                            account.send_message(chat_id, f'Ник обновлён: {st["temp_nick"]}. Если верно — напишите "+".')
                            log_info(pretty_order_context(None, buyer_id=author_id, gift=gift), f"Ник обновлён -> {st['temp_nick']}")
                        else:
                            account.send_message(chat_id, "Для подтверждения отправьте «+». Либо пришлите новый ник @username.")
                        _last_reply_by_buyer[author_id] = now
                        continue

        except Exception as e:
            log_error("", f"Ошибка обработки события: {short_text(e)}")
            logger.debug("Подробности ошибки обработки события:", exc_info=True)

if __name__ == "__main__":
    main()
