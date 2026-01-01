import os
import re
import json
import time
import asyncio
import logging
import threading
import colorlog
from contextlib import suppress
from typing import Optional, Tuple, List, Any, Dict

from dotenv import load_dotenv
from pyrogram import Client
from FunPayAPI import Account
from FunPayAPI.updater.runner import Runner
from FunPayAPI.updater.events import NewOrderEvent, NewMessageEvent

try:
    from gift_sets import resolve_to_gift_ids, get_required_stars, load_sets
    GIFT_SETS_AVAILABLE = True
except Exception:
    GIFT_SETS_AVAILABLE = False

if not hasattr(Client, "send_gift"):
    raise RuntimeError(
        "Установлен неподдерживаемый пакет 'pyrogram'. Нужен форк с поддержкой Stars.\n"
        "Используйте: pip uninstall -y pyrogram && pip install -U pyrofork tgcrypto"
    )

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

PRECHECK_BALANCE_RAW = _env_raw("PRECHECK_BALANCE")
PRECHECK_BALANCE = _env_bool("PRECHECK_BALANCE", True)

CREATOR_NAME = os.getenv("CREATOR_NAME", "@tinechelovec")
CREATOR_URL = os.getenv("CREATOR_URL", "https://t.me/tinechelovec")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/by_thc")
GITHUB_URL = os.getenv("GITHUB_URL", "https://github.com/tinechelovec/Funpay-Telegram-Gifts")
HELP_URL = os.getenv("HELP_URL", "https://teletype.in/@tinechelovec/Funpay-Telegram-Gifts")
BANNER_NOTE = os.getenv(
    "BANNER_NOTE",
    "Бот бесплатный и с открытым исходным кодом на GitHub. "
    "Создатель бота его НЕ продаёт. Если вы где-то видите платную версию — "
    "это решение перепродавца, к автору отношения не имеет."
)

LOG_NAME = "FunPay-Gifts"

try:
    logger = colorlog.getLogger(LOG_NAME)
    logger.setLevel(logging.INFO)

    console_handler = colorlog.StreamHandler()
    console_formatter = colorlog.ColoredFormatter(
        fmt="%(log_color)s[%(levelname)-5s]%(reset)s %(blue)s" + LOG_NAME + "%(reset)s: %(message)s",
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "red,bg_white",
        },
    )
    console_handler.setFormatter(console_formatter)

    file_handler = logging.FileHandler("log.txt", mode="a", encoding="utf-8")
    file_formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-5s] " + LOG_NAME + ": %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)

    logger.handlers.clear()
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

except Exception:
    logger = logging.getLogger(LOG_NAME)
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-5s] " + LOG_NAME + ": %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    fh = logging.FileHandler("log.txt", mode="a", encoding="utf-8")
    fh.setFormatter(formatter)

    logger.handlers.clear()
    logger.addHandler(ch)
    logger.addHandler(fh)

RED = "\033[31m"
BRIGHT_CYAN = "\033[96m"
RESET = "\033[0m"

def _log_banner_red():
    border = "═" * 85

    logger.info(f"{RED}{border}{RESET}")
    logger.info(f"{RED}Информация о проекте / FunPay Gifts{RESET}")
    logger.info(f"{RED}{border}{RESET}")

    line = f"{RED}Создатель: {CREATOR_NAME}"
    if CREATOR_URL:
        line += f" | Контакт: {BRIGHT_CYAN}{CREATOR_URL}{RED}"
    logger.info(line + RESET)

    if CHANNEL_URL:
        logger.info(f"{RED}Канал: {BRIGHT_CYAN}{CHANNEL_URL}{RESET}")

    if GITHUB_URL:
        logger.info(f"{RED}GitHub: {BRIGHT_CYAN}{GITHUB_URL}{RESET}")
    
    if HELP_URL:
        logger.info(f"{RED}Инструкция: {BRIGHT_CYAN}{HELP_URL}{RESET}")

    logger.info(f"{RED}Дисклеймер: {BANNER_NOTE}{RESET}")
    logger.info(f"{RED}{border}{RESET}")

def log_info(ctx: str, msg: str):
    logger.info(f"{ctx + ' | ' if ctx else ''}{msg}")

def log_warn(ctx: str, msg: str):
    logger.warning(f"{ctx + ' | ' if ctx else ''}{msg}")

