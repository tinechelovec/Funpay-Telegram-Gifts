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

load_dotenv()
GOLDEN_KEY = os.getenv("FUNPAY_AUTH_TOKEN")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH")

CATEGORY_ID = int(os.getenv("CATEGORY_ID", "3064"))
COOLDOWN_SECONDS = float(os.getenv("REPLY_COOLDOWN_SECONDS", "1.0"))
DEACTIVATE_ON_LOW_STARS = os.getenv("DEACTIVATE_ON_LOW_STARS", "1") == "1"
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
LOT_DEACTIVATE_SLEEP = float(os.getenv("LOT_DEACTIVATE_SLEEP", "0.35"))

LOG_NAME = "FunPay-Gifts"

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
        secondary_log_colors={},
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
    if ctx:
        logger.info(f"{ctx} | {msg}")
    else:
        logger.info(msg)


def log_warn(ctx: str, msg: str):
    if ctx:
        logger.warning(f"{ctx} | {msg}")
    else:
        logger.warning(msg)


def log_error(ctx: str, msg: str):
    if ctx:
        logger.error(f"{ctx} | {msg}")
    else:
        logger.error(msg)


with open("gifts.json", "r", encoding="utf-8") as f:
    GIFTS = json.load(f)

loop = asyncio.new_event_loop()
app: Optional[Client] = None


def start_pyrogram():
    global app
    asyncio.set_event_loop(loop)
    app = Client("stars", api_id=API_ID, api_hash=API_HASH, workdir="sessions")

    async def runner():
        await app.start()
        log_info("", "Pyrogram запущен — готов отправлять подарки.")

    loop.run_until_complete(runner())
    loop.run_forever()


threading.Thread(target=start_pyrogram, daemon=True).start()

waiting: dict[int, dict] = {}


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


async def send_gift_once(username: str, gift_id: int):
    try:
        uname = username.lstrip("@")
        res = await app.send_gift(chat_id=uname, gift_id=gift_id)
        return True, str(res)
    except Exception as e:
        return False, str(e)


def send_gift_sync(username: str, gift_id: int, timeout: float = 30.0) -> Tuple[bool, str]:
    fut = asyncio.run_coroutine_threadsafe(send_gift_once(username, gift_id), loop)
    try:
        return fut.result(timeout=timeout)
    except Exception as e:
        return False, f"Timeout/await error: {e}"


async def get_stars_balance_once() -> int:
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
    fut = asyncio.run_coroutine_threadsafe(get_stars_balance_once(), loop)
    try:
        return fut.result(timeout=timeout)
    except Exception as e:
        logger.debug(f"get_stars_balance_sync error: {e}", exc_info=True)
        return 0


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
    except Exception as e:
        logger.debug("get_my_subcategory_lots failed, пытаю запасной путь", exc_info=True)

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
            if DRY_RUN:
                log_info("", f"[DRY_RUN] Пропущено изменение лота {lot.id} -> active={active}")
                return True
            lot_fields.active = active
            account.save_lot(lot_fields)
            log_warn("", f"Лот {getattr(lot,'id','?')} изменён: active={active}")
            return True
        except Exception as e:
            log_error("", f"Ошибка при изменении лота {getattr(lot,'id','?')}: {short_text(e)}")
            logger.debug("Подробности update_lot_state:", exc_info=True)
            attempts -= 1
            time.sleep(1.0)
    log_error("", f"Не удалось изменить лот {getattr(lot,'id','?')} (исчерпаны попытки)")
    return False


