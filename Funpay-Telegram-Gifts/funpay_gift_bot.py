import os
import re
import unicodedata
import json
import time
import asyncio
import logging
import base64
import threading
import colorlog
from contextlib import suppress
from typing import Optional, Tuple, List, Any, Dict
from collections import deque
import random
from pathlib import Path

from dotenv import load_dotenv
from pyrogram import Client

try:
    from pyrogram.errors import FloodWait, PeerFlood
except Exception:
    class FloodWait(Exception):
        value = 0
    class PeerFlood(Exception):
        pass

from FunPayAPI import Account
from FunPayAPI.updater.runner import Runner
from FunPayAPI.updater.events import NewOrderEvent, NewMessageEvent

try:
    from FunPayAPI.common.exceptions import UnauthorizedError, RequestError
except Exception:
    class UnauthorizedError(Exception):
        pass
    class RequestError(Exception):
        pass

try:
    import requests
except Exception:
    requests = None

try:
    from settings import resolve_to_gift_ids, get_required_stars, load_sets, get_message, reload_messages
    GIFT_SETS_AVAILABLE = True
except Exception:
    GIFT_SETS_AVAILABLE = False
    def get_message(key: str, **kwargs) -> str:
        return ""
    def reload_messages() -> None:
        return

if not hasattr(Client, "send_gift"):
    raise RuntimeError(
        "Установлен неподдерживаемый пакет 'pyrogram'. Нужен форк с поддержкой Stars.\n"
        "Используйте: pip uninstall -y pyrogram && pip install -U pyrofork tgcrypto"
    )

load_dotenv()

HERE = Path(__file__).resolve().parent
SESSIONS_DIR = HERE / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
GOLDEN_KEY = os.getenv("FUNPAY_AUTH_TOKEN")
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
API_ID = int(API_ID) if API_ID and API_ID.isdigit() else None
MANUAL_ORDERS_JSON = HERE / "manual_orders.json"
MANUAL_NOTICE_COOLDOWN = 30.0
_MANUAL_LOCK = threading.Lock()
_MANUAL_ORDERS: Dict[str, dict] = {}
_last_manual_notice_by_chat: Dict[int, float] = {}
_STOP_CMD_RE = re.compile(r"^\s*!stop(?:\s+(\S+))?(?:\s+(.*))?\s*$", re.IGNORECASE)

def _load_manual_orders() -> None:
    global _MANUAL_ORDERS
    try:
        if MANUAL_ORDERS_JSON.exists():
            raw = MANUAL_ORDERS_JSON.read_text(encoding="utf-8").strip()
            data = json.loads(raw) if raw else {}
            if not isinstance(data, dict):
                data = {}

            norm: Dict[str, dict] = {}
            for k, v in data.items():
                key = str(k).strip()
                if not key:
                    continue
                if not isinstance(v, dict):
                    v = {}

                oid = v.get("order_id", key)
                v["order_id"] = str(oid).strip() if oid is not None else key
                try:
                    v["chat_id"] = int(v.get("chat_id") or 0)
                except Exception:
                    pass

                norm[key] = v

            _MANUAL_ORDERS = norm
        else:
            _MANUAL_ORDERS = {}
    except Exception:
        _MANUAL_ORDERS = {}