def log_error(ctx: str, msg: str):
    logger.error(f"{ctx + ' | ' if ctx else ''}{msg}")

with open("gifts.json", "r", encoding="utf-8") as f:
    GIFTS: Dict[str, dict] = json.load(f)

loop = asyncio.new_event_loop()
app: Optional[Client] = None
_app_started = threading.Event()

_pyro_gate = threading.Semaphore(1)
_restarts = 0
_completed_buyers: set[int] = set()

waiting: dict[int, dict] = {}
_last_reply_by_buyer: dict[int, float] = {}

def _build_client() -> Client:
    common = dict(workdir="sessions", no_updates=True)
    if API_ID and API_HASH:
        return Client("stars", api_id=API_ID, api_hash=API_HASH, **common)
    else:
        return Client("stars", **common)

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

async def _ping_pyro() -> bool:
    try:
        await app.get_me()
        return True
    except Exception:
        return False

async def _restart_pyro_once():
    with suppress(Exception):
        await app.stop()
    await asyncio.sleep(0.2)
    await app.start()
    log_warn("pyro", "Pyrogram перезапущен")

def _ensure_pyro_alive_sync() -> bool:
    if app is None:
        return False
    fut = asyncio.run_coroutine_threadsafe(_ping_pyro(), loop)
    try:
        ok = fut.result(timeout=5.0)
    except Exception:
        ok = False
    if ok:
        return True

    global _restarts
    _restarts += 1
    fut2 = asyncio.run_coroutine_threadsafe(_restart_pyro_once(), loop)
    with suppress(Exception):
        fut2.result(timeout=20.0)
    fut3 = asyncio.run_coroutine_threadsafe(_ping_pyro(), loop)
    try:
        return fut3.result(timeout=5.0)
    except Exception:
        return False

_log_banner_red()
threading.Thread(target=_thread_target, daemon=True).start()

_app_started.wait(timeout=10.0)
if not _app_started.is_set():
    log_warn("", "Pyrogram не успел стартовать за 10 сек. Продолжаю — вызовы подождут через future.")

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

def parse_quantity(order, desc: str) -> int:
    for name in ("quantity", "count", "qty", "amount", "items_count"):
        try:
            v = getattr(order, name, None)
            if isinstance(v, (int, float)) and v > 0:
                return int(v)
        except Exception:
            pass
    m = re.search(r"gift_qty\s*:\s*(\d+)", desc or "", flags=re.IGNORECASE)
    if m:
        try:
            q = int(m.group(1))
            return max(1, q)
        except Exception:
            pass
    return 1

def parse_recipients(text: str) -> List[str]:
    parts = [p.strip() for p in re.split(r"[,\s;]+", text or "") if p.strip()]
    res: List[str] = []
    for p in parts:
        p = p if p.startswith("@") else "@" + p
        if nick_looks_valid(p):
            res.append(p)

    seen = set()
    uniq: List[str] = []
    for u in res:
        key = u.lower()
        if key not in seen:
            uniq.append(u)
            seen.add(key)
    return uniq

def parse_single_recipient(text: str) -> Optional[str]:
    recips = parse_recipients(text)
    if len(recips) != 1:
        return None
    return recips[0]

def expand_assignment(recipients: List[str], qty: int) -> List[str]:
    if not recipients:
        return []
    if len(recipients) == 1:
        return [recipients[0]] * qty
    out: List[str] = []
    i = 0
    for _ in range(qty):
        out.append(recipients[i % len(recipients)])
        i += 1
    return out

def _format_plan(assign: List[str]) -> str:
    preview: Dict[str, int] = {}
    for u in assign:
        preview[u] = preview.get(u, 0) + 1
    return ", ".join([f"{u} ×{c}" for u, c in preview.items()])

def _choice_menu(options: List[str]) -> Tuple[List[str], str]:
    normalized: List[str] = []
    lines: List[str] = []
    for gk in options or []:
        gk = str(gk)
        g = GIFTS.get(gk)
        if not g:
            continue
        normalized.append(gk)
        title = g.get("title", gk)
        lines.append(f"{len(normalized)}) {title}")
    return normalized, ("\n".join(lines) if lines else "—")