def deactivate_lots(account: Account, subcat_id: int):
    if not DEACTIVATE_ON_LOW_STARS:
        log_info("", "Авто-деактивация лотов отключена.")
        return
    log_warn("", f"Запускаю деактивацию лотов в подкатегории {subcat_id}...")
    lots = _list_my_subcat_lots(account, subcat_id)
    if not lots:
        log_info("", "Лоты не найдены — пропускаю деактивацию.")
        return
    affected: List[str] = []
    for lot in lots:
        try:
            fields = account.get_lot_fields(lot.id)
            is_active = bool(getattr(fields, "active", False))
            title = short_text(
                _safe_attr(lot, "title", "description", default=str(getattr(lot, "id", "?"))), 80
            )
            if not is_active:
                log_info("", f"Лот уже выключен: {title} (id={lot.id})")
                continue
            ok = update_lot_state(account, lot, active=False)
            if ok:
                affected.append(f"{title} (id={lot.id})")
            time.sleep(LOT_DEACTIVATE_SLEEP)
        except Exception as e:
            log_error("", f"Ошибка при деактивации лота {getattr(lot,'id','?')}: {short_text(e)}")
            logger.debug("Подробности деактивации лота:", exc_info=True)
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
    return "other"


def main():
    if not GOLDEN_KEY or not API_ID or not API_HASH:
        log_error("", "В .env должны быть: GOLDEN_KEY (или FUNPAY_AUTH_TOKEN), API_ID, API_HASH")
        return

    account = Account(GOLDEN_KEY)
    account.get()
    if not getattr(account, "username", None):
        log_error("", "Не удалось авторизоваться в FunPay. Проверьте ключ.")
        return

    log_info("", f"Авторизован на FunPay как @{account.username}")
    runner = Runner(account)
    log_info("", "Ожидаю события от FunPay...")

    last_reply_ts = 0.0

    for event in runner.listen(requests_delay=3.0):
        try:
            now = time.time()
            if now - last_reply_ts < COOLDOWN_SECONDS:
                continue

            if isinstance(event, NewOrderEvent):
                order = account.get_order(event.order.id)
                subcat_id, subcat = get_subcategory_id_safe(order, account)
                if subcat_id != CATEGORY_ID:
                    logger.debug(f"Пропуск заказа #{getattr(order,'id','?')}: subcat={subcat_id}")
                    continue

                desc = (
                    getattr(order, "full_description", None)
                    or getattr(order, "short_description", None)
                    or getattr(order, "title", None)
                    or ""
                )
                gift_num = parse_gift_num(desc)
                if not gift_num:
                    ctx = f"Buyer {order.buyer_id} @{getattr(order, 'buyer_username', '')}"
                    account.send_message(
                        order.chat_id,
                        "❌ В описании заказа отсутствует обязательный параметр gift_tg. "
                        "Произошла ошибка при обработке заказа — сейчас оформляем возврат средств.",
                    )
                    log_warn(ctx, "Отсутствует gift_tg в описании → оформляю возврат")
                    try:
                        refund_order(account, order.id, order.chat_id, ctx=ctx)
                    except Exception as e:
                        log_error(ctx, f"Ошибка при оформлении возврата: {short_text(e)}")
                        logger.debug("Подробности ошибки возврата:", exc_info=True)
                    last_reply_ts = now
                    continue

                gift = GIFTS.get(gift_num)
                if not gift:
                    account.send_message(order.chat_id, f"Номер gift_tg:{gift_num} отсутствует. Укажите корректный номер.")
                    log_info(f"Buyer {order.buyer_id}", f"gift_tg:{gift_num} не найден в gifts.json.")
                    last_reply_ts = now
                    continue

                waiting[order.buyer_id] = {
                    "chat_id": order.chat_id,
                    "order_id": order.id,
                    "gift_num": gift_num,
                    "gift": gift,
                    "state": "awaiting_nick",
                    "temp_nick": None,
                }
                account.send_message(
                    order.chat_id,
                    f"Спасибо за покупку! К выдаче: {gift['title']} ({gift['price']}⭐).\nПришлите ваш Telegram-тег (пример: @username).",
                )
                log_info(pretty_order_context(order, gift=gift), "Ждём ник.")
                last_reply_ts = now

            elif isinstance(event, NewMessageEvent):
                msg = event.message
                chat_id = msg.chat_id
                author_id = msg.author_id
                text = (msg.text or "").strip()
                if author_id == getattr(account, "id", None) or author_id not in waiting:
                    continue

                st = waiting[author_id]
                gift = st["gift"]
                order_id = st["order_id"]

                if st["state"] == "awaiting_nick":
                    if not nick_looks_valid(text):
                        account.send_message(chat_id, "Неверный тег. Отправьте в виде @username (5–32 символа).")
                        last_reply_ts = now
                        continue
                    st["temp_nick"] = text.lstrip()
                    st["state"] = "awaiting_confirmation"
                    account.send_message(
                        chat_id, f'Вы указали {st["temp_nick"]} для получения {gift["title"]} ({gift["price"]}⭐).\nЕсли верно — напишите "+"'
                    )
                    log_info(pretty_order_context(None, buyer_id=author_id, gift=gift), "Ник получен — ждём подтверждение.")
                    last_reply_ts = now
                    continue

                if st["state"] == "awaiting_confirmation":
                    if text == "+":
                        username = st["temp_nick"]
                        account.send_message(chat_id, f"Отправляю {gift['title']} пользователю {username}...")
                        log_info(pretty_order_context(None, buyer_id=author_id, gift=gift), f"Отправка -> {username}")
                        ok, info = send_gift_sync(username, gift_id=gift["id"])
                        ctx = pretty_order_context(None, buyer_id=author_id, gift=gift)

                        if ok:
                            account.send_message(chat_id, f"Подарок успешно отправлен на {username}.")
                            log_info(ctx, f"УСПЕХ -> {username}")
                        else:
                            full_err = str(info)
                            log_error(ctx, f"Полная ошибка отправки: {full_err}")
                            logger.debug("Полная трассировка send_gift:", exc_info=True)

                            kind = classify_send_error(full_err)

                            if kind == "balance_low":
                                account.send_message(chat_id, "❌ Произошла ошибка при отправке подарка. Сейчас оформим возврат средств.")
                                log_warn(ctx, "BALANCE_TOO_LOW detected -> деактивация лотов + возврат")
                                try:
                                    deactivate_lots(account, CATEGORY_ID)
                                except Exception as e:
                                    log_error(ctx, f"Ошибка при деактивации лотов: {short_text(e)}")
                                    logger.debug("Подробности деактивации:", exc_info=True)
                                try:
                                    refund_order(account, order_id, chat_id, ctx=ctx)
                                except Exception as e:
                                    log_error(ctx, f"Ошибка при возврате: {short_text(e)}")
                                    logger.debug("Подробности возврата:", exc_info=True)

                            elif kind == "username_not_found":
                                account.send_message(chat_id, "❌ Неверный ник или такой пользователь не найден. Оформляю возврат средств.")
                                log_warn(ctx, "USERNAME_NOT_OCCUPIED -> возврат (лоты не трогаю)")
                                try:
                                    refund_order(account, order_id, chat_id, ctx=ctx)
                                except Exception as e:
                                    log_error(ctx, f"Ошибка при возврате: {short_text(e)}")
                                    logger.debug("Подробности возврата:", exc_info=True)

                            else:
                                account.send_message(chat_id, "❌ Неизвестная ошибка при отправке подарка. Оформляю возврат средств.")
                                log_warn(ctx, "Неизвестная ошибка -> пробую оформить возврат")
                                try:
                                    refund_order(account, order_id, chat_id, ctx=ctx)
                                except Exception as e:
                                    log_error(ctx, f"Ошибка при возврате: {short_text(e)}")
                                    logger.debug("Подробности возврата:", exc_info=True)

                        waiting.pop(author_id, None)
                        last_reply_ts = now
                    else:
                        if not nick_looks_valid(text):
                            account.send_message(chat_id, "Неверный тег. Отправьте в виде @username.")
                            last_reply_ts = now
                            continue
                        st["temp_nick"] = text
                        account.send_message(chat_id, f'Обновлено: {st["temp_nick"]}. Если верно — напишите "+"')
                        log_info(pretty_order_context(None, buyer_id=author_id, gift=gift), f"Ник обновлён -> {st['temp_nick']}")
                        last_reply_ts = now

        except Exception as e:
            log_error("", f"Ошибка обработки события: {short_text(e)}")
            logger.debug("Подробности ошибки обработки события:", exc_info=True)


if __name__ == "__main__":
    main()