def _save_manual_orders() -> None:
    try:
        with _MANUAL_LOCK:
            MANUAL_ORDERS_JSON.write_text(
                json.dumps(_MANUAL_ORDERS, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    except Exception:
        pass

def _oid(order_id: Any) -> str:
    return str(order_id).strip()

def _safe_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except Exception:
        return None

def _is_manual_order(order_id: Any) -> bool:
    key = _oid(order_id)
    with _MANUAL_LOCK:
        return key in _MANUAL_ORDERS

def _manual_order_for_chat(chat_id: int) -> Optional[str]:
    with _MANUAL_LOCK:
        for k, v in _MANUAL_ORDERS.items():
            try:
                if int(v.get("chat_id") or 0) == int(chat_id):
                    return str(v.get("order_id") or k)
            except Exception:
                continue
    return None

def _mark_order_manual(order_id: Any, chat_id: int, buyer_id: Optional[Any], actor_id: Optional[Any], note: str = "") -> None:
    key = _oid(order_id)
    ts = int(time.time())
    with _MANUAL_LOCK:
        prev = _MANUAL_ORDERS.get(key) if isinstance(_MANUAL_ORDERS.get(key), dict) else {}
        notified = bool(prev.get("notified", False))
        _MANUAL_ORDERS[key] = {
            "order_id": key,
            "chat_id": int(chat_id),
            "buyer_id": _safe_int(buyer_id),
            "by": _safe_int(actor_id),
            "ts": ts,
            "note": (note or "").strip(),
            "notified": notified,
        }
    _save_manual_orders()

def _set_manual_notified(order_id: Any) -> None:
    key = _oid(order_id)
    with _MANUAL_LOCK:
        v = _MANUAL_ORDERS.get(key)
        if isinstance(v, dict):
            v["notified"] = True
            _MANUAL_ORDERS[key] = v
    _save_manual_orders()

def _find_waiting_by_chat(chat_id: int) -> Optional[Tuple[int, dict]]:
    for bid, st in list(waiting.items()):
        try:
            if int(st.get("chat_id") or 0) == int(chat_id):
                return bid, st
        except Exception:
            continue
    return None

def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")

def _env_raw(name: str):
    return os.getenv(name)

MIN_SEND_DELAY = float(os.getenv("MIN_SEND_DELAY", "0.35"))
PER_RECIPIENT_DELAY = float(os.getenv("PER_RECIPIENT_DELAY", "1.20"))
BURST_WINDOW_SECONDS = float(os.getenv("BURST_WINDOW_SECONDS", "10"))
BURST_MAX_SENDS = int(os.getenv("BURST_MAX_SENDS", "20"))
SEND_JITTER = float(os.getenv("SEND_JITTER", "0.0"))
USERNAME_CACHE_TTL = float(os.getenv("USERNAME_CACHE_TTL", "86400"))
FLOODWAIT_EXTRA_SLEEP = float(os.getenv("FLOODWAIT_EXTRA_SLEEP", "0.30"))
SPAMBLOCK_PAUSE_SECONDS = float(os.getenv("SPAMBLOCK_PAUSE_SECONDS", "21600"))
AUTO_DEACTIVATE_ON_FLOODWAIT = _env_bool("AUTO_DEACTIVATE_ON_FLOODWAIT", False)
AUTO_RAISE_LOTS = _env_bool("AUTO_RAISE_LOTS", True)
AUTO_RAISE_INTERVAL_SECONDS = float(os.getenv("AUTO_RAISE_INTERVAL_SECONDS", "330"))
AUTO_RAISE_JITTER_SECONDS = float(os.getenv("AUTO_RAISE_JITTER_SECONDS", "30"))
_auto_raise_stop = threading.Event()
FLOOD_DEACTIVATE_COOLDOWN = float(os.getenv("FLOOD_DEACTIVATE_COOLDOWN", "900"))
TG_SESSIONS_RAW = (_env_raw("TG_SESSIONS") or _env_raw("TG_SESSION_NAMES") or "").strip()
TG_PRIMARY_SESSION = (_env_raw("TG_PRIMARY_SESSION") or "stars").strip() or "stars"
TG_AUTO_SWITCH = _env_bool("TG_AUTO_SWITCH", False)
TG_AUTO_SELECT_FOR_PRECHECK = _env_bool("TG_AUTO_SELECT_FOR_PRECHECK", True)
TG_BALANCE_CACHE_SECONDS = float(os.getenv("TG_BALANCE_CACHE_SECONDS", "10"))
TG_FAILOVER_NETWORK_PAUSE = float(os.getenv("TG_FAILOVER_NETWORK_PAUSE", "3"))
_ORIG_GETENV = os.getenv
_BRANDING_LOCKED = False

def sm(account: Account, chat_id: int, key: str, **kwargs):
    account.send_message(chat_id, get_message(key, **kwargs))

def _b64d(s: str) -> str:
    return base64.b64decode(s.encode("utf-8")).decode("utf-8")

_EXPECTED_BRANDING = {
    "CREATOR_NAME": _b64d("QHRpbmVjaGVsb3ZlYw=="),
    "CREATOR_URL": _b64d("aHR0cHM6Ly90Lm1lL3RpbmVjaGVsb3ZlYw=="),
    "CHANNEL_URL": _b64d("aHR0cHM6Ly90Lm1lL2J5X3RoYw=="),
    "GITHUB_URL": _b64d("aHR0cHM6Ly9naXRodWIuY29tL3RpbmVjaGVsb3ZlYy9GdW5wYXktVGVsZWdyYW0tR2lmdHM="),
    "HELP_URL": _b64d("aHR0cHM6Ly90ZWxldHlwZS5pbi9AdGluZWNoZWxvdmVjL0Z1bnBheS1UZWxlZ3JhbS1HaWZ0cw=="),
}

def lock_branding():
    global _BRANDING_LOCKED
    if not _BRANDING_LOCKED:
        def _locked_getenv(key, default=None):
            if key in _EXPECTED_BRANDING:
                return _EXPECTED_BRANDING[key]
            return _ORIG_GETENV(key, default)
        os.getenv = _locked_getenv
        _BRANDING_LOCKED = True
    g = globals()
    for k, v in _EXPECTED_BRANDING.items():
        g[k] = v

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
AUTO_RAISE_CATEGORY_IDS_RAW = _env_raw("AUTO_RAISE_CATEGORY_IDS")
AUTO_RAISE_SUBCATS_LIST, AUTO_RAISE_BAD_TOKENS, _AUTO_RAISE_RAW = _parse_id_list(AUTO_RAISE_CATEGORY_IDS_RAW,default=RAW_IDS_STR)

COOLDOWN_SECONDS = float(os.getenv("REPLY_COOLDOWN_SECONDS", "1.0"))
AUTO_REFUND_RAW = _env_raw("AUTO_REFUND")
AUTO_DEACTIVATE_RAW = _env_raw("AUTO_DEACTIVATE")
AUTO_REFUND = _env_bool("AUTO_REFUND", True)
AUTO_DEACTIVATE = _env_bool("AUTO_DEACTIVATE", True)

ANONYMOUS_GIFTS_RAW = _env_raw("ANONYMOUS_GIFTS")
ANONYMOUS_GIFTS = _env_bool("ANONYMOUS_GIFTS", False)
ANONYMOUS_MODE_RAW = _env_raw("ANONYMOUS_MODE") or _env_raw("ANON_MODE")
ANONYMOUS_MODE = (ANONYMOUS_MODE_RAW or "seller").strip().lower()

if ANONYMOUS_MODE in ("yes", "true", "1", "on", "anon", "anonymous"):
    ANON_POLICY = "forced"
    ANON_FORCED_VALUE = True
elif ANONYMOUS_MODE in ("no", "false", "0", "off", "public"):
    ANON_POLICY = "forced"
    ANON_FORCED_VALUE = False
elif ANONYMOUS_MODE in ("buyer", "ask", "customer", "client", "by_buyer"):
    ANON_POLICY = "buyer"
    ANON_FORCED_VALUE = None
else:
    ANON_POLICY = "seller"
    ANON_FORCED_VALUE = None

PRECHECK_BALANCE_RAW = _env_raw("PRECHECK_BALANCE")
PRECHECK_BALANCE = _env_bool("PRECHECK_BALANCE", True)

REQUIRE_PLUS_CONFIRMATION_RAW = _env_raw("REQUIRE_PLUS_CONFIRMATION")
REQUIRE_PLUS_CONFIRMATION = _env_bool("REQUIRE_PLUS_CONFIRMATION", True)

GIFT_PARAM_KEY_RAW = _env_raw("GIFT_PARAM_KEY") or _env_raw("GIFT_PARAM") or _env_raw("GIFT_PARAM_NAME")
GIFT_PARAM_KEY = (GIFT_PARAM_KEY_RAW or "gift_tg").strip()
if not GIFT_PARAM_KEY:
    GIFT_PARAM_KEY = "gift_tg"


def _norm_param_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


GIFT_PARAM_KEY_NORM = _norm_param_key(GIFT_PARAM_KEY)

CREATOR_NAME = os.getenv("CREATOR_NAME", "@tinechelovec") #раньше здесь был dadadadwada, бедный dadadadwada земля тебе плейрок
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
_app_started = threading.Event()
_pyro_gate = threading.Semaphore(1)
_restarts = 0
_completed_buyers: set[int] = set()
waiting: dict[int, dict] = {}
_last_reply_by_buyer: dict[int, float] = {}
ACCOUNT_GLOBAL: Optional[Account] = None

class TgSendLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._last_any = 0.0
        self._last_by_rec: Dict[str, float] = {}
        self._burst = deque()
        self._pause_until = 0.0

    def pause(self, seconds: float):
        if seconds <= 0:
            return
        now = time.monotonic()
        with self._lock:
            self._pause_until = max(self._pause_until, now + seconds)

    def _calc_wait_locked(self, now: float, rec_key: str) -> float:
        wait = 0.0
        if now < self._pause_until:
            wait = max(wait, self._pause_until - now)
        if MIN_SEND_DELAY > 0:
            wait = max(wait, MIN_SEND_DELAY - (now - self._last_any))
        if PER_RECIPIENT_DELAY > 0:
            last_r = self._last_by_rec.get(rec_key, 0.0)
            wait = max(wait, PER_RECIPIENT_DELAY - (now - last_r))
        if BURST_WINDOW_SECONDS > 0 and BURST_MAX_SENDS > 0:
            while self._burst and (now - self._burst[0]) >= BURST_WINDOW_SECONDS:
                self._burst.popleft()
            if len(self._burst) >= BURST_MAX_SENDS:
                wait = max(wait, BURST_WINDOW_SECONDS - (now - self._burst[0]))
        if SEND_JITTER > 0 and wait > 0:
            wait += random.uniform(0, SEND_JITTER)
        return wait

    def _reserve_locked(self, now: float, rec_key: str) -> None:
        self._last_any = now
        self._last_by_rec[rec_key] = now
        if BURST_WINDOW_SECONDS > 0 and BURST_MAX_SENDS > 0:
            self._burst.append(now)

    async def wait_async(self, rec_key: str) -> None:
        rec_key = rec_key.lower().strip()
        while True:
            now = time.monotonic()
            with self._lock:
                wait = self._calc_wait_locked(now, rec_key)
                if wait <= 0:
                    self._reserve_locked(now, rec_key)
                    return
            await asyncio.sleep(min(wait, 5.0))

_username_cache_lock = threading.Lock()
_username_id_cache: Dict[str, Tuple[int, float]] = {}
_last_flood_deactivate_ts = 0.0

def _session_sort_key(name: str) -> Tuple[int, int, str]:
    n = (name or "").strip().lower()
    if n == "stars":
        return (0, 0, n)
    m = re.fullmatch(r"stars(\d+)", n)
    if m:
        return (0, int(m.group(1)), n)
    return (1, 10**9, n)


def _parse_sessions_list(raw: str) -> List[str]:
    s = (raw or "").strip()
    if not s:
        return []
    parts = [p.strip() for p in re.split(r"[,;\s]+", s) if p.strip()]
    uniq: List[str] = []
    seen = set()
    for p in parts:
        if p not in seen:
            uniq.append(p)
            seen.add(p)
    return uniq

def _discover_sessions(workdir: Optional[Path] = None) -> List[str]:
    wd = Path(workdir) if workdir is not None else SESSIONS_DIR
    try:
        wd.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    out: List[str] = []
    try:
        for p in wd.iterdir():
            if p.is_file() and p.name.endswith(".session"):
                out.append(p.stem)
    except Exception:
        pass
    out = sorted(out, key=_session_sort_key)
    return out


def _load_session_names() -> List[str]:
    names = _parse_sessions_list(TG_SESSIONS_RAW)
    if not names:
        names = _discover_sessions(SESSIONS_DIR)
    if not names:
        names = ["stars"]

    if TG_PRIMARY_SESSION in names:
        names = [TG_PRIMARY_SESSION] + [x for x in names if x != TG_PRIMARY_SESSION]
    else:
        names = [TG_PRIMARY_SESSION] + [x for x in names if x != TG_PRIMARY_SESSION]

    names = sorted(dict.fromkeys(names), key=_session_sort_key)
    if TG_PRIMARY_SESSION in names:
        names = [TG_PRIMARY_SESSION] + [x for x in names if x != TG_PRIMARY_SESSION]
    return names


class TgAccountManager:
    def __init__(self, session_names: List[str]):
        self._lock = threading.Lock()
        self.session_names = list(session_names)
        self.clients: List[Client] = []
        self.limiters: List[TgSendLimiter] = []
        self.usable_until: List[float] = []
        self.alive: List[bool] = []
        self.balance_cache: List[Tuple[Optional[int], float]] = []
        self.active_idx = 0

    def set_active(self, idx: int) -> None:
        with self._lock:
            if 0 <= idx < len(self.clients):
                self.active_idx = idx

    def get_active(self) -> int:
        with self._lock:
            return int(self.active_idx)

    def mark_unusable(self, idx: int, seconds: float) -> None:
        if seconds <= 0:
            return
        now = time.time()
        if 0 <= idx < len(self.usable_until):
            self.usable_until[idx] = max(self.usable_until[idx], now + float(seconds))

    def is_usable(self, idx: int) -> bool:
        if not (0 <= idx < len(self.clients)):
            return False
        if not self.alive[idx]:
            return False
        return time.time() >= self.usable_until[idx]

    def order_try_list(self) -> List[int]:
        n = len(self.clients)
        if n <= 0:
            return []
        a = self.get_active()
        order = [a] + [i for i in range(n) if i != a]
        return order

    async def _precheck_authorized(self, c: Client, name: str):
        try:
            await c.connect()
            me = await c.get_me()
            return me
        except Exception as e:
            log_warn("tg", f"Сессия НЕ авторизована/битая: {name} :: {short_text(e)}")
            return None
        finally:
            with suppress(Exception):
                await c.disconnect()

    async def start_all(self) -> None:
        common = dict(workdir=str(SESSIONS_DIR), no_updates=True)
        if not API_ID or not API_HASH:
            raise RuntimeError("Не заданы API_ID/API_HASH")

        self.clients = []
        self.limiters = []
        self.usable_until = []
        self.alive = []
        self.balance_cache = []

        for name in self.session_names:
            c = Client(name, api_id=API_ID, api_hash=API_HASH, **common)
            self.clients.append(c)
            self.limiters.append(TgSendLimiter())
            self.usable_until.append(0.0)
            self.alive.append(False)
            self.balance_cache.append((None, 0.0))

        for i, c in enumerate(self.clients):
            name = self.session_names[i]

            me = await self._precheck_authorized(c, name)
            if me is None:
                self.alive[i] = False
                continue

            try:
                await c.start()
                self.alive[i] = True

                uname = None
                try:
                    uname = f"@{me.username}" if getattr(me, "username", None) else (f"{getattr(me, 'first_name', '')}".strip() or "id=" + str(getattr(me, "id", "?")))
                except Exception:
                    uname = None

                stars = None
                try:
                    stars = await self.get_balance(i)
                except Exception:
                    stars = None

                if uname:
                    log_info("tg", f"Сессия активна: {name} -> {uname} | Stars: {stars if stars is not None else '—'}")
                else:
                    log_info("tg", f"Сессия активна: {name} | Stars: {stars if stars is not None else '—'}")

            except Exception as e:
                self.alive[i] = False
                log_error("tg", f"Не удалось запустить сессию {name}: {short_text(e)}")

        if not any(self.alive):
            raise RuntimeError("Ни одна Telegram-сессия не запустилась")

        try:
            if TG_PRIMARY_SESSION in self.session_names:
                pi = self.session_names.index(TG_PRIMARY_SESSION)
                if 0 <= pi < len(self.alive) and self.alive[pi]:
                    self.set_active(pi)
                    return
            for j in range(len(self.alive)):
                if self.alive[j]:
                    self.set_active(j)
                    return
        except Exception:
            pass

    async def stop_all(self) -> None:
        for i, c in enumerate(self.clients):
            if not self.alive[i]:
                continue
            with suppress(Exception):
                await c.stop()
            self.alive[i] = False

    async def ping(self, idx: int) -> bool:
        if not (0 <= idx < len(self.clients)):
            return False
        if not self.alive[idx]:
            return False
        try:
            await self.clients[idx].get_me()
            return True
        except Exception:
            return False

    async def restart(self, idx: int) -> bool:
        if not (0 <= idx < len(self.clients)):
            return False
        c = self.clients[idx]
        with suppress(Exception):
            await c.stop()
        await asyncio.sleep(0.2)

        me = await self._precheck_authorized(c, self.session_names[idx])
        if me is None:
            self.alive[idx] = False
            return False

        try:
            await c.start()
            self.alive[idx] = True
            return True
        except Exception:
            self.alive[idx] = False
            return False

    async def get_balance(self, idx: int) -> Optional[int]:
        if not (0 <= idx < len(self.clients)):
            return None
        if not self.alive[idx]:
            return None
        cached, exp = self.balance_cache[idx]
        now = time.time()
        if cached is not None and now < exp:
            return cached
        try:
            bal = await self.clients[idx].get_stars_balance()
            out: Optional[int] = None
            if isinstance(bal, (int, float)):
                out = int(bal)
            elif isinstance(bal, dict):
                for k in ("balance", "stars", "stars_balance", "count"):
                    if k in bal:
                        try:
                            out = int(bal[k])
                            break
                        except Exception:
                            pass
            else:
                try:
                    out = int(bal)
                except Exception:
                    out = None
            self.balance_cache[idx] = (out, now + TG_BALANCE_CACHE_SECONDS)
            return out
        except Exception:
            self.balance_cache[idx] = (None, now + min(2.0, TG_BALANCE_CACHE_SECONDS))
            return None

TG_MANAGER: Optional[TgAccountManager] = None

def _exc_wait_seconds(e: Exception) -> int:
    v = getattr(e, "value", None)
    if isinstance(v, (int, float)):
        return int(v)
    v = getattr(e, "x", None)
    if isinstance(v, (int, float)):
        return int(v)
    s = str(e).lower()
    m = re.search(r"flood_wait_(\d+)", s)
    if m:
        return int(m.group(1))
    m = re.search(r"wait of (\d+) seconds", s)
    if m:
        return int(m.group(1))
    return 0

def _handle_floodwait(idx: int, seconds: int, username: str, gift_id: int, exc: Optional[Exception] = None):
    sec = int(seconds or 0)
    if sec <= 0 and exc is not None:
        sec = _exc_wait_seconds(exc)
    if sec <= 0:
        sec = 1
    if TG_MANAGER is not None and 0 <= idx < len(TG_MANAGER.limiters):
        TG_MANAGER.limiters[idx].pause(sec + FLOODWAIT_EXTRA_SLEEP)
        TG_MANAGER.mark_unusable(idx, sec + FLOODWAIT_EXTRA_SLEEP)
    log_warn("tg", f"FLOOD_WAIT: {sec}s (+{FLOODWAIT_EXTRA_SLEEP:.2f}) session={TG_MANAGER.session_names[idx] if TG_MANAGER else idx} to=@{username.lstrip('@')} gift_id={gift_id}")
    global _last_flood_deactivate_ts
    if AUTO_DEACTIVATE_ON_FLOODWAIT and ACCOUNT_GLOBAL is not None:
        now = time.time()
        if now - _last_flood_deactivate_ts >= FLOOD_DEACTIVATE_COOLDOWN:
            _last_flood_deactivate_ts = now
            try:
                log_warn("tg", "AUTO_DEACTIVATE_ON_FLOODWAIT=ON -> деактивирую лоты во всех CATEGORY_IDS...")
                for cid in CATEGORY_IDS_LIST:
                    deactivate_lots(ACCOUNT_GLOBAL, cid)
            except Exception as e:
                log_error("tg", f"Не смог деактивировать лоты после FloodWait: {short_text(e)}")

def _handle_spamblock(idx: int, username: str, gift_id: int, exc: Optional[Exception] = None):
    if TG_MANAGER is not None:
        TG_MANAGER.mark_unusable(idx, float(SPAMBLOCK_PAUSE_SECONDS))
        if 0 <= idx < len(TG_MANAGER.limiters):
            TG_MANAGER.limiters[idx].pause(float(SPAMBLOCK_PAUSE_SECONDS))
    log_error("tg", f"PEER_FLOOD/SPAM_BLOCK session={TG_MANAGER.session_names[idx] if TG_MANAGER else idx} to=@{username.lstrip('@')} gift_id={gift_id} :: {short_text(exc)}")

def _handle_network(idx: int, username: str, gift_id: int, exc: Optional[Exception] = None):
    if TG_MANAGER is not None:
        TG_MANAGER.mark_unusable(idx, float(TG_FAILOVER_NETWORK_PAUSE))
        if 0 <= idx < len(TG_MANAGER.limiters):
            TG_MANAGER.limiters[idx].pause(float(TG_FAILOVER_NETWORK_PAUSE))
    log_warn("tg", f"NETWORK session={TG_MANAGER.session_names[idx] if TG_MANAGER else idx} to=@{username.lstrip('@')} gift_id={gift_id} :: {short_text(exc)}")

from contextlib import suppress

def _build_subcat_to_cat_map(account: Account) -> Dict[int, int]:
    mapping: Dict[int, int] = {}
    try:
        if not getattr(account, "is_initiated", False):
            with suppress(Exception):
                account.get()

        cats = getattr(account, "categories", None)
        if not isinstance(cats, list) or not cats:
            with suppress(Exception):
                d = account.get_sorted_categories()
                if isinstance(d, dict):
                    cats = list(d.values())

        for cat in (cats or []):
            cid = getattr(cat, "id", None)
            if cid is None:
                continue

            subs = (
                getattr(cat, "subcategories", None)
                or getattr(cat, "sub_categories", None)
                or []
            )
            for sub in (subs or []):
                sid = getattr(sub, "id", None)
                if sid is None:
                    continue
                mapping[int(sid)] = int(cid)

        if not mapping:
            subs_all = getattr(account, "subcategories", None)
            if isinstance(subs_all, list):
                for sub in subs_all:
                    sid = getattr(sub, "id", None)
                    cat = getattr(sub, "category", None) or getattr(sub, "game", None)
                    cid = getattr(cat, "id", None) if cat else None
                    if sid is not None and cid is not None:
                        mapping[int(sid)] = int(cid)

    except Exception as e:
        log_warn("raise", f"Не смог построить mapping subcat->cat: {short_text(e)}")

    return mapping

def _group_subcats_by_category(subcat_ids: List[int], mapping: Dict[int, int]) -> Dict[int, List[int]]:
    out: Dict[int, List[int]] = {}
    for sid in subcat_ids or []:
        cid = mapping.get(int(sid))
        if cid is None:
            continue
        out.setdefault(int(cid), []).append(int(sid))
    return out

AUTO_RAISE_LOG_SUBCATS_LIMIT = int(os.getenv("AUTO_RAISE_LOG_SUBCATS_LIMIT", "25"))

def _fmt_ids(ids: List[int], limit: int = AUTO_RAISE_LOG_SUBCATS_LIMIT) -> str:
    ids = [int(x) for x in (ids or [])]
    if not ids:
        return "[]"
    if len(ids) <= limit:
        return "[" + ",".join(map(str, ids)) + "]"
    head = ids[:limit]
    return "[" + ",".join(map(str, head)) + f",…(+{len(ids)-limit})]"

def _auto_raise_loop(funpay_token: str):
    if not AUTO_RAISE_LOTS:
        return

    try:
        acc = Account(funpay_token)
        acc.get()
    except Exception as e:
        log_error("raise", f"Автоподнятие: не удалось авторизоваться: {short_text(e)}")
        return

    last_by_cat: Dict[int, float] = {}

    while not _auto_raise_stop.is_set():
        subcats = [int(x) for x in (AUTO_RAISE_SUBCATS_LIST or [])]

        if not subcats:
            _auto_raise_stop.wait(30.0)
            continue

        mapping = _build_subcat_to_cat_map(acc)
        groups = _group_subcats_by_category(subcats, mapping)

        now = time.time()
        per_cat_cooldown = max(300.0, float(AUTO_RAISE_INTERVAL_SECONDS))

        if not groups:
            cat_ids_to_raise = [int(x) for x in subcats]
        else:
            cat_ids_to_raise = sorted(set(int(cid) for cid in groups.keys()))

        for cat_id in cat_ids_to_raise:
            last = last_by_cat.get(cat_id, 0.0)
            if now - last < per_cat_cooldown:
                continue
            try:
                acc.raise_lots(int(cat_id))
                last_by_cat[int(cat_id)] = time.time()
                log_info("raise", f"Подняли категорию {cat_id}")
            except Exception as e:
                log_warn("raise", f"Не смог поднять категорию {cat_id}: {short_text(e)}")

        sleep_for = float(AUTO_RAISE_INTERVAL_SECONDS)
        if AUTO_RAISE_JITTER_SECONDS > 0:
            sleep_for += random.uniform(0, float(AUTO_RAISE_JITTER_SECONDS))

        log_info("raise", f"Следующее поднятие через {int(sleep_for)}с")
        _auto_raise_stop.wait(sleep_for)

async def _resolve_user_id_cached(idx: int, username: str) -> int:
    uname = username.lstrip("@")
    key = uname.lower()
    now = time.monotonic()
    with _username_cache_lock:
        hit = _username_id_cache.get(key)
        if hit and hit[1] > now:
            return hit[0]
    if TG_MANAGER is None:
        raise RuntimeError("tg manager not started")
    if not (0 <= idx < len(TG_MANAGER.clients)):
        raise RuntimeError("bad tg idx")
    await TG_MANAGER.limiters[idx].wait_async(f"resolve:{key}")
    u = await TG_MANAGER.clients[idx].get_users(uname)
    if isinstance(u, list):
        u = u[0]
    uid = int(getattr(u, "id", 0) or 0)
    if uid <= 0:
        raise RuntimeError("resolve returned empty user_id")
    with _username_cache_lock:
        _username_id_cache[key] = (uid, now + USERNAME_CACHE_TTL)
    return uid

async def _runner_start():
    global TG_MANAGER
    names = _load_session_names()
    TG_MANAGER = TgAccountManager(names)
    await TG_MANAGER.start_all()
    mode = "auto" if TG_AUTO_SWITCH else "manual"
    active = TG_MANAGER.get_active() if TG_MANAGER else 0
    act_name = TG_MANAGER.session_names[active] if TG_MANAGER and TG_MANAGER.session_names else "?"
    log_info("tg", f"Готово. Сессии={','.join(names)} mode={mode} primary={TG_PRIMARY_SESSION} active={act_name}")
    _app_started.set()

def _thread_target():
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_runner_start())
        loop.run_forever()
    except Exception as e:
        log_error("tg", f"Ошибка запуска Pyrogram-потока: {short_text(e)}")
        _app_started.set()

async def _ensure_any_alive() -> bool:
    if TG_MANAGER is None:
        return False
    order = TG_MANAGER.order_try_list()
    if not order:
        return False
    a = TG_MANAGER.get_active()
    if await TG_MANAGER.ping(a):
        return True
    if await TG_MANAGER.restart(a) and await TG_MANAGER.ping(a):
        return True
    if not TG_AUTO_SWITCH:
        return False
    for idx in order:
        if idx == a:
            continue
        if await TG_MANAGER.ping(idx):
            TG_MANAGER.set_active(idx)
            return True
        if await TG_MANAGER.restart(idx) and await TG_MANAGER.ping(idx):
            TG_MANAGER.set_active(idx)
            return True
    return False

def _ensure_pyro_alive_sync() -> bool:
    if TG_MANAGER is None:
        return False
    fut = asyncio.run_coroutine_threadsafe(_ensure_any_alive(), loop)
    try:
        return bool(fut.result(timeout=25.0))
    except Exception:
        return False

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

def mask_secret(s: Optional[str], head: int = 4, tail: int = 4) -> str:
    s = "" if s is None else str(s).strip()
    if not s:
        return "<empty>"
    if len(s) <= head + tail:
        return "*" * len(s)
    return f"{s[:head]}…{s[-tail:]} (len={len(s)})"

def _strip_invisible(s: str) -> str:
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKC", s)
    return "".join(ch for ch in s if unicodedata.category(ch) not in ("Cf", "Cc"))

def is_plus_confirm(s: str) -> bool:
    s = _strip_invisible(s)
    s = "".join(ch for ch in s if not ch.isspace())
    return s == "+"

def parse_anon_choice(text: str) -> Optional[bool]:
    if not text:
        return None
    t = _strip_invisible(text).strip().lower()
    t = re.sub(r"\s+", " ", t)

    if t in ("1", "да", "yes", "y", "anon", "анон", "анонимно", "аноним"):
        return True
    if t in ("2", "нет", "no", "n", "public", "не анонимно", "неанон"):
        return False
    if "анон" in t and "не" not in t:
        return True
    if "не" in t and "анон" in t:
        return False
    return None

def _build_param_regex(key: str) -> str:
    key = (key or "").strip()
    if not key:
        key = "gift_tg"
    parts = re.findall(r"[A-Za-z0-9]+", key)
    if not parts:
        parts = ["gift", "tg"]
    sep = r"[\s_\-]*"
    return sep.join(re.escape(p) for p in parts)

def parse_gift_num(text: str, param_key: Optional[str] = None) -> Optional[str]:
    if not text:
        return None
    t = _strip_invisible(text)
    key = param_key or GIFT_PARAM_KEY
    key_pat = _build_param_regex(key)
    m = re.search(rf"{key_pat}\s*(?:[:=]|-)?\s*([0-9]{{1,6}}(?:\s*,\s*[0-9]{{1,6}})*)", t, flags=re.IGNORECASE)
    if not m:
        return None
    nums = re.findall(r"\d+", m.group(1))
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

def _norm_brand(s: str) -> str:
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKC", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) not in ("Cf", "Cc"))
    s = re.sub(r"\s+", "", s).strip().lower()
    return s

def check_branding_or_warn() -> None:
    mismatched = []
    g = globals()

    for k, expected in _EXPECTED_BRANDING.items():
        current = g.get(k, "")
        if _norm_brand(current) != _norm_brand(expected):
            mismatched.append(k)

    if not mismatched:
        return

    msg = (
        "❌ Произошла ошибка в проекте "
        "Напишите пожалуйста создетелю.\n\n"
        f"Telegram: {_EXPECTED_BRANDING['CREATOR_NAME']}\n"
    )

    try:
        logger.critical(msg)
    except Exception:
        print(msg)

    raise SystemExit(23)

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

async def _get_stars_balance_once(idx: int) -> Optional[int]:
    if TG_MANAGER is None:
        return None
    try:
        return await TG_MANAGER.get_balance(idx)
    except Exception:
        return None

def get_stars_balance_sync(timeout: float = 10.0) -> Optional[int]:
    if TG_MANAGER is None:
        return None
    if not _ensure_pyro_alive_sync():
        return None
    idx = TG_MANAGER.get_active()
    with _pyro_gate:
        fut = asyncio.run_coroutine_threadsafe(_get_stars_balance_once(idx), loop)
        try:
            res = fut.result(timeout=timeout)
            return res if isinstance(res, int) and res >= 0 else None
        except Exception:
            return None

def pick_account_for_need_sync(need: int, timeout: float = 12.0) -> Tuple[Optional[int], Optional[int]]:
    if TG_MANAGER is None:
        return None, None
    if not _ensure_pyro_alive_sync():
        return None, None
    n = len(TG_MANAGER.clients)
    if n <= 0:
        return None, None
    if not TG_AUTO_SELECT_FOR_PRECHECK or not TG_AUTO_SWITCH:
        idx = TG_MANAGER.get_active()
        with _pyro_gate:
            fut = asyncio.run_coroutine_threadsafe(_get_stars_balance_once(idx), loop)
            try:
                bal = fut.result(timeout=timeout)
            except Exception:
                bal = None
        return idx, bal if isinstance(bal, int) else None
    best_idx = None
    best_bal = None
    order = TG_MANAGER.order_try_list()
    for idx in order:
        if not TG_MANAGER.is_usable(idx):
            continue
        with _pyro_gate:
            fut = asyncio.run_coroutine_threadsafe(_get_stars_balance_once(idx), loop)
            try:
                bal = fut.result(timeout=timeout)
            except Exception:
                bal = None
        if not isinstance(bal, int):
            continue
        if best_bal is None or bal > best_bal:
            best_bal = bal
            best_idx = idx
        if need > 0 and bal >= need:
            best_bal = bal
            best_idx = idx
            break
    return best_idx, best_bal

def try_partial_refund(account: Account, order_id: int, units: int, gift: dict, chat_id: int, ctx: str = "") -> bool:
    total_stars = int(units) * int(gift.get("price", 0))
    if units <= 0 or total_stars <= 0:
        return True
    try:
        account.refund(order_id, amount=total_stars)
        log_info(ctx, f"Partial refund by amount done: {total_stars}⭐ for {units} pcs")
        try:
            sm(account, chat_id, "partial_refund_amount", units=units, total_stars=total_stars)
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
            sm(account, chat_id, "partial_refund_units", units=units)
        except Exception:
            pass
        return True
    except Exception as e:
        log_warn(ctx, f"Partial refund(units) failed: {short_text(e)}")
    try:
        sm(account, chat_id, "partial_refund_unavailable")
    except Exception:
        pass
    log_error(ctx, "Partial refund not supported by API")
    return False

async def _send_gift_once(idx: int, username: str, gift_id: int, hide_my_name: bool) -> bool:
    if TG_MANAGER is None:
        raise RuntimeError("tg manager not started")
    uname = username.lstrip("@")
    peer: Any = uname
    try:
        peer = await _resolve_user_id_cached(idx, uname)
    except Exception as e:
        log_warn("resolve", f"resolve @{uname} failed, fallback to username: {short_text(e)}")
        peer = uname
    attempts = [{"gift_id": gift_id}, {"star_gift_id": gift_id}]
    last_err: Optional[Exception] = None
    for i, extra in enumerate(attempts, 1):
        try:
            await TG_MANAGER.limiters[idx].wait_async(f"send:{uname.lower()}")
            log_info("send_gift", f"Попытка {i}: session={TG_MANAGER.session_names[idx]} peer={'id' if isinstance(peer, int) else 'uname'} anon={ANONYMOUS_GIFTS}")
            res = await TG_MANAGER.clients[idx].send_gift(
                chat_id=peer,
                hide_my_name=bool(hide_my_name),
                **extra
            )
            log_info("send_gift", f"Попытка {i}: session={TG_MANAGER.session_names[idx]} peer={'id' if isinstance(peer, int) else 'uname'} anon={bool(hide_my_name)}")
            return bool(res) if isinstance(res, bool) else True
        except TypeError as e:
            s = str(e)
            last_err = e
            if "unexpected keyword argument" in s or ("NoneType" in s and "len()" in s):
                log_warn("send_gift", f"Сигнатура/баг: {e} — пробую другой вариант")
                continue
            log_warn("send_gift", f"TypeError: {e} — пробую следующий вариант")
            continue
        except Exception as e:
            se = str(e).lower()
            if isinstance(peer, int) and "peer_id_invalid" in se:
                log_warn("send_gift", f"peer_id_invalid on id, fallback to username @{uname}")
                peer = uname
                last_err = e
                continue
            last_err = e
            raise
    if last_err:
        raise last_err
    raise RuntimeError("Не удалось подобрать сигнатуру send_gift для этой сборки.")

def classify_send_error(info: str) -> str:
    if not info:
        return "other"
    lower = info.lower()
    if "flood_wait:" in lower or "flood_wait_" in lower:
        return "flood"
    if "peer_flood" in lower:
        return "spam_block"
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
    if "connection lost" in lower or "socket.send()" in lower or "nonetype' object has no attribute 'read'" in lower or "read() called while another coroutine" in lower:
        return "network"
    return "other"

def _send_gift_with_idx_sync(idx: int, username: str, gift_id: int, hide_my_name: bool, timeout: float) -> Tuple[bool, str]:
    if TG_MANAGER is None:
        return False, "TG manager not started"
    if not _ensure_pyro_alive_sync():
        return False, "Pyrogram not connected"
    with _pyro_gate:
        fut = asyncio.run_coroutine_threadsafe(_send_gift_once(idx, username, gift_id, hide_my_name), loop)
        try:
            res = fut.result(timeout=timeout)
            return True, str(res)
        except FloodWait as e:
            sec = _exc_wait_seconds(e)
            _handle_floodwait(idx, sec, username, gift_id, exc=e)
            return False, f"FLOOD_WAIT:{sec}"
        except PeerFlood as e:
            _handle_spamblock(idx, username, gift_id, exc=e)
            return False, "PEER_FLOOD"
        except Exception as e:
            sec = _exc_wait_seconds(e)
            low = str(e).lower()
            if sec > 0 and ("flood" in low or "wait" in low):
                _handle_floodwait(idx, sec, username, gift_id, exc=e)
                return False, f"FLOOD_WAIT:{sec}"
            if classify_send_error(str(e)) == "network":
                _handle_network(idx, username, gift_id, exc=e)
            return False, str(e)

def send_gift_sync(username: str, gift_id: int, hide_my_name: bool, timeout: float = 30.0) -> Tuple[bool, str]:
    if TG_MANAGER is None:
        return False, "TG manager not started"
    if not _ensure_pyro_alive_sync():
        return False, "Pyrogram not connected"
    order = TG_MANAGER.order_try_list()
    last_info = "no_attempt"
    for idx in order:
        if not TG_MANAGER.is_usable(idx):
            continue
        if not TG_AUTO_SWITCH and idx != TG_MANAGER.get_active():
            continue
        ok, info = _send_gift_with_idx_sync(idx, username, gift_id, hide_my_name, timeout)
        if ok:
            TG_MANAGER.set_active(idx)
            return True, info
        last_info = info
        kind = classify_send_error(str(info))
        if not TG_AUTO_SWITCH:
            return False, info
        if kind == "balance_low":
            continue
        if kind in ("flood", "spam_block", "network"):
            continue
        return False, info
    return False, last_info

def refund_order(account: Account, order_id: int, chat_id: int, ctx: str = "") -> bool:
    try:
        account.refund(order_id)
        log_info(ctx, f"Refund done for order {order_id}")
        try:
            sm(account, chat_id, "refund_done")
        except Exception:
            pass
        return True
    except Exception as e:
        log_error(ctx, f"Refund failed for order {order_id}: {short_text(e)}")
        logger.debug("Refund details:", exc_info=True)
        try:
            sm(account, chat_id, "refund_failed")
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
                log_info("", f"Лот {getattr(lot, 'id', '?')} уже active={active}")
                return True
            lot_fields.active = active
            account.save_lot(lot_fields)
            log_warn("", f"Лот {getattr(lot, 'id', '?')} изменён: active={active}")
            return True
        except Exception as e:
            log_error("", f"Ошибка при изменении лота {getattr(lot, 'id', '?')}: {short_text(e)}")
            logger.debug("Подробности update_lot_state:", exc_info=True)
            attempts -= 1
            time.sleep(min(0.5 * (3 - attempts), 1.5))
    log_error("", f"Не удалось изменить лот {getattr(lot, 'id', '?')} (исчерпаны попытки)")
    return False

def deactivate_lots(account: Account, subcat_id: int):
    log_warn("", f"Запускаю деактивацию ВСЕХ лотов в подкатегории {subcat_id}...")
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
            log_error("", f"Ошибка при деактивации лота {getattr(lot, 'id', '?')}: {short_text(e)}")
            logger.debug("Подробности деактивации лота:", exc_info=True)
            consecutive_errors += 1
        if consecutive_errors:
            pause = min(0.2 * consecutive_errors, 1.5)
            time.sleep(pause)
    if affected:
        log_warn("", "Деактивированы лоты:\n- " + "\n- ".join(affected))
    else:
        log_info("", "Не было активных лотов для деактивации.")

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

def _parse_stars_from_text_hint(text: str) -> Optional[int]:
    if not text:
        return None
    t = _strip_invisible(text)
    m = re.search(r"(\d{1,6})\s*⭐", t)
    if not m:
        m = re.search(r"(\d{1,6})\s*(?:stars?|ст\.?|зв[её]зд)\b", t, flags=re.IGNORECASE)
    if not m:
        return None
    try:
        v = int(m.group(1))
        return v if v > 0 else None
    except Exception:
        return None

def _collect_lot_text(lot, lot_fields) -> str:
    chunks: List[str] = []

    def add(v):
        if isinstance(v, str):
            vv = v.strip()
            if vv and vv not in chunks:
                chunks.append(vv)

    for obj in (lot_fields, lot):
        if not obj:
            continue
        for attr in ("full_description", "description", "desc", "text", "public_description", "offer_description", "short_description", "title", "name"):
            add(getattr(obj, attr, None))
    for obj in (lot_fields, lot):
        if not obj or not hasattr(obj, "__dict__") or not isinstance(obj.__dict__, dict):
            continue
        for _k, _v in obj.__dict__.items():
            if isinstance(_v, str):
                nv = _norm_param_key(_v)
                if (GIFT_PARAM_KEY_NORM and GIFT_PARAM_KEY_NORM in nv) or ("gift" in _v.lower() and "tg" in _v.lower()):
                    add(_v)
        for _k, _v in obj.__dict__.items():
            if isinstance(_v, str) and 0 < len(_v.strip()) <= 5000:
                add(_v)
    fs = getattr(lot_fields, "fields", None)
    if isinstance(fs, list):
        for f in fs:
            if hasattr(f, "__dict__") and isinstance(f.__dict__, dict):
                for _k, _v in f.__dict__.items():
                    if isinstance(_v, str):
                        add(_v)
            for attr in ("value", "text", "name", "label"):
                add(getattr(f, attr, None))
    elif isinstance(fs, dict):
        for _v in fs.values():
            add(_v if isinstance(_v, str) else None)
    return "\n".join(chunks)

def _obj_keys_preview(obj, limit: int = 40) -> str:
    try:
        d = getattr(obj, "__dict__", None)
        if isinstance(d, dict):
            keys = list(d.keys())
            if len(keys) > limit:
                keys = keys[:limit] + ["…"]
            return ", ".join(str(k) for k in keys)
    except Exception:
        pass
    return "—"

def lot_required_stars_from_description(desc: str) -> Optional[int]:
    text = desc or ""
    gift_num = parse_gift_num(text)
    if not gift_num:
        return _parse_stars_from_text_hint(text)
    try:
        _ids_per_unit, price_per_unit, _title, _is_set_any, is_choice, choice_options = resolve_item(gift_num)
        if is_choice:
            mx = _choice_max_price(choice_options)
            return int(mx) if mx > 0 else None
        v = int(price_per_unit)
        return v if v > 0 else None
    except Exception:
        return _parse_stars_from_text_hint(text)

def deactivate_lots_over_balance(account: Account, subcat_id: int, balance: int):
    log_warn("", f"Выборочная деактивация: subcat={subcat_id}, balance={balance}⭐ ...")
    lots = _list_my_subcat_lots(account, subcat_id)
    if not lots:
        log_info("", "Лоты не найдены — пропускаю.")
        return
    affected: List[str] = []
    skipped_unknown = 0
    for lot in lots:
        try:
            fields = account.get_lot_fields(lot.id)
            is_active = bool(getattr(fields, "active", False))
            if not is_active:
                continue
            title = short_text(_safe_attr(lot, "title", "name", default=str(getattr(lot, "id", "?"))), 80)
            lot_text = _collect_lot_text(lot, fields)
            need = lot_required_stars_from_description(lot_text)
            if need is None:
                skipped_unknown += 1
                snippet = short_text(lot_text.replace("\n", " "), 160)
                log_warn("", f"Не смог определить ⭐ по описанию лота: {title} (id={lot.id}) — оставляю активным | text≈'{snippet}' | lot_fields_keys={_obj_keys_preview(fields)}")
                continue
            if need > balance:
                ok = update_lot_state(account, lot, active=False)
                if ok:
                    affected.append(f"{title} (id={lot.id}, need={need}⭐)")
        except Exception as e:
            log_error("", f"Ошибка выборочной деактивации лота {getattr(lot, 'id', '?')}: {short_text(e)}")
            logger.debug("Подробности:", exc_info=True)
    if affected:
        log_warn("", "Деактивированы (дороже текущего баланса):\n- " + "\n- ".join(affected))
    else:
        log_info("", "Нет лотов дороже баланса — ничего не выключал.")
    if skipped_unknown:
        log_warn("", f"Лотов с неизвестной ценой в ⭐ (не выключал): {skipped_unknown}")

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

def _deliver_normal(account: Account, chat_id: int, author_id: int, st: dict, ctx_user: str):
    order_id = st["order_id"]
    qty = int(st.get("qty", 1))
    recipients = st.get("recipients") or []
    assign = expand_assignment(recipients, qty) if recipients else []
    item_title = st.get("gift_title", "товар")
    ids_per_unit = list(st.get("ids_per_unit") or [])
    price = int(st.get("price", 0) or 0)
    sm(account, chat_id, "send_start_normal", item_title=item_title, qty=qty)
    log_info(ctx_user, f"NORMAL: START delivery {item_title} x{qty}")
    sent_units = 0
    failed_units = 0
    failed_reasons: List[str] = []
    for i in range(qty):
        username = assign[i]
        unit_ok = True
        for gid in ids_per_unit:
            hide_my_name = bool(st.get("hide_my_name", ANONYMOUS_GIFTS))
            ok, info = send_gift_sync(username, gift_id=gid, hide_my_name=hide_my_name)
            if ok:
                time.sleep(0.25)
                log_info(ctx_user, f"NORMAL: OK -> {username} [unit {i + 1}/{qty}] part={gid}")
                continue
            kind = classify_send_error(str(info))
            failed_reasons.append(kind)
            unit_ok = False
            log_warn(ctx_user, f"NORMAL: FAIL -> {username}: {kind} :: {short_text(info)}")
            if kind == "balance_low":
                sm(account, chat_id, "send_err_balance_low_seller")
                break
            elif kind == "spam_block":
                sm(account, chat_id, "send_err_flood")
                break
            elif kind == "flood":
                sm(account, chat_id, "send_err_flood")
                break
            elif kind == "username_not_found":
                if AUTO_REFUND:
                    try_partial_refund(account, order_id, 1, {"price": price, "title": item_title}, chat_id, ctx=ctx_user)
                break
            elif kind == "network":
                sm(account, chat_id, "send_err_network")
                _ensure_pyro_alive_sync()
                break
            else:
                break
        if unit_ok:
            sent_units += 1
        else:
            failed_units += 1
            if failed_reasons and failed_reasons[-1] in ("balance_low", "flood", "spam_block", "network"):
                break
    if sent_units > 0:
        sm(account, chat_id, "send_done_units", sent_units=sent_units)
        _completed_buyers.add(author_id)
    if failed_units > 0:
        sm(account, chat_id, "send_failed_units", failed_units=failed_units, reasons=", ".join(set(failed_reasons)))
    if failed_units == 0 and sent_units == qty:
        order_url = f"https://funpay.com/orders/{order_id}/"
        sm(account, chat_id, "request_review", order_url=order_url)
    waiting.pop(author_id, None)

def _deliver_choice(account: Account, chat_id: int, author_id: int, st: dict, ctx_user: str):
    order_id = st["order_id"]
    qty = int(st.get("qty", 1))
    recipient = st.get("choice_recipient")
    gift_id = st.get("choice_selected_gift_id")
    gift_title = st.get("choice_selected_title")
    price = int(st.get("choice_selected_price") or 0)
    if not recipient or not gift_id or not gift_title:
        sm(account, chat_id, "choice_state_error")
        return
    sm(account, chat_id, "send_start_choice", gift_title=gift_title, qty=qty, recipient=recipient)
    log_info(ctx_user, f"CHOICE: START delivery {gift_title} x{qty} to {recipient}")
    sent_units = 0
    failed_units = 0
    failed_reasons: List[str] = []
    for i in range(qty):
        hide_my_name = bool(st.get("hide_my_name", ANONYMOUS_GIFTS))
        ok, info = send_gift_sync(recipient, gift_id=int(gift_id), hide_my_name=hide_my_name)   
        if ok:
            sent_units += 1
            time.sleep(0.25)
            log_info(ctx_user, f"CHOICE: OK -> {recipient} [unit {i + 1}/{qty}] gift_id={gift_id}")
            continue
        kind = classify_send_error(str(info))
        failed_units += 1
        failed_reasons.append(kind)
        log_warn(ctx_user, f"CHOICE: FAIL -> {recipient}: {kind} :: {short_text(info)}")
        if kind == "username_not_found":
            sm(account, chat_id, "send_err_username_not_found")
            if AUTO_REFUND:
                if sent_units == 0:
                    refund_order(account, order_id, chat_id, ctx=ctx_user)
                else:
                    remaining = qty - sent_units
                    try_partial_refund(account, order_id, remaining, {"price": price, "title": gift_title}, chat_id, ctx=ctx_user)
            break
        if kind == "balance_low":
            sm(account, chat_id, "send_err_balance_low_contact")
            break
        if kind in ("flood", "spam_block"):
            sm(account, chat_id, "send_err_flood")
            break
        if kind == "network":
            sm(account, chat_id, "send_err_network")
            _ensure_pyro_alive_sync()
            break
        sm(account, chat_id, "send_err_generic")
        break
    if sent_units > 0:
        sm(account, chat_id, "send_done_units", sent_units=sent_units)
    if failed_units > 0:
        sm(account, chat_id, "send_failed_units", failed_units=failed_units, reasons=", ".join(set(failed_reasons)))
    if failed_units == 0 and sent_units == qty:
        order_url = f"https://funpay.com/orders/{order_id}/"
        sm(account, chat_id, "request_review", order_url=order_url)
        _completed_buyers.add(author_id)
    waiting.pop(author_id, None)

def main():
    check_branding_or_warn()
    _log_banner_red()
    if not GOLDEN_KEY:
        log_error("", "❌ В .env должен быть FUNPAY_AUTH_TOKEN")
        return

    threading.Thread(target=_thread_target, daemon=True).start()
    _app_started.wait(timeout=15.0)
    if not _app_started.is_set():
        log_warn("", "Pyrogram не успел стартовать за 15 сек. Продолжаю.")

    if BAD_TOKENS:
        log_warn("", f"CATEGORY_ID(S) содержит нечисловые значения и они будут проигнорированы: {BAD_TOKENS}")

    account = Account(GOLDEN_KEY)
    try:
        account.get()
    except UnauthorizedError as e:
        raw = str(e)
        log_error("", "❌ Не удалось авторизоваться/подключиться к FunPay (UnauthorizedError).")
        if ("Статус-код ответа: 200" in raw) or ("status-code" in raw.lower() and "200" in raw):
            log_error("", "FunPay вернул HTML-страницу при запросе (HTTP 200), а не API-ответ. Проверьте FUNPAY_AUTH_TOKEN и сеть.")
        else:
            log_error("", f"Детали: {short_text(raw, 260)}")
        return
    except RequestError as e:
        raw = str(e)
        log_error("", "❌ Ошибка запроса к FunPay (RequestError).")
        if ("Статус-код ответа: 200" in raw) or ("<!DOCTYPE html" in raw) or ("<html" in raw.lower()):
            log_error("", "Похоже, FunPay отвечает HTML-страницей вместо данных.")
        else:
            log_error("", f"Детали: {short_text(raw, 260)}")
        return
    except Exception as e:
        log_error("", f"❌ Не удалось подключиться/авторизоваться в FunPay: {short_text(e)}")
        return

    if not getattr(account, "username", None):
        log_error("", "Не удалось авторизоваться в FunPay. Проверьте FUNPAY_AUTH_TOKEN (golden_key).")
        return

    global ACCOUNT_GLOBAL
    ACCOUNT_GLOBAL = account
    log_info("", f"Авторизован на FunPay как @{account.username}")
    if AUTO_RAISE_LOTS:
        log_info(
            "raise",
            f"AUTO_RAISE_LOTS=ON subcats={AUTO_RAISE_SUBCATS_LIST} "
            f"interval={AUTO_RAISE_INTERVAL_SECONDS}s jitter={AUTO_RAISE_JITTER_SECONDS}s"
        )
        threading.Thread(target=_auto_raise_loop, args=(GOLDEN_KEY,), daemon=True).start()
    else:
        log_info("raise", "AUTO_RAISE_LOTS=OFF")
    _load_manual_orders()
    log_info("manual", f"Загружено ручных заказов: {len(_MANUAL_ORDERS)}")

    runner = Runner(account)
    log_info("", "Ожидаю события от FunPay...")

    for event in runner.listen(requests_delay=3.0):
        try:
            reload_messages()
            now = time.time()

            if isinstance(event, NewOrderEvent):
                order = account.get_order(event.order.id)
                buyer_id = getattr(order, "buyer_id", None)
                if buyer_id is None:
                    continue

                if _is_manual_order(order.id):
                    log_warn("manual", f"SKIP auto: order_id={order.id} buyer_id={buyer_id} chat_id={order.chat_id}")
                    try:
                        with _MANUAL_LOCK:
                            rec = _MANUAL_ORDERS.get(str(order.id), {})
                            notified = bool(rec.get("notified", False))
                        if not notified:
                            account.send_message(order.chat_id, "🛑 Этот заказ переведён в ручной режим. Ожидайте продавца.")
                            _set_manual_notified(order.id)
                    except Exception:
                        pass
                    continue

                _completed_buyers.discard(buyer_id)
                if now - _last_reply_by_buyer.get(buyer_id, 0.0) < COOLDOWN_SECONDS:
                    continue

                subcat_id, _ = get_subcategory_id_safe(order, account)
                if subcat_id not in ALLOWED_CATEGORY_IDS:
                    continue

                desc = (getattr(order, "full_description", None) or getattr(order, "short_description", None) or getattr(order, "title", None) or "")
                gift_num = parse_gift_num(desc)
                if not gift_num:
                    ctx = f"Buyer {buyer_id} @{getattr(order, 'buyer_username', '')}"
                    log_error(ctx, f"❌ Отсутствует обязательный параметр {GIFT_PARAM_KEY} в описании заказа. Заказ пропущен без ответа в чат.")
                    continue

                try:
                    ids_per_unit, price_per_unit, item_title, is_set_any, is_choice, choice_options = resolve_item(gift_num)
                except KeyError:
                    sm(account, order.chat_id, "gift_num_not_found", gift_param_key=GIFT_PARAM_KEY, gift_num=gift_num)
                    log_info(f"Buyer {buyer_id}", f"{GIFT_PARAM_KEY}:{gift_num} не найден ни в gifts.json, ни в наборах.")
                    _last_reply_by_buyer[buyer_id] = now
                    continue

                qty = parse_quantity(order, desc)
                if qty <= 0:
                    sm(account, order.chat_id, "qty_invalid")
                    _last_reply_by_buyer[buyer_id] = now
                    continue

                shown_price = "?"
                if not is_choice:
                    shown_price = str(int(price_per_unit))
                ctx_purchase = pretty_order_context(order, gift={"title": item_title, "price": shown_price, "id": ids_per_unit[0] if ids_per_unit else "?"})
                log_info(ctx_purchase, f"Новый заказ принят. qty={qty}, is_choice={is_choice}, {GIFT_PARAM_KEY}={gift_num}")

                bal = None
                need_all = 0
                if is_choice:
                    max_price = _choice_max_price(choice_options)
                    need_all = max_price * qty if max_price > 0 else 0
                else:
                    price = int(price_per_unit)
                    need_all = price * qty

                if PRECHECK_BALANCE and need_all > 0:
                    pick_idx, pick_bal = pick_account_for_need_sync(need_all)
                    if pick_idx is not None and TG_MANAGER is not None:
                        TG_MANAGER.set_active(pick_idx)
                    bal = pick_bal
                    if isinstance(bal, int) and bal < need_all:
                        if AUTO_REFUND:
                            sm(account, order.chat_id, "seller_balance_low_refund")
                        else:
                            sm(account, order.chat_id, "seller_balance_low_wait")
                        log_warn(ctx_purchase, f"BALANCE_TOO_LOW pre-check: bal={bal}, need_all={need_all}, qty={qty}")
                        if AUTO_DEACTIVATE:
                            for cid in CATEGORY_IDS_LIST:
                                try:
                                    deactivate_lots_over_balance(account, cid, bal)
                                except Exception as e:
                                    log_error(ctx_purchase, f"Ошибка выборочной деактивации в {cid}: {e}")
                        if AUTO_REFUND:
                            refund_order(account, order.id, order.chat_id, ctx=ctx_purchase)
                        _last_reply_by_buyer[buyer_id] = now
                        continue
                    elif bal is None:
                        sm(account, order.chat_id, "stars_check_unavailable")

                log_info(ctx_purchase, f"need_all={need_all}, bal={bal}")

                if ANON_POLICY == "forced":
                    hide_my_name = bool(ANON_FORCED_VALUE)
                    need_ask = False
                elif ANON_POLICY == "seller":
                    hide_my_name = bool(ANONYMOUS_GIFTS)
                    need_ask = False
                else:
                    hide_my_name = None
                    need_ask = True

                next_state = "awaiting_choice_nick" if is_choice else "awaiting_nicks"
                state = "awaiting_anon" if need_ask else next_state

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
                    "state": state,
                    "next_state": next_state,
                    "recipients": [],
                    "choice_recipient": None,
                    "choice_selected_key": None,
                    "choice_selected_title": None,
                    "choice_selected_gift_id": None,
                    "choice_selected_price": None,
                }

                if need_ask:
                    shown_price = ""
                    if not is_choice:
                        shown_price = f"{int(price_per_unit)}⭐"
                    account.send_message(order.chat_id, get_message(
                        "anon_choose_prompt",
                        item_title=item_title,
                        qty=qty,
                        shown_price=shown_price
                    ))
                else:
                    if is_choice:
                        account.send_message(order.chat_id, get_message("order_start_choice", item_title=item_title, qty=qty))
                    else:
                        shown_price = f"{int(price_per_unit)}⭐"
                        account.send_message(order.chat_id, get_message("order_start_normal", item_title=item_title, qty=qty, shown_price=shown_price))


                log_info(ctx_purchase, f"Состояние создано: state={waiting[buyer_id]['state']}")
                _last_reply_by_buyer[buyer_id] = now
                continue

            elif isinstance(event, NewMessageEvent):
                msg = event.message
                chat_id = msg.chat_id
                author_id = msg.author_id
                raw_text = msg.text or ""
                text = _strip_invisible(raw_text).strip()
                seller_id = getattr(account, "id", None)
                mstop = _STOP_CMD_RE.match(text)
                if mstop and seller_id is not None and author_id == seller_id:
                    order_id_arg = mstop.group(1)
                    note = (mstop.group(2) or "").strip()

                    found = _find_waiting_by_chat(chat_id)
                    buyer_id = found[0] if found else None
                    st = found[1] if found else None

                    order_id = None
                    if order_id_arg:
                        order_id = str(order_id_arg).strip()
                    elif st:
                        order_id = str(st.get("order_id")).strip() if st.get("order_id") is not None else None

                    if order_id is not None and st is None:
                        try:
                            o = account.get_order(order_id)
                            if o and int(getattr(o, "chat_id", 0) or 0) == int(chat_id):
                                buyer_id = getattr(o, "buyer_id", None)
                        except Exception:
                            pass

                    _mark_order_manual(order_id, chat_id=chat_id, buyer_id=buyer_id, actor_id=author_id, note=note)
                    log_warn("manual", f"!stop by seller -> order_id={order_id} chat_id={chat_id} buyer_id={buyer_id} note='{note}'")

                    if st and buyer_id is not None:
                        waiting.pop(buyer_id, None)

                    try:
                        account.send_message(chat_id, f"🛑 Заказ #{order_id} переведён в ручной режим. Автовыдача отключена.")
                    except Exception:
                        pass

                    _last_reply_by_buyer[author_id] = now
                    continue

                if author_id in _completed_buyers:
                    continue
                if now - _last_reply_by_buyer.get(author_id, 0.0) < COOLDOWN_SECONDS:
                    continue
                if author_id == getattr(account, "id", None):
                    continue

                manual_oid = _manual_order_for_chat(chat_id)
                if manual_oid is not None:
                    last = _last_manual_notice_by_chat.get(int(chat_id), 0.0)
                    if now - last >= MANUAL_NOTICE_COOLDOWN:
                        _last_manual_notice_by_chat[int(chat_id)] = now
                        log_info("manual", f"Buyer message ignored (manual mode): chat_id={chat_id} order_id={manual_oid} author_id={author_id}")
                        try:
                            account.send_message(chat_id, "ℹ️ Заказ в ручном режиме. Пожалуйста, ожидайте продавца.")
                        except Exception:
                            pass
                    _last_reply_by_buyer[author_id] = now
                    continue

                if author_id not in waiting:
                    continue

                st = waiting[author_id]
                qty = int(st.get("qty", 1))
                is_choice = bool(st.get("is_choice"))
                ctx_user = pretty_order_context(None, buyer_id=author_id, gift={"title": st.get("gift_title", "?"), "price": "?", "id": "?"})

                if st.get("state") == "awaiting_anon":
                    ans = parse_anon_choice(text)
                    if ans is None:
                        sm(account, chat_id, "anon_choose_bad")
                        _last_reply_by_buyer[author_id] = now
                        continue

                    st["hide_my_name"] = bool(ans)
                    st["state"] = st.get("next_state") or ("awaiting_choice_nick" if st.get("is_choice") else "awaiting_nicks")

                    sm(account, chat_id, "anon_chosen", mode=("анонимно" if ans else "не анонимно"))

                    if st.get("is_choice"):
                        account.send_message(chat_id, get_message("order_start_choice", item_title=st.get("gift_title", "товар"), qty=int(st.get("qty", 1))))
                    else:
                        shown_price = f"{int(st.get('price', 0) or 0)}⭐" if int(st.get('price', 0) or 0) > 0 else "?"
                        account.send_message(chat_id, get_message("order_start_normal", item_title=st.get("gift_title", "товар"), qty=int(st.get("qty", 1)), shown_price=shown_price))

                    _last_reply_by_buyer[author_id] = now
                    continue

                if is_choice:
                    maybe_nick = parse_single_recipient(text)
                    if maybe_nick and st["state"] in ("awaiting_choice_pick", "awaiting_choice_confirmation"):
                        st["choice_recipient"] = maybe_nick
                        log_info(ctx_user, f"CHOICE: получатель обновлён -> {maybe_nick}")
                        if st.get("choice_selected_title"):
                            sm(account, chat_id, "choice_recipient_updated_with_selected", recipient=maybe_nick, gift_title=st["choice_selected_title"], qty=qty)
                        else:
                            sm(account, chat_id, "choice_recipient_updated_no_selected", recipient=maybe_nick)
                        _last_reply_by_buyer[author_id] = now
                        continue

                    if st["state"] == "awaiting_choice_nick":
                        recip = parse_single_recipient(text)
                        if not recip:
                            sm(account, chat_id, "choice_need_one_nick")
                            _last_reply_by_buyer[author_id] = now
                            continue
                        st["choice_recipient"] = recip
                        st["state"] = "awaiting_choice_pick"
                        log_info(ctx_user, f"CHOICE: получатель -> {recip}")
                        options_raw = list(st.get("choice_options") or [])
                        options_norm, menu = _choice_menu(options_raw)
                        st["choice_options"] = options_norm
                        if not options_norm:
                            sm(account, chat_id, "choice_empty_options")
                            log_error(ctx_user, "CHOICE: пустые варианты")
                            if AUTO_REFUND:
                                refund_order(account, st["order_id"], chat_id, ctx="choice-empty-options")
                            waiting.pop(author_id, None)
                            _last_reply_by_buyer[author_id] = now
                            continue
                        sm(account, chat_id, "choice_pick_prompt", menu=menu)
                        _last_reply_by_buyer[author_id] = now
                        continue

                    if st["state"] == "awaiting_choice_pick":
                        options_norm = list(st.get("choice_options") or [])
                        idx = _parse_choice_index(text, max_n=len(options_norm))
                        if idx is None:
                            _, menu = _choice_menu(options_norm)
                            sm(account, chat_id, "choice_bad_number", max_n=len(options_norm), menu=menu)
                            _last_reply_by_buyer[author_id] = now
                            continue
                        gift_key = options_norm[idx - 1]
                        g = GIFTS.get(str(gift_key))
                        if not g:
                            sm(account, chat_id, "choice_gift_missing")
                            log_error(ctx_user, f"CHOICE: gift_key={gift_key} отсутствует в gifts.json")
                            if AUTO_REFUND:
                                refund_order(account, st["order_id"], chat_id, ctx="choice-gift-missing")
                            waiting.pop(author_id, None)
                            _last_reply_by_buyer[author_id] = now
                            continue
                        recipient = st.get("choice_recipient")
                        if not recipient:
                            st["state"] = "awaiting_choice_nick"
                            sm(account, chat_id, "choice_send_nick_first")
                            _last_reply_by_buyer[author_id] = now
                            continue
                        st["choice_selected_key"] = gift_key
                        st["choice_selected_title"] = g.get("title", f"Подарок {gift_key}")
                        st["choice_selected_gift_id"] = int(g["id"])
                        st["choice_selected_price"] = int(g.get("price", 0) or 0)
                        log_info(ctx_user, f"CHOICE: выбрано -> {st['choice_selected_title']} (gift_key={gift_key}), qty={qty}")
                        if REQUIRE_PLUS_CONFIRMATION:
                            st["state"] = "awaiting_choice_confirmation"
                            sm(account, chat_id, "choice_selected_confirm", gift_title=st["choice_selected_title"], recipient=recipient, qty=qty)
                            _last_reply_by_buyer[author_id] = now
                            continue
                        else:
                            st["state"] = "delivering"
                            _deliver_choice(account, chat_id, author_id, st, ctx_user)
                            _last_reply_by_buyer[author_id] = now
                            continue

                    if st["state"] == "awaiting_choice_confirmation":
                        if not is_plus_confirm(raw_text):
                            options_norm = list(st.get("choice_options") or [])
                            idx = _parse_choice_index(text, max_n=len(options_norm))
                            if idx is not None:
                                gift_key = options_norm[idx - 1]
                                g = GIFTS.get(str(gift_key))
                                if not g:
                                    sm(account, chat_id, "choice_selected_missing")
                                    _last_reply_by_buyer[author_id] = now
                                    continue
                                st["choice_selected_key"] = gift_key
                                st["choice_selected_title"] = g.get("title", f"Подарок {gift_key}")
                                st["choice_selected_gift_id"] = int(g["id"])
                                st["choice_selected_price"] = int(g.get("price", 0) or 0)
                                log_info(ctx_user, f"CHOICE: выбор обновлён -> {st['choice_selected_title']} (gift_key={gift_key})")
                                recipient = st.get("choice_recipient") or "—"
                                sm(account, chat_id, "choice_selection_updated", gift_title=st["choice_selected_title"], recipient=recipient, qty=qty)
                                _last_reply_by_buyer[author_id] = now
                                continue
                            sm(account, chat_id, "choice_need_plus_or_number")
                            _last_reply_by_buyer[author_id] = now
                            continue
                        st["state"] = "delivering"
                        _deliver_choice(account, chat_id, author_id, st, ctx_user)
                        _last_reply_by_buyer[author_id] = now
                        continue

                if st["state"] == "awaiting_nicks":
                    recips = parse_recipients(text)
                    if not recips:
                        sm(account, chat_id, "normal_bad_format")
                        _last_reply_by_buyer[author_id] = now
                        continue
                    st["recipients"] = recips
                    assign = expand_assignment(recips, qty)
                    plan = _format_plan(assign)
                    if REQUIRE_PLUS_CONFIRMATION:
                        st["state"] = "awaiting_confirmation"
                        sm(account, chat_id, "normal_plan_confirm", item_title=st.get("gift_title", "товар"), plan=plan)
                        log_info(ctx_user, f"NORMAL: получатели приняты. plan={plan}")
                        _last_reply_by_buyer[author_id] = now
                        continue
                    else:
                        st["state"] = "delivering"
                        _deliver_normal(account, chat_id, author_id, st, ctx_user)
                        _last_reply_by_buyer[author_id] = now
                        continue

                if st["state"] == "awaiting_confirmation":
                    if is_plus_confirm(raw_text):
                        st["state"] = "delivering"
                        _deliver_normal(account, chat_id, author_id, st, ctx_user)
                        _last_reply_by_buyer[author_id] = now
                        continue
                    recips = parse_recipients(text)
                    if recips:
                        st["recipients"] = recips
                        assign = expand_assignment(recips, qty)
                        plan = _format_plan(assign)
                        sm(account, chat_id, "normal_plan_updated", plan=plan)
                        log_info(ctx_user, f"NORMAL: план обновлён. plan={plan}")
                    else:
                        sm(account, chat_id, "normal_need_plus_or_list")
                    _last_reply_by_buyer[author_id] = now
                    continue

        except Exception as e:
            log_error("", f"Ошибка обработки события: {short_text(e)}")
            logger.debug("Подробности ошибки обработки события:", exc_info=True)

if __name__ == "__main__":
    main()