def _parse_choice_index(text: str, max_n: int) -> Optional[int]:
    nums = re.findall(r"\d+", text or "")
    if not nums:
        return None
    try:
        n = int(nums[0])
    except Exception:
        return None
    if 1 <= n <= max_n:
        return n
    return None

async def _get_stars_balance_once() -> Optional[int]:
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
        return int(bal)
    except Exception as e:
        logger.debug(f"get_stars_balance failed: {e}", exc_info=True)
        return None

def get_stars_balance_sync(timeout: float = 10.0) -> Optional[int]:
    if app is None:
        return None
    if not _ensure_pyro_alive_sync():
        return None
    with _pyro_gate:
        fut = asyncio.run_coroutine_threadsafe(_get_stars_balance_once(), loop)
        try:
            res = fut.result(timeout=timeout)
            return res if isinstance(res, int) and res >= 0 else None
        except Exception as e:
            logger.debug(f"get_stars_balance_sync error: {e}", exc_info=True)
            return None

def try_partial_refund(account: Account, order_id: int, units: int, gift: dict, chat_id: int, ctx: str = "") -> bool:
    total_stars = int(units) * int(gift.get("price", 0))
    if units <= 0 or total_stars <= 0:
        return True
    try:
        account.refund(order_id, amount=total_stars)
        log_info(ctx, f"Partial refund by amount done: {total_stars}⭐ for {units} pcs")
        try:
            account.send_message(chat_id, f"✅ Возврат за неотправленные позиции: {units} шт. → {total_stars}⭐.")
        except Exception:
            pass
        return True
    except TypeError:
        pass
    except Exception as e:
        log_warn(ctx, f"Partial refund(amount) failed: {short_text(e)}")
    try:
        account.refund_partial(order_id, units)
        log_info(ctx, f"Partial refund by units done: {units}")
        try:
            account.send_message(chat_id, f"✅ Возврат за {units} шт. оформлен.")
        except Exception:
            pass
        return True
    except Exception as e:
        log_warn(ctx, f"Partial refund(units) failed: {short_text(e)}")
    try:
        account.send_message(chat_id, "⚠️ Автоматический частичный возврат недоступен. Свяжитесь с админом по остатку.")
    except Exception:
        pass
    log_error(ctx, "Partial refund not supported by API")
    return False

async def _send_gift_once(username: str, gift_id: int) -> bool:
    uname = username.lstrip("@")
    await asyncio.sleep(0.05)
    attempts = [{"gift_id": gift_id}, {"star_gift_id": gift_id}]
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
            if "unexpected keyword argument" in s or ("NoneType" in s and "len()" in s):
                log_warn("send_gift", f"Сигнатура/баг: {e} — пробую другой вариант")
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
    if not _ensure_pyro_alive_sync():
        return False, "Pyrogram not connected"
    with _pyro_gate:
        fut = asyncio.run_coroutine_threadsafe(_send_gift_once(username, gift_id), loop)
        try:
            res = fut.result(timeout=timeout)
            return True, str(res)
        except Exception as e:
            return False, str(e)

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
    if "connection lost" in lower or "socket.send()" in lower or "nonetype' object has no attribute 'read'" in lower \
       or "read() called while another coroutine" in lower:
        return "network"
    return "other"

def resolve_item(key: str) -> Tuple[List[int], int, str, bool, bool, List[str]]:
    key_s = str(key)

    if GIFT_SETS_AVAILABLE:
        try:
            sets_map = load_sets() or {}
            if key_s in sets_map:
                s = sets_map[key_s]
                title = getattr(s, "title", None) or f"Набор #{key_s}"
                mode = str(getattr(s, "mode", "fixed")).lower().strip()
                if mode == "choice":
                    options = [str(x) for x in (getattr(s, "options", None) or [])]
                    return [], 0, title, True, True, options

                ids = [int(x) for x in resolve_to_gift_ids(key_s)]
                price = int(get_required_stars(key_s))
                return ids, price, title, True, False, []
        except Exception:
            pass

    g = GIFTS.get(key_s)
    if not g:
        raise KeyError("not_found")
    return [int(g["id"])], int(g.get("price", 0)), g.get("title", f"Подарок #{key_s}"), False, False, []

def _choice_max_price(options: List[str]) -> int:
    prices = []
    for k in options:
        g = GIFTS.get(str(k))
        if g:
            try:
                prices.append(int(g.get("price", 0) or 0))
            except Exception:
                pass
    prices = [p for p in prices if p > 0]
    return max(prices) if prices else 0

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
        log_warn("", "AUTO_DEACTIVATE не задан в .env → использую по умолчанию: ON")
    else:
        log_info("", f"AUTO_DEACTIVATE задан пользователем: {AUTO_DEACTIVATE_RAW} → эффективно: {AUTO_DEACTIVATE}")

    if ANONYMOUS_GIFTS_RAW is None:
        log_warn("", "ANONYMOUS_GIFTS не задан в .env → использую по умолчанию: OFF (не анонимно)")
    else:
        log_info("", f"ANONYMOUS_GIFTS задан пользователем: {ANONYMOUS_GIFTS_RAW} → эффективно: {ANONYMOUS_GIFTS}")

    if PRECHECK_BALANCE_RAW is None:
        log_warn("", "PRECHECK_BALANCE не задан в .env → использую по умолчанию: ON")
    else:
        log_info("", f"PRECHECK_BALANCE задан пользователем: {PRECHECK_BALANCE_RAW} → эффективно: {PRECHECK_BALANCE}")

    log_info(
        "",
        f"Итого настройки: AUTO_REFUND={AUTO_REFUND}, AUTO_DEACTIVATE={AUTO_DEACTIVATE}, "
        f"ANONYMOUS_GIFTS={ANONYMOUS_GIFTS}, PRECHECK_BALANCE={PRECHECK_BALANCE}, COOLDOWN={COOLDOWN_SECONDS}",
    )

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

                _completed_buyers.discard(buyer_id)

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
                    log_error(ctx, "❌ Отсутствует обязательный параметр gift_tg в описании заказа. Заказ пропущен без ответа в чат.")
                    continue

                try:
                    ids_per_unit, price_per_unit, item_title, is_set_any, is_choice, choice_options = resolve_item(gift_num)
                except KeyError:
                    account.send_message(order.chat_id, f"Номер gift_tg:{gift_num} отсутствует. Укажите корректный номер.")
                    log_info(f"Buyer {buyer_id}", f"gift_tg:{gift_num} не найден ни в gifts.json, ни в наборах.")
                    _last_reply_by_buyer[buyer_id] = now
                    continue

                qty = parse_quantity(order, desc)

                if qty <= 0:
                    account.send_message(order.chat_id, "❌ Неверное количество. Свяжитесь с админом.")
                    _last_reply_by_buyer[buyer_id] = now
                    continue

                shown_price = "?"
                if not is_choice:
                    shown_price = str(int(price_per_unit))
                ctx_purchase = pretty_order_context(
                    order,
                    gift={"title": item_title, "price": shown_price, "id": ids_per_unit[0] if ids_per_unit else "?"}
                )
                log_info(ctx_purchase, f"Новый заказ принят. qty={qty}, is_choice={is_choice}, gift_tg={gift_num}")

                bal = None
                need_all = 0

                if is_choice:
                    max_price = _choice_max_price(choice_options)
                    need_all = max_price * qty if max_price > 0 else 0
                else:
                    price = int(price_per_unit)
                    need_all = price * qty

                if PRECHECK_BALANCE and need_all > 0:
                    bal = get_stars_balance_sync()

                    if isinstance(bal, int) and bal < need_all:
                        if AUTO_REFUND:
                            account.send_message(order.chat_id, "❌ У продавца недостаточно средств.\nСейчас оформлю полный возврат заказа.")
                        else:
                            account.send_message(order.chat_id, "❌ У продавца недостаточно средств.\nОжидайте ответа продавца.")

                        log_warn(ctx_purchase, f"BALANCE_TOO_LOW pre-check: bal={bal}, need_all={need_all}, qty={qty}")

                        if AUTO_DEACTIVATE:
                            for cid in CATEGORY_IDS_LIST:
                                try:
                                    deactivate_lots(account, cid)
                                except Exception as e:
                                    log_error(ctx_purchase, f"Ошибка деактивации в {cid}: {e}")

                        if AUTO_REFUND:
                            refund_order(account, order.id, order.chat_id, ctx=ctx_purchase)

                        _last_reply_by_buyer[buyer_id] = now
                        continue

                    elif bal is None:
                        account.send_message(
                            order.chat_id,
                            "⚠️ Временная ошибка связи с Telegram Stars — проверка баланса недоступна. "
                            "Продолжаю обработку заказа; если звёзд не хватит при отправке — сообщу отдельно."
                        )

                log_info(ctx_purchase, f"need_all={need_all}, bal={bal}")

                waiting[buyer_id] = {
                    "chat_id": order.chat_id,
                    "order_id": order.id,
                    "gift_num": gift_num,
                    "gift_title": item_title,

                    "ids_per_unit": ids_per_unit,
                    "price": int(price_per_unit) if not is_choice else 0,

                    "is_set_any": bool(is_set_any),
                    "is_choice": bool(is_choice),
                    "choice_options": [str(x) for x in (choice_options or [])],

                    "qty": int(qty),
                    "subcat_id": subcat_id,

                    "state": "awaiting_choice_nick" if is_choice else "awaiting_nicks",
                    "recipients": [],
                    "choice_recipient": None,

                    "choice_selected_key": None,
                    "choice_selected_title": None,
                    "choice_selected_gift_id": None,
                    "choice_selected_price": None,
                }

                if is_choice:
                    account.send_message(
                        order.chat_id,
                        (
                            f"✅ Спасибо за покупку!\n"
                            f"🧾 Товар: {item_title} ×{qty} (выбор подарка)\n\n"
                            "👤 Пришлите никнейм получателя (@username).\n"
                        ),
                    )
                else:
                    shown_price = f"{int(price_per_unit)}⭐"
                    account.send_message(
                        order.chat_id,
                        (
                            f"✅ Спасибо за покупку!\n"
                            f"🧾 К выдаче: {item_title} ×{qty} по {shown_price}.\n\n"
                            "👤 Пришлите теги получателей:\n"
                            "• один @username — тогда вся выдача уйдёт ему\n"
                            "• или список через запятую/пробел/перенос: @u1, @u2, @u3"
                        ),
                    )

                log_info(ctx_purchase, f"Состояние создано: state={waiting[buyer_id]['state']}")
                _last_reply_by_buyer[buyer_id] = now
                continue

            elif isinstance(event, NewMessageEvent):
                msg = event.message
                chat_id = msg.chat_id
                author_id = msg.author_id
                text = (msg.text or "").strip()

                if author_id in _completed_buyers:
                    continue

                if now - _last_reply_by_buyer.get(author_id, 0.0) < COOLDOWN_SECONDS:
                    continue
                if author_id == getattr(account, "id", None) or author_id not in waiting:
                    continue

                st = waiting[author_id]
                order_id = st["order_id"]
                qty = int(st.get("qty", 1))
                is_choice = bool(st.get("is_choice"))

                ctx_user = pretty_order_context(None, buyer_id=author_id, gift={"title": st.get("gift_title", "?"), "price": "?", "id": "?"})

                if is_choice:
                    maybe_nick = parse_single_recipient(text)
                    if maybe_nick and st["state"] in ("awaiting_choice_pick", "awaiting_choice_confirmation"):
                        st["choice_recipient"] = maybe_nick
                        log_info(ctx_user, f"CHOICE: получатель обновлён -> {maybe_nick}")
                        if st.get("choice_selected_title"):
                            account.send_message(
                                chat_id,
                                f"✅ Получатель обновлён: {maybe_nick}\n"
                                f"🎁 Выбрано: {st['choice_selected_title']} ×{qty}\n"
                                "Если всё верно — отправьте «+»."
                            )
                        else:
                            account.send_message(chat_id, f"✅ Получатель: {maybe_nick}\nТеперь выберите номер подарка.")
                        _last_reply_by_buyer[author_id] = now
                        continue

                    if st["state"] == "awaiting_choice_nick":
                        recip = parse_single_recipient(text)
                        if not recip:
                            account.send_message(chat_id, "❌ Пришлите ровно ОДИН никнейм (@username).")
                            _last_reply_by_buyer[author_id] = now
                            continue

                        st["choice_recipient"] = recip
                        st["state"] = "awaiting_choice_pick"
                        log_info(ctx_user, f"CHOICE: получатель -> {recip}")

                        options_raw = list(st.get("choice_options") or [])
                        options_norm, menu = _choice_menu(options_raw)
                        st["choice_options"] = options_norm

                        if not options_norm:
                            account.send_message(chat_id, "❌ Ошибка: список вариантов пуст. Свяжитесь с админом.")
                            log_error(ctx_user, "CHOICE: пустые варианты")
                            if AUTO_REFUND:
                                refund_order(account, order_id, chat_id, ctx="choice-empty-options")
                            waiting.pop(author_id, None)
                            _last_reply_by_buyer[author_id] = now
                            continue

                        account.send_message(
                            chat_id,
                            (
                                "🎁 Выберите подарок, который хотите получить.\n"
                                "Напишите номер из списка:\n\n"
                                f"{menu}"
                            )
                        )
                        _last_reply_by_buyer[author_id] = now
                        continue

                    if st["state"] == "awaiting_choice_pick":
                        options_norm = list(st.get("choice_options") or [])
                        idx = _parse_choice_index(text, max_n=len(options_norm))
                        if idx is None:
                            _, menu = _choice_menu(options_norm)
                            account.send_message(chat_id, f"❌ Неверный номер. Выберите 1–{len(options_norm)}:\n\n{menu}")
                            _last_reply_by_buyer[author_id] = now
                            continue

                        gift_key = options_norm[idx - 1]
                        g = GIFTS.get(str(gift_key))
                        if not g:
                            account.send_message(chat_id, "❌ Ошибка: выбранный подарок отсутствует в gifts.json. Свяжитесь с админом.")
                            log_error(ctx_user, f"CHOICE: gift_key={gift_key} отсутствует в gifts.json")
                            if AUTO_REFUND:
                                refund_order(account, order_id, chat_id, ctx="choice-gift-missing")
                            waiting.pop(author_id, None)
                            _last_reply_by_buyer[author_id] = now
                            continue

                        recipient = st.get("choice_recipient")
                        if not recipient:
                            st["state"] = "awaiting_choice_nick"
                            account.send_message(chat_id, "❌ Сначала пришлите никнейм получателя (@username).")
                            _last_reply_by_buyer[author_id] = now
                            continue

                        gift_id = int(g["id"])
                        gift_title = g.get("title", f"Подарок {gift_key}")
                        price = int(g.get("price", 0) or 0)

                        st["choice_selected_key"] = gift_key
                        st["choice_selected_title"] = gift_title
                        st["choice_selected_gift_id"] = gift_id
                        st["choice_selected_price"] = price
                        st["state"] = "awaiting_choice_confirmation"

                        log_info(ctx_user, f"CHOICE: выбрано -> {gift_title} (gift_key={gift_key}, gift_id={gift_id}), qty={qty}")

                        account.send_message(
                            chat_id,
                            (
                                f"✅ Вы выбрали: {gift_title}\n"
                                f"👤 Получатель: {recipient}\n"
                                f"📦 Количество: {qty}\n\n"
                                "Если всё верно — отправьте «+».\n"
                                "Чтобы изменить выбор — отправьте другой номер."
                            )
                        )
                        _last_reply_by_buyer[author_id] = now
                        continue

                    if st["state"] == "awaiting_choice_confirmation":
                        if text != "+":
                            options_norm = list(st.get("choice_options") or [])
                            idx = _parse_choice_index(text, max_n=len(options_norm))
                            if idx is not None:
                                gift_key = options_norm[idx - 1]
                                g = GIFTS.get(str(gift_key))
                                if not g:
                                    account.send_message(chat_id, "❌ Ошибка: выбранный подарок отсутствует. Выберите другой номер.")
                                    _last_reply_by_buyer[author_id] = now
                                    continue

                                gift_id = int(g["id"])
                                gift_title = g.get("title", f"Подарок {gift_key}")
                                price = int(g.get("price", 0) or 0)

                                st["choice_selected_key"] = gift_key
                                st["choice_selected_title"] = gift_title
                                st["choice_selected_gift_id"] = gift_id
                                st["choice_selected_price"] = price

                                log_info(ctx_user, f"CHOICE: выбор обновлён -> {gift_title} (gift_key={gift_key})")

                                recipient = st.get("choice_recipient") or "—"
                                account.send_message(
                                    chat_id,
                                    (
                                        f"✅ Выбор обновлён: {gift_title}\n"
                                        f"👤 Получатель: {recipient}\n"
                                        f"📦 Количество: {qty}\n\n"
                                        "Подтвердите отправку: «+»."
                                    )
                                )
                                _last_reply_by_buyer[author_id] = now
                                continue

                            account.send_message(chat_id, "Для отправки подтвердите «+». Или отправьте номер, чтобы изменить выбор.")
                            _last_reply_by_buyer[author_id] = now
                            continue

                        recipient = st.get("choice_recipient")
                        gift_id = st.get("choice_selected_gift_id")
                        gift_title = st.get("choice_selected_title")
                        price = int(st.get("choice_selected_price") or 0)

                        if not recipient:
                            st["state"] = "awaiting_choice_nick"
                            account.send_message(chat_id, "❌ Сначала пришлите никнейм получателя (@username).")
                            _last_reply_by_buyer[author_id] = now
                            continue

                        if not gift_id or not gift_title:
                            st["state"] = "awaiting_choice_pick"
                            options_norm = list(st.get("choice_options") or [])
                            _, menu = _choice_menu(options_norm)
                            account.send_message(chat_id, f"❌ Сначала выберите подарок:\n\n{menu}")
                            _last_reply_by_buyer[author_id] = now
                            continue

                        account.send_message(chat_id, f"🚚 Отправляю: {gift_title} ×{qty} → {recipient}")
                        log_info(ctx_user, f"CHOICE: подтверждено '+', начинаю отправку -> {gift_title} x{qty} to {recipient}")

                        sent_units = 0
                        failed_units = 0
                        failed_reasons: List[str] = []

                        for i in range(qty):
                            ok, info = send_gift_sync(recipient, gift_id=int(gift_id))
                            if ok:
                                sent_units += 1
                                time.sleep(0.25)
                                log_info(ctx_user, f"CHOICE: УСПЕХ -> {recipient} [unit {i+1}/{qty}] gift_id={gift_id}")
                                continue

                            kind = classify_send_error(str(info))
                            failed_units += 1
                            failed_reasons.append(kind)
                            log_warn(ctx_user, f"CHOICE: FAIL -> {recipient}: {kind} :: {short_text(info)}")

                            if kind == "username_not_found":
                                account.send_message(chat_id, "❌ Никнейм не найден в Telegram. Проверьте @username.")
                                if AUTO_REFUND:
                                    if sent_units == 0:
                                        refund_order(account, order_id, chat_id, ctx=ctx_user)
                                    else:
                                        remaining = qty - sent_units
                                        try_partial_refund(account, order_id, remaining, {"price": price, "title": gift_title}, chat_id, ctx=ctx_user)
                                break

                            if kind == "balance_low":
                                account.send_message(chat_id, "⚠️ У продавца недостаточно Stars для выдачи. Свяжитесь с продавцом.")
                                break
                            if kind == "flood":
                                account.send_message(chat_id, "⚠️ Слишком много запросов (flood). Попробуйте позже.")
                                break
                            if kind == "network":
                                account.send_message(chat_id, "⚠️ Проблема связи с Telegram. Попробуйте позже.")
                                _ensure_pyro_alive_sync()
                                break

                            account.send_message(chat_id, "⚠️ Ошибка отправки. Попробуйте позже или свяжитесь с продавцом.")
                            break

                        if sent_units > 0:
                            account.send_message(chat_id, f"🎉 Успешно отправлено: {sent_units} шт.")

                        if failed_units > 0:
                            account.send_message(chat_id, f"⚠️ Не удалось отправить: {failed_units} шт. Причины: {', '.join(set(failed_reasons))}")

                        if failed_units == 0 and sent_units == qty:
                            order_url = f"https://funpay.com/orders/{order_id}/"
                            account.send_message(chat_id, f"🙏 Подтвердите выполнение заказа и оставьте отзыв: {order_url}")
                            _completed_buyers.add(author_id)

                        waiting.pop(author_id, None)
                        _last_reply_by_buyer[author_id] = now
                        continue

                recipients = st.get("recipients") or []
                assign = expand_assignment(recipients, qty) if recipients else []

                if st["state"] == "awaiting_nicks":
                    recips = parse_recipients(text)
                    if not recips:
                        account.send_message(chat_id, "❌ Неверный формат. Пришлите один @username или список через запятую/пробел.")
                        _last_reply_by_buyer[author_id] = now
                        continue

                    st["recipients"] = recips
                    assign = expand_assignment(recips, qty)
                    plan = _format_plan(assign)

                    st["state"] = "awaiting_confirmation"
                    account.send_message(
                        chat_id,
                        f"📦 План выдачи: {st.get('gift_title', 'товар')} — {plan}.\n"
                        "✅ Если верно — напишите «+». Или пришлите новый список получателей."
                    )
                    log_info(ctx_user, f"NORMAL: получатели приняты. plan={plan}")
                    _last_reply_by_buyer[author_id] = now
                    continue

                if st["state"] == "awaiting_confirmation":
                    if text == "+":
                        recipients = st.get("recipients") or []
                        assign = expand_assignment(recipients, qty) if recipients else []

                        item_title = st.get("gift_title", "товар")
                        ids_per_unit = list(st.get("ids_per_unit") or [])
                        price = int(st.get("price", 0) or 0)

                        account.send_message(chat_id, f"🚚 Отправляю {item_title} — всего {qty} шт.")
                        log_info(ctx_user, f"NORMAL: подтверждено '+', отправляю {item_title} x{qty}")

                        sent_units = 0
                        failed_units = 0
                        failed_reasons: List[str] = []

                        for i in range(qty):
                            username = assign[i]
                            unit_ok = True

                            for gid in ids_per_unit:
                                ok, info = send_gift_sync(username, gift_id=gid)
                                if ok:
                                    time.sleep(0.25)
                                    log_info(ctx_user, f"NORMAL: УСПЕХ -> {username} [unit {i+1}/{qty}] part={gid}")
                                    continue

                                kind = classify_send_error(str(info))
                                failed_reasons.append(kind)
                                unit_ok = False
                                log_warn(ctx_user, f"NORMAL: FAIL -> {username}: {kind} :: {short_text(info)}")

                                if kind == "balance_low":
                                    account.send_message(chat_id, "⚠️ Недостаточно Stars у продавца. Выдачу остановил.")
                                    break
                                elif kind == "flood":
                                    account.send_message(chat_id, "⚠️ Слишком много запросов (flood). Попробуйте позже.")
                                    break
                                elif kind == "username_not_found":
                                    if AUTO_REFUND:
                                        try_partial_refund(account, order_id, 1, {"price": price, "title": item_title}, chat_id, ctx=ctx_user)
                                    break
                                elif kind == "network":
                                    account.send_message(chat_id, "⚠️ Проблема с соединением с Telegram. Попробуйте позже.")
                                    _ensure_pyro_alive_sync()
                                    break
                                else:
                                    break

                            if unit_ok:
                                sent_units += 1
                            else:
                                failed_units += 1
                                if failed_reasons and failed_reasons[-1] in ("balance_low", "flood", "network"):
                                    break

                        if sent_units > 0:
                            account.send_message(chat_id, f"🎉 Успешно отправлено: {sent_units} шт.")
                            _completed_buyers.add(author_id)

                        if failed_units > 0:
                            account.send_message(chat_id, f"⚠️ Не удалось отправить: {failed_units} шт. Причины: {', '.join(set(failed_reasons))}")

                        if failed_units == 0 and sent_units == qty:
                            order_url = f"https://funpay.com/orders/{order_id}/"
                            account.send_message(chat_id, f"🙏 Подтвердите выполнение заказа и оставьте отзыв: {order_url}")

                        waiting.pop(author_id, None)
                        _last_reply_by_buyer[author_id] = now
                        continue

                    recips = parse_recipients(text)
                    if recips:
                        st["recipients"] = recips
                        assign = expand_assignment(recips, qty)
                        plan = _format_plan(assign)
                        account.send_message(chat_id, f"✅ План обновлён: {plan}. Для подтверждения отправьте «+».")
                        log_info(ctx_user, f"NORMAL: план обновлён. plan={plan}")
                    else:
                        account.send_message(chat_id, "Для подтверждения отправьте «+». Либо пришлите новый список получателей.")
                    _last_reply_by_buyer[author_id] = now
                    continue

        except Exception as e:
            log_error("", f"Ошибка обработки события: {short_text(e)}")
            logger.debug("Подробности ошибки обработки события:", exc_info=True)


if __name__ == "__main__":
    main()
