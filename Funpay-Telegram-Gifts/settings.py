from __future__ import annotations

import asyncio
import json
import os
import re
import getpass
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Union, Any

HERE = Path(__file__).resolve().parent
GIFTS_JSON = HERE / "gifts.json"
SETS_JSON = HERE / "gift_sets.json"
ENV_PATH = HERE / ".env"
SESSIONS_DIR = HERE / "sessions"
MESSAGES_JSON = HERE / "messages.json"

CANCEL_TOKENS = {"0", "q", "й", "exit", "quit", "выход", "назад", "отмена", "cancel", "back"}

DEFAULT_GIFTS: Dict[str, Dict] = {
    "1": {"id": 5170145012310081615, "title": "❤️ Сердце", "price": 15},
    "2": {"id": 5170233102089322756, "title": "🐻 Медведь", "price": 15},
    "3": {"id": 5170250947678437525, "title": "🎁 Подарок", "price": 25},
    "4": {"id": 5168103777563050263, "title": "🌹 Роза", "price": 25},
    "5": {"id": 5170144170496491616, "title": "🎂 Торт", "price": 50},
    "6": {"id": 5170314324215857265, "title": "💐 Цветы", "price": 50},
    "7": {"id": 5170564780938756245, "title": "🚀 Ракета", "price": 50},
    "8": {"id": 5168043875654172773, "title": "🏆 Кубок", "price": 100},
    "9": {"id": 5170690322832818290, "title": "💍 Кольцо", "price": 100},
    "10": {"id": 5170521118301225164, "title": "💎 Алмаз", "price": 100},
    "11": {"id": 6028601630662853006, "title": "🍾 Шампанское", "price": 50},
    "12": {"id": 5922558454332916696, "title": "🎄 Ёлка", "price": 50},
    "13": {"id": 5956217000635139069, "title": "🐻 Новогодний медведь", "price": 50},
}

MAX_SET_SLOTS = 50

ENV_DEFAULTS: Dict[str, str] = {
    "AUTO_REFUND": "true",
    "AUTO_DEACTIVATE": "true",
    "ANONYMOUS_GIFTS": "false",
    "ANONYMOUS_MODE": "seller",
    "CATEGORY_IDS": "3064,2418",
    "REPLY_COOLDOWN_SECONDS": "1.0",
    "PRECHECK_BALANCE": "true",
    "REQUIRE_PLUS_CONFIRMATION": "true",
    "GIFT_PARAM_KEY": "gift_tg",
    "MIN_SEND_DELAY": "0.35",
    "PER_RECIPIENT_DELAY": "1.20",
    "BURST_WINDOW_SECONDS": "10",
    "BURST_MAX_SENDS": "20",
    "SEND_JITTER": "0.08",
    "USERNAME_CACHE_TTL": "86400",
    "FLOODWAIT_EXTRA_SLEEP": "0.30",
    "SPAMBLOCK_PAUSE_SECONDS": "21600",
    "AUTO_DEACTIVATE_ON_FLOODWAIT": "false",
    "FLOOD_DEACTIVATE_COOLDOWN": "900",
    "TG_SESSIONS": "", 
    "TG_PRIMARY_SESSION": "stars",
    "TG_AUTO_SWITCH": "false",
    "TG_AUTO_SELECT_FOR_PRECHECK": "true",
    "TG_BALANCE_CACHE_SECONDS": "10",
    "TG_FAILOVER_NETWORK_PAUSE": "3",  
}

TG_ENV_HELP: Dict[str, str] = {
    "MIN_SEND_DELAY": "⏱️ Мин. пауза между ЛЮБЫМИ запросами к Telegram (в сек). Больше = безопаснее, но медленнее.",
    "PER_RECIPIENT_DELAY": "👤 Мин. пауза между отправками одному получателю (сек). Спасает от спама в одного юзера.",
    "BURST_WINDOW_SECONDS": "📦 Окно burst (сек): считаем отправки внутри этого интервала.",
    "BURST_MAX_SENDS": "🚥 Лимит отправок в burst-окне. Если превышен — бот подождёт до конца окна.",
    "SEND_JITTER": "🎲 Случайная добавка (джиттер) к задержкам (сек). Делает интервалы не идеально ровными.",
    "USERNAME_CACHE_TTL": "🧠 Кэш username→user_id (сек). Больше TTL = меньше resolve-запросов (обычно лучше).",
    "FLOODWAIT_EXTRA_SLEEP": "🧯 Доп. пауза сверху к FloodWait (сек). Чтобы не словить FloodWait повторно сразу после ожидания.",
    "SPAMBLOCK_PAUSE_SECONDS": "⛔ Если Telegram дал PeerFlood/спам-блок — стоп отправок на N секунд (пример: 21600 = 6 часов).",
    "AUTO_DEACTIVATE_ON_FLOODWAIT": "🔌 Если true — при FloodWait бот отключит лоты из CATEGORY_IDS, чтобы не набирались новые заказы.",
    "FLOOD_DEACTIVATE_COOLDOWN": "🕒 Авто-деактивация при FloodWait не чаще, чем раз в N секунд (защита от постоянных выключений).",
}

TG_SESS_ENV_HELP: Dict[str, str] = {
    "TG_SESSIONS": (
        "📲 Список Telegram-сессий, которые бот будет использовать.\n"
        "• Формат: stars,stars2,stars3\n"
        "• Если пусто — бот сам возьмёт все *.session из папки sessions/.\n"
        "• Если указать — будут использоваться только эти сессии."
    ),
    "TG_PRIMARY_SESSION": (
        "⭐ Основная сессия.\n"
        "Бот сначала пробует её, а остальные — как резерв (если включён авто-свитч)."
    ),
    "TG_AUTO_SWITCH": (
        "🔁 Авто-переключение между сессиями.\n"
        "Если true — при FloodWait/PeerFlood/сетевых проблемах/недостатке Stars бот попробует другую сессию."
    ),
    "TG_AUTO_SELECT_FOR_PRECHECK": (
        "💰 Авто-выбор сессии под precheck баланса.\n"
        "Если true и TG_AUTO_SWITCH=true — бот перед выдачей выберет аккаунт, где хватает Stars.\n"
        "Если false — проверяет только активную (primary) сессию."
    ),
    "TG_BALANCE_CACHE_SECONDS": (
        "🧠 Кэш Stars-баланса (в секундах).\n"
        "Чем больше — тем реже запросы к Telegram, меньше риск flood."
    ),
    "TG_FAILOVER_NETWORK_PAUSE": (
        "🌐 Пауза (сек) на конкретную сессию после сетевой ошибки.\n"
        "Нужно, чтобы бот не долбил одну и ту же сломанную сессию каждую секунду."
    ),
}

DEFAULT_MESSAGES: Dict[str, str] = {
    "order_start_choice": "✅ Спасибо за покупку!\n🧾 Товар: {item_title} ×{qty} (выбор подарка)\n\n👤 Пришлите никнейм получателя (@username).\n",
    "order_start_normal": "✅ Спасибо за покупку!\n🧾 К выдаче: {item_title} ×{qty} по {shown_price}.\n\n👤 Пришлите теги получателей:\n• один @username — тогда вся выдача уйдёт ему\n• или список через запятую/пробел/перенос: @u1, @u2, @u3",
    "gift_not_found": "Номер {gift_param_key}:{gift_num} отсутствует. Укажите корректный номер.",
    "bad_quantity": "❌ Неверное количество. Свяжитесь с админом.",
    "precheck_balance_low_refund": "❌ У продавца недостаточно средств.\nСейчас оформлю полный возврат заказа.",
    "precheck_balance_low_wait": "❌ У продавца недостаточно средств.\nОжидайте ответа продавца.",
    "stars_check_unavailable": "⚠️ Временная ошибка связи с Telegram Stars — проверка баланса недоступна. Продолжаю обработку заказа; если звёзд не хватит при отправке — сообщу отдельно.",
    "awaiting_nicks_bad_format": "❌ Неверный формат. Пришлите один @username или список через запятую/пробел.",
    "awaiting_choice_one_nick": "❌ Пришлите ровно ОДИН никнейм (@username).",
    "choice_menu": "🎁 Выберите подарок, который хотите получить.\nНапишите номер из списка:\n\n{menu}",
    "choice_bad_number": "❌ Неверный номер. Выберите 1–{max_n}:\n\n{menu}",
    "choice_confirm": "✅ Вы выбрали: {gift_title}\n👤 Получатель: {recipient}\n📦 Количество: {qty}\n\nЕсли всё верно — отправьте «+».\nЧтобы изменить выбор — отправьте другой номер.",
    "normal_plan_confirm": "📦 План выдачи: {item_title} — {plan}.\n✅ Если верно — напишите «+». Или пришлите новый список получателей.",
    "normal_plan_info": "📦 План выдачи: {item_title} — {plan}.",
    "normal_plan_updated": "✅ План обновлён: {plan}. Для подтверждения отправьте «+».",
    "need_plus_or_update": "Для подтверждения отправьте «+». Либо пришлите новый список получателей.",
    "deliver_start_normal": "🚚 Отправляю {item_title} — всего {qty} шт.",
    "deliver_start_choice": "🚚 Отправляю: {gift_title} ×{qty} → {recipient}",
    "send_err_balance_low": "⚠️ Недостаточно Stars у продавца. Выдачу остановил.",
    "send_err_flood": "⚠️ Слишком много запросов (flood). Попробуйте позже.",
    "send_err_network": "⚠️ Проблема с соединением с Telegram. Попробуйте позже.",
    "send_err_network_choice": "⚠️ Проблема связи с Telegram. Попробуйте позже.",
    "send_err_username_not_found": "❌ Никнейм не найден в Telegram. Проверьте @username.",
    "send_err_generic": "⚠️ Ошибка отправки. Попробуйте позже или свяжитесь с продавцом.",
    "sent_success": "🎉 Успешно отправлено: {sent_units} шт.",
    "sent_failed": "⚠️ Не удалось отправить: {failed_units} шт. Причины: {reasons}",
    "request_review": "🙏 Подтвердите выполнение заказа и оставьте отзыв: {order_url}",
    "refund_done": "✅ Средства возвращены.",
    "refund_fail": "❌ Ошибка возврата. Свяжитесь с админом.",
    "partial_refund_amount": "✅ Возврат за неотправленные позиции: {units} шт. → {stars}⭐.",
    "partial_refund_units": "✅ Возврат за {units} шт. оформлен.",
    "choice_recipient_updated_with_selected": "✅ Получатель обновлён: {recipient}\n🎁 Выбрано: {gift_title} ×{qty}\nЕсли всё верно — отправьте «+».",
    "choice_recipient_updated_no_selected": "✅ Получатель: {recipient}\nТеперь выберите номер подарка.",
    "choice_pick_need_recipient": "❌ Сначала пришлите никнейм получателя (@username).",
    "choice_confirm_need_plus": "Для отправки подтвердите «+». Или отправьте номер, чтобы изменить выбор.",
    "choice_confirm_updated": "✅ Выбор обновлён: {gift_title}\n👤 Получатель: {recipient}\n📦 Количество: {qty}\n\nПодтвердите отправку: «+».",
    "choice_choose_first": "❌ Сначала выберите подарок:\n\n{menu}",
    "choice_error_empty_options": "❌ Ошибка: список вариантов пуст. Свяжитесь с админом.",
    "choice_error_gift_missing": "❌ Ошибка: выбранный подарок отсутствует в gifts.json. Свяжитесь с админом.",
    "choice_state_error": "❌ Ошибка состояния выбора. Свяжитесь с админом.",
    "anon_choose_prompt": (
    "✅ Спасибо за покупку!\n"
    "🧾 К выдаче: {item_title} ×{qty}.\n"
    "{shown_price}\n\n"
    "Отправлять подарок анонимно?\n"
    "1) Да (анонимно)\n"
    "2) Нет (показывать имя)\n\n"
    "Ответьте 1 или 2."
    ),
    "anon_choose_bad": "❌ Не понял. Ответьте 1 (анонимно) или 2 (не анонимно).",
    "anon_chosen": "✅ Ок, отправляю: {mode}.",
}

@dataclass
class SetItem:
    gift_key: str
    qty: int = 1

@dataclass
class FixedGiftSet:
    key: str
    title: str
    items: List[SetItem] = field(default_factory=list)
    mode: str = "fixed"

    def compute_price(self, base_gifts: Dict[str, Dict]) -> int:
        total = 0
        for it in self.items:
            if it.gift_key not in base_gifts:
                raise ValueError(f"В наборе {self.key} обнаружен подарок {it.gift_key}, которого нет в gifts.json.")
            price = int(base_gifts[it.gift_key]["price"])
            total += price * int(it.qty)
        return total

    def expand_to_gift_ids(self, base_gifts: Dict[str, Dict]) -> List[int]:
        expanded: List[int] = []
        for it in self.items:
            gift = base_gifts[it.gift_key]
            expanded.extend([int(gift["id"])] * int(it.qty))
        return expanded

@dataclass
class ChoiceGiftSet:
    key: str
    title: str
    options: List[str] = field(default_factory=list)
    mode: str = "choice"

    def inferred_price(self, base_gifts: Dict[str, Dict]) -> Optional[int]:
        prices: List[int] = []
        for gk in self.options:
            g = base_gifts.get(str(gk))
            if not g:
                continue
            prices.append(int(g.get("price", 0) or 0))
        prices = [p for p in prices if p > 0]
        if not prices:
            return None
        if all(p == prices[0] for p in prices):
            return prices[0]
        return None

    def compute_price(self, base_gifts: Dict[str, Dict]) -> int:
        p = self.inferred_price(base_gifts)
        if p is None:
            raise ValueError("choice_set_price_unknown")
        return int(p)

GiftSet = Union[FixedGiftSet, ChoiceGiftSet]

def load_base_gifts() -> Dict[str, Dict]:
    if GIFTS_JSON.exists():
        with GIFTS_JSON.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): v for k, v in data.items()}
    return DEFAULT_GIFTS

def load_sets() -> Dict[str, GiftSet]:
    if not SETS_JSON.exists():
        return {}
    with SETS_JSON.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    out: Dict[str, GiftSet] = {}
    for k, v in raw.items():
        k = str(k)
        mode = str(v.get("mode", "fixed")).lower().strip()

        if mode == "choice":
            options_raw = v.get("options", []) or []
            options: List[str] = []
            for it in options_raw:
                if isinstance(it, str):
                    options.append(it)
                elif isinstance(it, dict) and "gift_key" in it:
                    options.append(str(it["gift_key"]))
            out[k] = ChoiceGiftSet(key=k, title=v.get("title", f"Набор {k}"), options=[str(x) for x in options])
        else:
            items = [
                SetItem(gift_key=str(i["gift_key"]), qty=int(i.get("qty", 1)))
                for i in v.get("items", []) or []
                if isinstance(i, dict) and "gift_key" in i
            ]
            out[k] = FixedGiftSet(key=k, title=v.get("title", f"Набор {k}"), items=items)

    return out

def save_sets(sets: Dict[str, GiftSet]) -> None:
    payload: Dict[str, dict] = {}
    for k, s in sets.items():
        if isinstance(s, ChoiceGiftSet):
            payload[k] = {"mode": "choice", "title": s.title, "options": [str(x) for x in s.options]}
        else:
            payload[k] = {"mode": "fixed", "title": s.title, "items": [asdict(it) for it in s.items]}
    with SETS_JSON.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def resolve_to_gift_ids(key: str | int, base_gifts: Dict[str, Dict] | None = None) -> List[int]:
    base = base_gifts or load_base_gifts()
    sets = load_sets()
    key_s = str(key)

    if key_s in sets:
        s = sets[key_s]
        if isinstance(s, ChoiceGiftSet):
            raise ValueError("Это набор-выбор. Нужен выбор конкретного подарка.")
        return s.expand_to_gift_ids(base)

    if key_s not in base:
        raise KeyError(f"Подарок {key} не найден.")
    return [int(base[key_s]["id"])]

def get_required_stars(key: str | int, base_gifts: Dict[str, Dict] | None = None) -> int:
    base = base_gifts or load_base_gifts()
    sets = load_sets()
    key_s = str(key)

    if key_s in sets:
        s = sets[key_s]
        if isinstance(s, ChoiceGiftSet):
            return s.compute_price(base)
        return int(s.compute_price(base))

    if key_s not in base:
        raise KeyError(f"Подарок {key} не найден.")
    return int(base[key_s]["price"])

def get_gift_id_bounds(base: Dict[str, Dict]) -> Tuple[int, int]:
    ids: List[int] = []
    for k in base.keys():
        try:
            ids.append(int(k))
        except ValueError:
            pass
    if not ids:
        return 1, 1
    return min(ids), max(ids)

def _is_cancel_token(s: str) -> bool:
    return s.strip().lower() in CANCEL_TOKENS

def press_enter() -> None:
    input("Нажмите Enter, чтобы продолжить: ")

def input_int(
    prompt: str,
    *,
    min_val: Optional[int] = None,
    max_val: Optional[int] = None,
    allow_blank: bool = False,
    allow_cancel: bool = True,
) -> Optional[int]:
    while True:
        s = input(prompt).strip()
        if allow_cancel and _is_cancel_token(s):
            return None
        if s == "" and allow_blank:
            return None
        if not s.lstrip("-").isdigit():
            print("Введите число.")
            continue
        val = int(s)
        if min_val is not None and val < min_val:
            print(f"Число должно быть не меньше {min_val}.")
            continue
        if max_val is not None and val > max_val:
            print(f"Число должно быть не больше {max_val}.")
            continue
        return val

def input_str(prompt: str, *, allow_empty: bool = False, allow_cancel: bool = True) -> Optional[str]:
    while True:
        s = input(prompt).strip()
        if allow_cancel and _is_cancel_token(s):
            return None
        if s == "" and not allow_empty:
            print("Пустое значение недопустимо.")
            continue
        return s

def yes_no(prompt: str) -> bool:
    while True:
        s = input(f"{prompt} (y/n, 0 — отмена): ").strip().lower()
        if _is_cancel_token(s):
            return False
        if s in ("y", "yes", "д", "да"):
            return True
        if s in ("n", "no", "н", "нет"):
            return False
        print("Ответьте y или n.")

def print_gifts_catalog(base: Dict[str, Dict]) -> None:
    print("🎁 Доступные подарки (из gifts.json):")
    for k in sorted(base.keys(), key=lambda x: int(x) if x.isdigit() else 10**18):
        g = base[k]
        print(f"  {k:>2}: {g['title']} — {g['price']}⭐ (Telegram ID: {g['id']})")

def summarize_fixed_set(s: FixedGiftSet, base: Dict[str, Dict]) -> Tuple[str, int]:
    lines = [f"🧩 [{s.key}] {s.title}  (Обычный набор)"]
    total = 0
    for it in s.items:
        g = base[it.gift_key]
        price = int(g["price"]) * it.qty
        total += price
        lines.append(f"  • {g['title']} — {it.qty} шт. = {price}⭐ (код {it.gift_key})")
    lines.append(f"💰 Итого: {total}⭐")
    return "\n".join(lines), total

def summarize_choice_set(s: ChoiceGiftSet, base: Dict[str, Dict]) -> Tuple[str, Optional[int]]:
    lines = [f"🎲 [{s.key}] {s.title}  (Набор-выбор)"]
    lines.append("📝 Цена задаётся продавцом на FunPay (в лоте).")
    p = s.inferred_price(base)
    if p is not None:
        lines.append(f"ℹ️ Подсказка: все варианты по {p}⭐.")
    else:
        lines.append("⚠️ Варианты разной цены (проверьте, что цена лота на FunPay вам подходит).")
    lines.append("🎁 Варианты:")
    for gk in s.options:
        g = base.get(str(gk))
        if g:
            lines.append(f"  • {gk}: {g['title']} ({g.get('price', '?')}⭐)")
        else:
            lines.append(f"  • {gk}: (нет в gifts.json)")
    return "\n".join(lines), p

def choose_existing_set_id(sets: Dict[str, GiftSet], *, min_set_id: int) -> Optional[str]:
    if not sets:
        print("Пока нет созданных наборов.")
        return None

    print("📦 Текущие наборы:")
    for k in sorted(sets.keys(), key=lambda x: int(x) if x.isdigit() else 10**18):
        s = sets[k]
        tag = "🎲 выбор" if isinstance(s, ChoiceGiftSet) else "🧩 обычный"
        print(f"  {k}: {s.title} ({tag})")

    sid = input_int(f"Укажите ID набора (число ≥ {min_set_id}, 0 — назад): ", min_val=min_set_id, allow_cancel=True)
    if sid is None:
        return None
    sid_s = str(sid)
    if sid_s not in sets:
        print("Набор с таким ID не найден.")
        return None
    return sid_s

def _calc_min_set_id(base: Dict[str, Dict]) -> int:
    _gift_min, gift_max = get_gift_id_bounds(base)
    return gift_max + 1

def cmd_create_fixed_set() -> None:
    base = load_base_gifts()
    sets = load_sets()

    gift_min, gift_max = get_gift_id_bounds(base)
    min_set_id = _calc_min_set_id(base)

    print("=== 🧩 Создание обычного набора ===")
    print("Отмена: 0 / «отмена» / «назад».")
    print_gifts_catalog(base)

    title = input_str("Название набора: ", allow_empty=False, allow_cancel=True)
    if title is None:
        print("Отменено.")
        return

    print("Соберите состав набора.")
    print(f"Введите ID подарков по одному ({gift_min}–{gift_max}). Enter — закончить.")
    gift_keys: List[str] = []
    i = 1
    while i <= MAX_SET_SLOTS:
        s = input(f"Подарок №{i} — ID: ").strip()
        if _is_cancel_token(s):
            print("Отменено.")
            return
        if s == "":
            break
        if not s.isdigit() or s not in base:
            print(f"Укажите ID из списка ({gift_min}–{gift_max}).")
            continue
        gift_keys.append(s)
        i += 1

    if not gift_keys:
        print("Состав пустой.")
        return

    print("Количество для каждого подарка:")
    items: List[SetItem] = []
    for idx, gk in enumerate(gift_keys, start=1):
        qty = input_int(f"{idx}. {base[gk]['title']} — сколько выдавать (1–999): ", min_val=1, max_val=999, allow_cancel=True)
        if qty is None:
            print("Отменено.")
            return
        items.append(SetItem(gift_key=gk, qty=qty))

    while True:
        set_id = input_int(f"ID набора (gift_tg), число ≥ {min_set_id}: ", min_val=min_set_id, max_val=10**9, allow_cancel=True)
        if set_id is None:
            print("Отменено.")
            return
        set_id_s = str(set_id)
        if set_id_s in sets:
            print("Этот ID уже используется.")
            continue
        break

    s = FixedGiftSet(key=set_id_s, title=title, items=items)
    summary, _ = summarize_fixed_set(s, base)

    print("Проверьте:")
    print(summary)
    if yes_no("Сохранить"):
        sets[set_id_s] = s
        save_sets(sets)
        print("✅ Сохранено.")
    else:
        print("Не сохранено.")

def cmd_create_choice_set() -> None:
    base = load_base_gifts()
    sets = load_sets()

    gift_min, gift_max = get_gift_id_bounds(base)
    min_set_id = _calc_min_set_id(base)

    print("=== 🎲 Создание набора-выбора ===")
    print("Покупатель выбирает 1 подарок из списка. Цена задаётся вами на FunPay.")
    print("Отмена: 0 / «отмена» / «назад».")
    print_gifts_catalog(base)

    title = input_str("Название набора-выбора: ", allow_empty=False, allow_cancel=True)
    if title is None:
        print("Отменено.")
        return

    print(f"Введите ID подарка ({gift_min}–{gift_max}). Enter — закончить.")
    options: List[str] = []
    i = 1
    while i <= MAX_SET_SLOTS:
        s = input(f"Вариант №{i} — ID: ").strip()
        if _is_cancel_token(s):
            print("Отменено.")
            return
        if s == "":
            break
        if not s.isdigit() or s not in base:
            print(f"Укажите ID из списка ({gift_min}–{gift_max}).")
            continue
        if s in options:
            print("Уже добавлено.")
            continue
        options.append(s)
        i += 1

    if not options:
        print("Список вариантов пустой.")
        return

    while True:
        set_id = input_int(f"ID набора (gift_tg), число ≥ {min_set_id}: ", min_val=min_set_id, max_val=10**9, allow_cancel=True)
        if set_id is None:
            print("Отменено.")
            return
        set_id_s = str(set_id)
        if set_id_s in sets:
            print("Этот ID уже используется.")
            continue
        break

    s = ChoiceGiftSet(key=set_id_s, title=title, options=options)
    summary, _maybe_price = summarize_choice_set(s, base)

    print("Проверьте:")
    print(summary)
    if yes_no("Сохранить"):
        sets[set_id_s] = s
        save_sets(sets)
        print("✅ Сохранено.")
    else:
        print("Не сохранено.")

def cmd_edit_set() -> None:
    base = load_base_gifts()
    sets = load_sets()
    min_set_id = _calc_min_set_id(base)
    gift_min, gift_max = get_gift_id_bounds(base)

    print("=== ✏️ Редактирование набора ===")
    sid = choose_existing_set_id(sets, min_set_id=min_set_id)
    if not sid:
        return

    s = sets[sid]
    changed = False

    if isinstance(s, ChoiceGiftSet):
        print("Что изменить?")
        print("  1) Список вариантов")
        print("  2) ID набора")
        print("  3) Название")
        print("  0) Назад")
        choice = input_int("Ваш выбор: ", min_val=0, max_val=3, allow_cancel=True)
        if choice in (None, 0):
            return

        if choice == 1:
            print_gifts_catalog(base)
            print(f"Новые варианты ({gift_min}–{gift_max}). Enter — закончить.")
            opts: List[str] = []
            i = 1
            while i <= MAX_SET_SLOTS:
                val = input(f"Вариант №{i} — ID: ").strip()
                if _is_cancel_token(val):
                    print("Отменено.")
                    return
                if val == "":
                    break
                if not val.isdigit() or val not in base:
                    print(f"Укажите ID из списка ({gift_min}–{gift_max}).")
                    continue
                if val in opts:
                    print("Уже добавлено.")
                    continue
                opts.append(val)
                i += 1
            if not opts:
                print("Пусто. Без изменений.")
            else:
                s.options = opts
                changed = True

        elif choice == 2:
            while True:
                new_id = input_int(f"Новый ID (число ≥ {min_set_id}): ", min_val=min_set_id, allow_cancel=True)
                if new_id is None:
                    print("Отменено.")
                    return
                new_id_s = str(new_id)
                if new_id_s in sets and new_id_s != s.key:
                    print("Этот ID уже используется.")
                    continue
                if new_id_s != s.key:
                    sets.pop(s.key)
                    s.key = new_id_s
                    sets[new_id_s] = s
                    changed = True
                break

        elif choice == 3:
            new_title = input_str("Новое название: ", allow_empty=False, allow_cancel=True)
            if new_title is None:
                print("Отменено.")
                return
            s.title = new_title
            changed = True

        if changed:
            summary, _ = summarize_choice_set(s, base)
            print("Обновлено:")
            print(summary)
            if yes_no("Сохранить"):
                save_sets(sets)
                print("✅ Изменения сохранены.")
            else:
                print("Не сохранено.")
        else:
            print("Изменений нет.")
        return

    print("Что изменить?")
    print("  1) Состав набора (пересобрать)")
    print("  2) Количество по позициям")
    print("  3) ID набора")
    print("  4) Название")
    print("  0) Назад")

    choice = input_int("Ваш выбор: ", min_val=0, max_val=4, allow_cancel=True)
    if choice in (None, 0):
        return

    if choice == 1:
        print_gifts_catalog(base)
        print(f"Введите новые подарки по одному ({gift_min}–{gift_max}). Enter — закончить.")
        gift_keys: List[str] = []
        i = 1
        while i <= MAX_SET_SLOTS:
            val = input(f"Подарок №{i} — ID: ").strip()
            if _is_cancel_token(val):
                print("Отменено.")
                return
            if val == "":
                break
            if not val.isdigit() or val not in base:
                print(f"Укажите ID из списка ({gift_min}–{gift_max}).")
                continue
            gift_keys.append(val)
            i += 1

        if not gift_keys:
            print("Пусто. Без изменений.")
        else:
            new_items: List[SetItem] = []
            for idx, gk in enumerate(gift_keys, start=1):
                qty = input_int(f"{idx}. {base[gk]['title']} — новое количество (1–999): ", min_val=1, max_val=999, allow_cancel=True)
                if qty is None:
                    print("Отменено.")
                    return
                new_items.append(SetItem(gift_key=gk, qty=qty))
            s.items = new_items
            changed = True

    elif choice == 2:
        if not s.items:
            print("У набора нет позиций.")
        else:
            for it in s.items:
                qty = input_int(
                    f"{base[it.gift_key]['title']} — новое количество (сейчас {it.qty}): ",
                    min_val=1,
                    max_val=999,
                    allow_cancel=True,
                )
                if qty is None:
                    print("Отменено.")
                    return
                it.qty = qty
            changed = True

    elif choice == 3:
        while True:
            new_id = input_int(f"Новый ID (число ≥ {min_set_id}): ", min_val=min_set_id, allow_cancel=True)
            if new_id is None:
                print("Отменено.")
                return
            new_id_s = str(new_id)
            if new_id_s in sets and new_id_s != s.key:
                print("Этот ID уже используется.")
                continue
            if new_id_s != s.key:
                sets.pop(s.key)
                s.key = new_id_s
                sets[new_id_s] = s
                changed = True
            break

    elif choice == 4:
        new_title = input_str("Новое название: ", allow_empty=False, allow_cancel=True)
        if new_title is None:
            print("Отменено.")
            return
        s.title = new_title
        changed = True

    if changed:
        summary, _ = summarize_fixed_set(s, base)
        print("Обновлено:")
        print(summary)
        if yes_no("Сохранить"):
            save_sets(sets)
            print("✅ Изменения сохранены.")
        else:
            print("Не сохранено.")
    else:
        print("Изменений нет.")

def cmd_delete_set() -> None:
    base = load_base_gifts()
    sets = load_sets()
    min_set_id = _calc_min_set_id(base)

    print("=== 🗑️ Удаление набора ===")
    sid = choose_existing_set_id(sets, min_set_id=min_set_id)
    if not sid:
        return

    if yes_no(f"Удалить набор {sid} безвозвратно"):
        sets.pop(sid, None)
        save_sets(sets)
        print("✅ Удалено.")
    else:
        print("Отменено.")

def cmd_list_sets() -> None:
    base = load_base_gifts()
    sets = load_sets()

    print("=== 📚 Список наборов ===")
    if not sets:
        print("Пока нет ни одного набора.")
        return

    for k in sorted(sets.keys(), key=lambda x: int(x) if x.isdigit() else 10**18):
        s = sets[k]
        if isinstance(s, ChoiceGiftSet):
            summary, _ = summarize_choice_set(s, base)
        else:
            summary, _ = summarize_fixed_set(s, base)
        print(summary)
        print(f"Использование в лоте FunPay: gift_tg: {k}")
        print("-" * 48)

def _mask_secret(s: Optional[str], head: int = 4, tail: int = 4) -> str:

    s = "" if s is None else str(s).strip()
    if not s:
        return "(не задано)"
    if len(s) <= head + tail:
        return "*" * len(s)
    return f"{s[:head]}…{s[-tail:]} (len={len(s)})"

def _box(title: str) -> None:
    print("\n" + "═" * 30)
    print(title)
    print("═" * 30)

def _kv_line(k: str, v: Optional[str], dv: Optional[str] = None) -> None:
    cur = v if (v is not None and str(v).strip() != "") else "—"
    if dv is not None:
        print(f"• {k}: {cur}   (по умолчанию: {dv})")
    else:
        print(f"• {k}: {cur}")

def _parse_bool(s: str) -> bool:
    v = s.strip().lower()
    if v in ("true", "t", "1", "yes", "y", "да", "д", "on"):
        return True
    if v in ("false", "f", "0", "no", "n", "нет", "н", "off"):
        return False
    raise ValueError("Ожидается true/false (или да/нет, 1/0).")

def _ensure_env_file() -> None:
    if not ENV_PATH.exists():
        ENV_PATH.write_text("", encoding="utf-8")

def _read_env_file_lines() -> List[str]:
    _ensure_env_file()
    return ENV_PATH.read_text(encoding="utf-8").splitlines()

def _write_env_file_lines(lines: List[str]) -> None:
    ENV_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

def _set_env_key_fallback(key: str, value: str) -> None:
    key = key.strip()
    value = "" if value is None else str(value)
    lines = _read_env_file_lines()
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
    replaced = False
    out: List[str] = []
    for line in lines:
        if pat.match(line):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and out[-1].strip() != "":
            out.append("")
        out.append(f"{key}={value}")
    _write_env_file_lines(out)

def _load_env_into_os() -> None:
    _ensure_env_file()
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH, override=True)
        return
    except Exception:
        pass

    for line in _read_env_file_lines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        os.environ[k.strip()] = v.strip()

def _set_env_key(key: str, value: str) -> None:
    _ensure_env_file()
    try:
        from dotenv import set_key
        set_key(str(ENV_PATH), key, value)
    except Exception:
        _set_env_key_fallback(key, value)
    _load_env_into_os()

def _get_env(key: str) -> Optional[str]:
    v = os.getenv(key)
    if v is None:
        return None
    v = v.strip()
    return v if v else None

_MESSAGES_CACHE: Optional[Dict[str, str]] = None
_MESSAGES_MTIME: float = 0.0

def _ensure_messages_file() -> None:
    if not MESSAGES_JSON.exists():
        save_messages(dict(DEFAULT_MESSAGES))

def _maybe_reload_messages() -> None:
    global _MESSAGES_CACHE, _MESSAGES_MTIME
    _ensure_messages_file()
    try:
        mt = MESSAGES_JSON.stat().st_mtime
    except Exception:
        return
    if _MESSAGES_CACHE is None or mt != _MESSAGES_MTIME:
        _MESSAGES_CACHE = load_messages()
        _MESSAGES_MTIME = mt

def load_messages() -> Dict[str, str]:
    _ensure_messages_file()
    try:
        with MESSAGES_JSON.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            out: Dict[str, str] = {}
            for k, v in raw.items():
                if isinstance(k, str) and isinstance(v, str):
                    out[k] = v
            return out
    except Exception:
        pass
    return {}

def save_messages(msgs: Dict[str, str]) -> None:
    payload: Dict[str, str] = {}
    for k, v in (msgs or {}).items():
        if isinstance(k, str) and isinstance(v, str):
            payload[k] = v
    with MESSAGES_JSON.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + str(key) + "}"

def get_message(key: str, **kwargs) -> str:
    global _MESSAGES_CACHE
    _maybe_reload_messages()
    tpl = None
    if _MESSAGES_CACHE:
        tpl = _MESSAGES_CACHE.get(str(key))
    if tpl is None:
        tpl = DEFAULT_MESSAGES.get(str(key), "")
    try:
        return str(tpl).format_map(_SafeDict(kwargs))
    except Exception:
        return str(tpl)

def reload_messages() -> None:
    global _MESSAGES_CACHE, _MESSAGES_MTIME
    _MESSAGES_CACHE = None
    _MESSAGES_MTIME = 0.0
    _maybe_reload_messages()

def list_message_keys() -> List[str]:
    custom = load_messages()
    keys = set(DEFAULT_MESSAGES.keys()) | set(custom.keys())
    return sorted(keys)

def _ensure_optional_defaults_written() -> None:
    changed = 0
    for k, dv in ENV_DEFAULTS.items():
        if _get_env(k) is None:
            _set_env_key(k, dv)
            changed += 1
    if changed:
        print(f"✅ Записал(а) отсутствующие дефолты: {changed} шт.")
    else:
        print("👌 Все необязательные ключи уже были на месте. Ничего не менял(а).")

def _prompt_required_str(key: str, current: Optional[str], *, secret: bool = False) -> str:
    while True:
        if secret:
            hint = "задано" if current else "НЕ задано"
            print(f"{key} (секрет) — сейчас: {hint}")
            val = getpass.getpass("Введите значение (Enter — оставить как есть): ").strip()
        else:
            cur_show = current if current else "(НЕ задано)"
            print(f"{key} — сейчас: {cur_show}")
            val = input("Введите значение (Enter — оставить как есть): ").strip()

        if val == "":
            if current:
                return current
            print("Это обязательный пункт. Пустым оставить нельзя.")
            continue
        return val

def _prompt_required_int(key: str, current: Optional[str]) -> int:
    while True:
        cur_show = current if current else "(НЕ задано)"
        print(f"{key} — сейчас: {cur_show}")
        val = input("Введите число (Enter — оставить как есть): ").strip()

        if val == "":
            if current:
                try:
                    return int(current)
                except ValueError:
                    print("Сейчас в .env не число. Введите корректное значение.")
                    continue
            print("Это обязательный пункт. Пустым оставить нельзя.")
            continue

        try:
            return int(val)
        except ValueError:
            print("Нужно целое число.")

def _prompt_bool_key(key: str, current: Optional[str], default: bool) -> str:
    cur_show = current if current else "(не задано)"
    print(f"{key} — сейчас: {cur_show}")
    print(f"Введите true/false (Enter — дефолт: {str(default).lower()})")
    while True:
        val = input("> ").strip()
        if val == "":
            return str(default).lower()
        try:
            return str(_parse_bool(val)).lower()
        except ValueError as e:
            print(str(e))

def _prompt_anonymous_mode(current: Optional[str]) -> str:
    cur = (current or ENV_DEFAULTS.get("ANONYMOUS_MODE", "seller")).strip().lower()

    aliases = {
        "seller": "seller",
        "prod": "seller",
        "default": "seller",

        "buyer": "buyer",
        "ask": "buyer",
        "customer": "buyer",
        "client": "buyer",

        "yes": "yes",
        "true": "yes",
        "1": "yes",
        "anon": "yes",
        "anonymous": "yes",

        "no": "no",
        "false": "no",
        "0": "no",
        "public": "no",
    }
    cur = aliases.get(cur, "seller")

    print("ANONYMOUS_MODE — режим анонимности отправки подарка:")
    print("  1) seller  — решает продавец (берём ANONYMOUS_GIFTS=true/false)")
    print("  2) buyer   — покупатель выбирает в чате (1/2) для СВОЕГО заказа")
    print("  3) yes     — всегда анонимно (принудительно)")
    print("  4) no      — всегда НЕ анонимно (принудительно)")
    print(f"Сейчас: {cur}")
    print("Введите номер (1–4) или слово (seller/buyer/yes/no). Enter — оставить как есть.")

    val = input("> ").strip().lower()
    if val == "":
        return cur
    if _is_cancel_token(val):
        return cur

    if val in ("1", "seller"):
        return "seller"
    if val in ("2", "buyer"):
        return "buyer"
    if val in ("3", "yes"):
        return "yes"
    if val in ("4", "no"):
        return "no"

    val2 = aliases.get(val)
    if val2 in ("seller", "buyer", "yes", "no"):
        return val2

    print("❌ Не понял. Оставляю как было.")
    return cur

def _prompt_float(key: str, current: Optional[str], default: float) -> str:
    cur_show = current if current else str(default)
    print(f"{key} — сейчас: {cur_show}")
    while True:
        val = input("Введите число (Enter — оставить как есть): ").strip()
        if val == "":
            return cur_show
        try:
            return str(float(val.replace(",", ".")))
        except ValueError:
            print("Нужно число, например 1.0")

def _prompt_int_env(key: str, current: Optional[str], default: int, *, min_val: Optional[int] = None, max_val: Optional[int] = None) -> str:
    cur_show = current if current else str(default)
    print(f"{key} — сейчас: {cur_show}")
    while True:
        val = input("Введите целое число (Enter — оставить как есть): ").strip()
        if val == "":
            return cur_show
        if not val.lstrip("-").isdigit():
            print("Нужно целое число.")
            continue
        n = int(val)
        if min_val is not None and n < min_val:
            print(f"Число должно быть не меньше {min_val}.")
            continue
        if max_val is not None and n > max_val:
            print(f"Число должно быть не больше {max_val}.")
            continue
        return str(n)

def _prompt_category_ids(key: str, current: Optional[str], default: str) -> str:
    cur_show = current if current else "(не задано)"
    print(f"{key} — сейчас: {cur_show}")
    print(f"Введите через запятую, например: 3064,2418 (Enter — дефолт: {default})")
    while True:
        val = input("> ").strip().replace(" ", "")
        if val == "":
            return current if current else default
        parts = [p for p in val.split(",") if p]
        if not parts:
            print("Пусто.")
            continue
        if any(not p.isdigit() for p in parts):
            print("Все значения должны быть числами.")
            continue
        return ",".join(parts)

def _print_env_summary() -> None:
    _load_env_into_os()
    _box("📋 Текущие настройки (.env)")

    def show(k: str, *, secret: bool = False, default: Optional[str] = None) -> None:
        v = _get_env(k)
        if secret:
            print(f"{k:>24} = {_mask_secret(v)}")
            return
        if v is None and default is not None:
            print(f"{k:>24} = {default}  (default)")
        else:
            print(f"{k:>24} = {v or '(не задано)'}")

    print("🔐 Обязательные:")
    show("FUNPAY_AUTH_TOKEN", secret=True)
    show("API_ID")
    show("API_HASH", secret=True)

    print("\n🤖 Поведение / FunPay:")
    for k in (
        "AUTO_REFUND",
        "AUTO_DEACTIVATE",
        "ANONYMOUS_MODE",
        "ANONYMOUS_GIFTS",
        "PRECHECK_BALANCE",
        "REQUIRE_PLUS_CONFIRMATION",
        "REPLY_COOLDOWN_SECONDS",
        "CATEGORY_IDS",
        "GIFT_PARAM_KEY",
    ):
        show(k, default=ENV_DEFAULTS.get(k))

    print("\n🚦 Telegram антифлуд:")
    for k in (
        "MIN_SEND_DELAY",
        "PER_RECIPIENT_DELAY",
        "BURST_WINDOW_SECONDS",
        "BURST_MAX_SENDS",
        "SEND_JITTER",
        "USERNAME_CACHE_TTL",
        "FLOODWAIT_EXTRA_SLEEP",
        "SPAMBLOCK_PAUSE_SECONDS",
        "AUTO_DEACTIVATE_ON_FLOODWAIT",
        "FLOOD_DEACTIVATE_COOLDOWN",
    ):
        show(k, default=ENV_DEFAULTS.get(k))

    print(f"\n📄 Файл .env: {ENV_PATH}")

def _edit_required_env() -> None:
    _load_env_into_os()
    print("=== Обязательные настройки ===")
    api_id = _prompt_required_int("API_ID", _get_env("API_ID"))
    _set_env_key("API_ID", str(api_id))

    api_hash = _prompt_required_str("API_HASH", _get_env("API_HASH"), secret=False)
    _set_env_key("API_HASH", api_hash)

    token = _prompt_required_str("FUNPAY_AUTH_TOKEN", _get_env("FUNPAY_AUTH_TOKEN"), secret=True)
    _set_env_key("FUNPAY_AUTH_TOKEN", token)

    print("✅ Обязательные настройки сохранены.")

def _edit_bot_env_flags() -> None:
    _load_env_into_os()
    print("=== Настройки поведения ===")

    def dv_bool(name: str) -> bool:
        return ENV_DEFAULTS.get(name, "false").lower() == "true"

    _set_env_key("AUTO_REFUND", _prompt_bool_key("AUTO_REFUND", _get_env("AUTO_REFUND"), default=dv_bool("AUTO_REFUND")))
    _set_env_key("AUTO_DEACTIVATE", _prompt_bool_key("AUTO_DEACTIVATE", _get_env("AUTO_DEACTIVATE"), default=dv_bool("AUTO_DEACTIVATE")))
    mode = _prompt_anonymous_mode(_get_env("ANONYMOUS_MODE"))
    _set_env_key("ANONYMOUS_MODE", mode)

    if mode == "seller":
        _set_env_key("ANONYMOUS_GIFTS", _prompt_bool_key("ANONYMOUS_GIFTS", _get_env("ANONYMOUS_GIFTS"), default=dv_bool("ANONYMOUS_GIFTS")))
    else:
        cur = _get_env("ANONYMOUS_GIFTS") or ENV_DEFAULTS.get("ANONYMOUS_GIFTS", "false")
        print(f"ANONYMOUS_GIFTS сейчас: {cur} (ℹ️ в режиме ANONYMOUS_MODE={mode} этот флаг игнорируется)")

    _set_env_key("PRECHECK_BALANCE", _prompt_bool_key("PRECHECK_BALANCE", _get_env("PRECHECK_BALANCE"), default=dv_bool("PRECHECK_BALANCE")))
    _set_env_key("REQUIRE_PLUS_CONFIRMATION", _prompt_bool_key("REQUIRE_PLUS_CONFIRMATION", _get_env("REQUIRE_PLUS_CONFIRMATION"), default=dv_bool("REQUIRE_PLUS_CONFIRMATION")))
    _set_env_key("REPLY_COOLDOWN_SECONDS", _prompt_float("REPLY_COOLDOWN_SECONDS", _get_env("REPLY_COOLDOWN_SECONDS"), default=float(ENV_DEFAULTS["REPLY_COOLDOWN_SECONDS"])),)
    print("✅ Настройки поведения сохранены.")

def _edit_tg_env_settings() -> None:
    _load_env_into_os()

    _box("🚦 Telegram антифлуд / лимиты отправок")
    print("📝 Эти параметры помогают снизить шанс FloodWait/PeerFlood.\n"
          "💡 Если часто ловишь flood — увеличивай задержки и/или уменьшай burst.\n")

    def dv_float(name: str) -> float:
        try:
            return float(ENV_DEFAULTS.get(name, "0").replace(",", "."))
        except Exception:
            return 0.0

    def dv_int(name: str) -> int:
        try:
            return int(float(ENV_DEFAULTS.get(name, "0")))
        except Exception:
            return 0

    def dv_bool(name: str) -> bool:
        return ENV_DEFAULTS.get(name, "false").lower() == "true"

    presets = {
        "1": ("🐢 Безопасно", {
            "MIN_SEND_DELAY": "0.70",
            "PER_RECIPIENT_DELAY": "2.00",
            "BURST_WINDOW_SECONDS": "10",
            "BURST_MAX_SENDS": "12",
            "SEND_JITTER": "0.15",
            "USERNAME_CACHE_TTL": "86400",
            "FLOODWAIT_EXTRA_SLEEP": "0.60",
            "SPAMBLOCK_PAUSE_SECONDS": "21600",
            "AUTO_DEACTIVATE_ON_FLOODWAIT": "true",
            "FLOOD_DEACTIVATE_COOLDOWN": "900",
        }),
        "2": ("⚖️ Баланс", {
            "MIN_SEND_DELAY": "0.35",
            "PER_RECIPIENT_DELAY": "1.20",
            "BURST_WINDOW_SECONDS": "10",
            "BURST_MAX_SENDS": "20",
            "SEND_JITTER": "0.08",
            "USERNAME_CACHE_TTL": "86400",
            "FLOODWAIT_EXTRA_SLEEP": "0.30",
            "SPAMBLOCK_PAUSE_SECONDS": "21600",
            "AUTO_DEACTIVATE_ON_FLOODWAIT": "false",
            "FLOOD_DEACTIVATE_COOLDOWN": "900",
        }),
        "3": ("🚀 Быстро (риск выше)", {
            "MIN_SEND_DELAY": "0.20",
            "PER_RECIPIENT_DELAY": "0.60",
            "BURST_WINDOW_SECONDS": "10",
            "BURST_MAX_SENDS": "30",
            "SEND_JITTER": "0.05",
            "USERNAME_CACHE_TTL": "86400",
            "FLOODWAIT_EXTRA_SLEEP": "0.20",
            "SPAMBLOCK_PAUSE_SECONDS": "21600",
            "AUTO_DEACTIVATE_ON_FLOODWAIT": "false",
            "FLOOD_DEACTIVATE_COOLDOWN": "900",
        }),
    }

    print("⚙️ Быстрая настройка пресетом:")
    for k, (name, _) in presets.items():
        print(f"  {k}) {name}")
    print("  4) 🛠️ Настроить вручную")
    print("  0) ↩️ Назад")

    pick = input("Выбор: ").strip().lower()
    if pick in CANCEL_TOKENS or pick == "0":
        return

    if pick in presets:
        preset_name, kv = presets[pick]
        _box(f"✅ Применяю пресет: {preset_name}")
        for k, v in kv.items():
            _set_env_key(k, v)
            print(f"✔️ {k} = {v}")
        print("\n🎉 Готово! Значения записаны в .env.")
        return

    _box("🛠️ Ручная настройка")
    print("ℹ️ Подсказки показываются перед каждым параметром.\n")

    def explain(k: str):
        print("\n" + "—" * 30)
        print(f"🔧 {k}")
        print(TG_ENV_HELP.get(k, ""))

    explain("MIN_SEND_DELAY")
    _set_env_key("MIN_SEND_DELAY", _prompt_float("MIN_SEND_DELAY", _get_env("MIN_SEND_DELAY"), default=dv_float("MIN_SEND_DELAY")))

    explain("PER_RECIPIENT_DELAY")
    _set_env_key("PER_RECIPIENT_DELAY", _prompt_float("PER_RECIPIENT_DELAY", _get_env("PER_RECIPIENT_DELAY"), default=dv_float("PER_RECIPIENT_DELAY")))

    explain("BURST_WINDOW_SECONDS")
    _set_env_key("BURST_WINDOW_SECONDS", _prompt_float("BURST_WINDOW_SECONDS", _get_env("BURST_WINDOW_SECONDS"), default=dv_float("BURST_WINDOW_SECONDS")))

    explain("BURST_MAX_SENDS")
    _set_env_key("BURST_MAX_SENDS", _prompt_int_env("BURST_MAX_SENDS", _get_env("BURST_MAX_SENDS"), default=dv_int("BURST_MAX_SENDS"), min_val=0, max_val=10**9))

    explain("SEND_JITTER")
    _set_env_key("SEND_JITTER", _prompt_float("SEND_JITTER", _get_env("SEND_JITTER"), default=dv_float("SEND_JITTER")))

    explain("USERNAME_CACHE_TTL")
    _set_env_key("USERNAME_CACHE_TTL", _prompt_int_env("USERNAME_CACHE_TTL", _get_env("USERNAME_CACHE_TTL"), default=dv_int("USERNAME_CACHE_TTL"), min_val=0, max_val=10**12))

    explain("FLOODWAIT_EXTRA_SLEEP")
    _set_env_key("FLOODWAIT_EXTRA_SLEEP", _prompt_float("FLOODWAIT_EXTRA_SLEEP", _get_env("FLOODWAIT_EXTRA_SLEEP"), default=dv_float("FLOODWAIT_EXTRA_SLEEP")))

    explain("SPAMBLOCK_PAUSE_SECONDS")
    _set_env_key("SPAMBLOCK_PAUSE_SECONDS", _prompt_int_env("SPAMBLOCK_PAUSE_SECONDS", _get_env("SPAMBLOCK_PAUSE_SECONDS"), default=dv_int("SPAMBLOCK_PAUSE_SECONDS"), min_val=0, max_val=10**12))

    explain("AUTO_DEACTIVATE_ON_FLOODWAIT")
    _set_env_key(
        "AUTO_DEACTIVATE_ON_FLOODWAIT",
        _prompt_bool_key("AUTO_DEACTIVATE_ON_FLOODWAIT", _get_env("AUTO_DEACTIVATE_ON_FLOODWAIT"), default=dv_bool("AUTO_DEACTIVATE_ON_FLOODWAIT")),
    )

    explain("FLOOD_DEACTIVATE_COOLDOWN")
    _set_env_key("FLOOD_DEACTIVATE_COOLDOWN", _prompt_int_env("FLOOD_DEACTIVATE_COOLDOWN", _get_env("FLOOD_DEACTIVATE_COOLDOWN"), default=dv_int("FLOOD_DEACTIVATE_COOLDOWN"), min_val=0, max_val=10**12))

    print("\n🎉 ✅ Telegram антифлуд-настройки сохранены в .env.")

def _edit_category_ids() -> None:
    _load_env_into_os()
    print("=== Категории FunPay ===")
    cur = _get_env("CATEGORY_IDS")
    val = _prompt_category_ids("CATEGORY_IDS", cur, default=ENV_DEFAULTS["CATEGORY_IDS"])
    _set_env_key("CATEGORY_IDS", val)
    print("✅ CATEGORY_IDS сохранён.")


def _edit_gift_param_key() -> None:
    _load_env_into_os()
    print("=== Ключ параметра в описании лота ===")
    cur = _get_env("GIFT_PARAM_KEY") or ENV_DEFAULTS["GIFT_PARAM_KEY"]
    print(f"Сейчас: {cur}")
    val = input("Введите новое имя параметра (Enter — оставить): ").strip()
    if val == "":
        print("Без изменений.")
        return
    if not re.search(r"[A-Za-z0-9]", val):
        print("Имя параметра должно содержать буквы/цифры.")
        return
    _set_env_key("GIFT_PARAM_KEY", val)
    print("✅ GIFT_PARAM_KEY сохранён.")

def _check_funpay_auth() -> None:
    _load_env_into_os()
    token = _get_env("FUNPAY_AUTH_TOKEN")
    if not token:
        print("FUNPAY_AUTH_TOKEN не задан.")
        return

    print("Проверяю авторизацию FunPay...")
    try:
        from FunPayAPI import Account
        from FunPayAPI.common.exceptions import UnauthorizedError
    except Exception:
        print("FunPayAPI не установлен(а) или не импортируется.")
        return

    try:
        acc = Account(token)
        acc.get()
        uname = getattr(acc, "username", None)
        if uname:
            print(f"✅ Успешно! Авторизован как @{uname}")
        else:
            print("⚠️ Запрос прошёл, но username не найден.")
    except UnauthorizedError as e:
        s = str(e)
        print("❌ Не удалось авторизоваться в FunPay (UnauthorizedError).")
        if "Статус-код ответа: 200" in s or "<!DOCTYPE html" in s.lower():
            print("FunPay вернул HTML-страницу вместо API.")
            print("Проверьте FUNPAY_AUTH_TOKEN и отключите VPN/прокси, попробуйте другой интернет.")
        else:
            print(s[:600])
    except Exception as e:
        s = str(e)
        print("❌ Ошибка при проверке FunPay:", s[:600])

def _ensure_sessions_dir() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

def _list_sessions() -> List[str]:
    _ensure_sessions_dir()
    out: List[str] = []
    for p in sorted(SESSIONS_DIR.glob("*.session")):
        out.append(p.stem)
    return out

def _delete_session_files(session_name: str) -> int:
    _ensure_sessions_dir()
    deleted = 0
    base = SESSIONS_DIR / f"{session_name}.session"
    patterns = [
        base,
        SESSIONS_DIR / f"{session_name}.session-journal",
        SESSIONS_DIR / f"{session_name}.session-wal",
        SESSIONS_DIR / f"{session_name}.session-shm",
    ]
    for p in patterns:
        if p.exists():
            try:
                p.unlink()
                deleted += 1
            except Exception:
                pass
    return deleted

def _import_pyrogram() -> Tuple[Any, Any]:
    try:
        from pyrogram import Client
        from pyrogram.errors import SessionPasswordNeeded, PhoneNumberInvalid, PhoneCodeInvalid, PhoneCodeExpired, PhoneNumberUnoccupied
    except Exception as e:
        raise RuntimeError(f"Не удалось импортировать pyrogram/pyrofork: {e}")
    errs = {
        "SessionPasswordNeeded": SessionPasswordNeeded,
        "PhoneNumberInvalid": PhoneNumberInvalid,
        "PhoneCodeInvalid": PhoneCodeInvalid,
        "PhoneCodeExpired": PhoneCodeExpired,
        "PhoneNumberUnoccupied": PhoneNumberUnoccupied,
    }
    return Client, errs

def _env_api() -> Tuple[Optional[int], Optional[str]]:
    _load_env_into_os()
    api_id = _get_env("API_ID")
    api_hash = _get_env("API_HASH")
    if not api_id or not api_id.isdigit() or not api_hash:
        return None, None
    return int(api_id), api_hash

def _ask_phone() -> Optional[str]:
    while True:
        phone = input("Введите номер телефона (пример: +79991234567): ").strip().replace(" ", "")
        if _is_cancel_token(phone):
            return None
        if not phone:
            print("Номер не может быть пустым.")
            continue
        confirm = input(f"Вы ввели: {phone}. Верно? (да/нет): ").strip().lower()
        if confirm in ("да", "д", "y", "yes"):
            return phone

def _ask_code() -> Optional[str]:
    while True:
        code = input("Введите код из Telegram: ").strip().replace(" ", "")
        if _is_cancel_token(code):
            return None
        if code:
            return code
        print("Код не может быть пустым.")

async def _session_info_async(session_name: str) -> Tuple[bool, str]:
    Client, _errs = _import_pyrogram()
    api_id, api_hash = _env_api()
    if not api_id or not api_hash:
        return False, "Не заданы API_ID/API_HASH в .env."

    app = Client(session_name, api_id=api_id, api_hash=api_hash, workdir=str(SESSIONS_DIR), no_updates=True)
    try:
        await app.connect()
    except Exception as e:
        return False, f"Не удалось подключиться: {e}"

    try:
        me = await app.get_me()
        username = f"@{me.username}" if getattr(me, "username", None) else "(без username)"
        name = " ".join([x for x in [getattr(me, "first_name", None), getattr(me, "last_name", None)] if x]).strip()
        info_lines = [
            f"Сессия: {session_name}",
            f"ID: {getattr(me, 'id', '?')}",
            f"Имя: {name or '(не задано)'}",
            f"Username: {username}",
        ]
        if hasattr(app, "get_stars_balance"):
            try:
                bal = await app.get_stars_balance()
                info_lines.append(f"Stars: {bal}")
            except Exception:
                pass
        await app.disconnect()
        return True, "\n".join(info_lines)
    except Exception as e:
        try:
            await app.disconnect()
        except Exception:
            pass
        return False, f"Сессия не авторизована или повреждена: {e}"

async def _create_session_async(session_name: str) -> Tuple[bool, str]:
    Client, errs = _import_pyrogram()
    api_id, api_hash = _env_api()
    if not api_id or not api_hash:
        return False, "Не заданы API_ID/API_HASH в .env."

    app = Client(session_name, api_id=api_id, api_hash=api_hash, workdir=str(SESSIONS_DIR), no_updates=True)
    try:
        await app.connect()
    except Exception as e:
        return False, f"Не удалось подключиться: {e}"

    try:
        me = await app.get_me()
        username = f"@{me.username}" if getattr(me, "username", None) else "(без username)"
        await app.disconnect()
        return True, f"Сессия уже авторизована. Аккаунт: {username} | ID: {me.id}"
    except Exception:
        pass

    phone = _ask_phone()
    if not phone:
        await app.disconnect()
        return False, "Отменено."

    try:
        sent = await app.send_code(phone)
    except errs["PhoneNumberInvalid"]:
        await app.disconnect()
        return False, "Неверный формат номера."
    except errs["PhoneNumberUnoccupied"]:
        await app.disconnect()
        return False, "Этот номер не зарегистрирован в Telegram."
    except Exception as e:
        await app.disconnect()
        return False, f"Ошибка при отправке кода: {e}"

    code = _ask_code()
    if not code:
        await app.disconnect()
        return False, "Отменено."

    try:
        await app.sign_in(phone_number=phone, phone_code_hash=sent.phone_code_hash, phone_code=code)
    except errs["PhoneCodeInvalid"]:
        await app.disconnect()
        return False, "Код неверный."
    except errs["PhoneCodeExpired"]:
        await app.disconnect()
        return False, "Код устарел."
    except errs["SessionPasswordNeeded"]:
        pwd = getpass.getpass("Включена 2FA. Введите пароль: ").strip()
        if not pwd:
            await app.disconnect()
            return False, "Пароль пустой."
        try:
            await app.check_password(pwd)
        except Exception as e:
            await app.disconnect()
            return False, f"Ошибка 2FA пароля: {e}"
    except Exception as e:
        await app.disconnect()
        return False, f"Ошибка входа: {e}"

    try:
        me = await app.get_me()
        username = f"@{me.username}" if getattr(me, "username", None) else "(без username)"
        lines = [f"✅ Сессия создана: {session_name}", f"Аккаунт: {username} | ID: {me.id}"]
        if hasattr(app, "get_stars_balance"):
            try:
                bal = await app.get_stars_balance()
                lines.append(f"Stars: {bal}")
            except Exception:
                pass
        await app.disconnect()
        return True, "\n".join(lines)
    except Exception as e:
        await app.disconnect()
        return False, f"Вход прошёл, но не удалось получить профиль: {e}"

def _run_async(coro) -> Any:
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

def _session_name_valid(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", name or ""))

def _prompt_sessions_csv(current: Optional[str], existing: List[str]) -> str:
    cur_show = current if current is not None and current.strip() != "" else "(авто из папки sessions/)"
    print(f"TG_SESSIONS — сейчас: {cur_show}")
    if existing:
        print("Найденные сессии в папке sessions/:", ", ".join(existing))
    else:
        print("В папке sessions/ пока нет *.session.")

    print("\n" + TG_SESS_ENV_HELP.get("TG_SESSIONS", ""))
    print("\nВарианты ввода:")
    print("• Enter — оставить как есть")
    print("• auto / - / пустая строка после очистки — включить авто-режим (бот сам найдёт *.session)")
    print("• stars,stars2,stars3 — явный список\n")

    val = input("> ").strip()
    if _is_cancel_token(val):
        return current or ""

    if val == "":

        return current or ""

    if val.lower() in ("auto", "-"):
        return ""

    parts = [p.strip() for p in val.split(",") if p.strip()]
    if not parts:
        return ""

    bad = [p for p in parts if not _session_name_valid(p)]
    if bad:
        print("❌ Некорректные имена сессий:", ", ".join(bad))
        print("Разрешены: буквы/цифры/_/- (пример: stars2).")
        return current or ""

    seen = set()
    uniq: List[str] = []
    for p in parts:
        if p not in seen:
            uniq.append(p)
            seen.add(p)

    return ",".join(uniq)

def _prompt_primary_session(current: Optional[str], default: str = "stars") -> str:
    cur_show = current if current else default
    print(f"TG_PRIMARY_SESSION — сейчас: {cur_show}")
    print("\n" + TG_SESS_ENV_HELP.get("TG_PRIMARY_SESSION", ""))
    print("\nEnter — оставить как есть. Пример: stars или stars2.")
    val = input("> ").strip()
    if _is_cancel_token(val):
        return cur_show
    if val == "":
        return cur_show
    if not _session_name_valid(val):
        print("❌ Некорректное имя. Разрешены: буквы/цифры/_/-")
        return cur_show
    return val

def _edit_tg_sessions_env_settings() -> None:
    _load_env_into_os()
    _box("📲 Telegram-сессии: мульти-аккаунты / авто-переключение")

    print(
        "Бот может работать с НЕСКОЛЬКИМИ Telegram-аккаунтами (несколько .session файлов).\n"
        "Это помогает:\n"
        "• переживать FloodWait / PeerFlood\n"
        "• распределять выдачи по аккаунтам\n"
        "• выбирать аккаунт с нужным Stars-балансом\n"
    )

    existing = _list_sessions()

    cur_sessions = _get_env("TG_SESSIONS")
    if cur_sessions is None:
        cur_sessions = _get_env("TG_SESSION_NAMES")

    new_sessions = _prompt_sessions_csv(cur_sessions, existing)
    _set_env_key("TG_SESSIONS", new_sessions)
    _set_env_key("TG_SESSION_NAMES", new_sessions)

    print("\n—" * 30)
    cur_primary = _get_env("TG_PRIMARY_SESSION") or ENV_DEFAULTS.get("TG_PRIMARY_SESSION", "stars")
    new_primary = _prompt_primary_session(cur_primary, default=ENV_DEFAULTS.get("TG_PRIMARY_SESSION", "stars"))
    _set_env_key("TG_PRIMARY_SESSION", new_primary)

    print("\n—" * 30)
    print("🔧 TG_AUTO_SWITCH")
    print(TG_SESS_ENV_HELP.get("TG_AUTO_SWITCH", ""))
    _set_env_key(
        "TG_AUTO_SWITCH",
        _prompt_bool_key("TG_AUTO_SWITCH", _get_env("TG_AUTO_SWITCH"), default=(ENV_DEFAULTS.get("TG_AUTO_SWITCH", "false") == "true")),
    )

    print("\n—" * 30)
    print("🔧 TG_AUTO_SELECT_FOR_PRECHECK")
    print(TG_SESS_ENV_HELP.get("TG_AUTO_SELECT_FOR_PRECHECK", ""))
    _set_env_key(
        "TG_AUTO_SELECT_FOR_PRECHECK",
        _prompt_bool_key(
            "TG_AUTO_SELECT_FOR_PRECHECK",
            _get_env("TG_AUTO_SELECT_FOR_PRECHECK"),
            default=(ENV_DEFAULTS.get("TG_AUTO_SELECT_FOR_PRECHECK", "true") == "true"),
        ),
    )

    print("\n—" * 30)
    print("🔧 TG_BALANCE_CACHE_SECONDS")
    print(TG_SESS_ENV_HELP.get("TG_BALANCE_CACHE_SECONDS", ""))
    _set_env_key(
        "TG_BALANCE_CACHE_SECONDS",
        _prompt_float(
            "TG_BALANCE_CACHE_SECONDS",
            _get_env("TG_BALANCE_CACHE_SECONDS"),
            default=float(ENV_DEFAULTS.get("TG_BALANCE_CACHE_SECONDS", "10")),
        ),
    )

    print("\n—" * 30)
    print("🔧 TG_FAILOVER_NETWORK_PAUSE")
    print(TG_SESS_ENV_HELP.get("TG_FAILOVER_NETWORK_PAUSE", ""))
    _set_env_key(
        "TG_FAILOVER_NETWORK_PAUSE",
        _prompt_float(
            "TG_FAILOVER_NETWORK_PAUSE",
            _get_env("TG_FAILOVER_NETWORK_PAUSE"),
            default=float(ENV_DEFAULTS.get("TG_FAILOVER_NETWORK_PAUSE", "3")),
        ),
    )

    print("\n✅ Готово. Настройки мульти-сессий записаны в .env.")
    print("ℹ️ Важно: чтобы бот реально использовал несколько сессий — создай их в этом меню (пункт 3) и/или укажи TG_SESSIONS.")

def menu_sessions() -> None:
    while True:
        print("==============================")
        print("Управление Telegram-сессией")
        print("==============================")
        print("0 / отмена — назад")
        print("1) Показать сессии")
        print("2) Показать профиль по сессии")
        print("3) Создать/войти в новую сессию")
        print("4) Удалить сессию")
        print("5) ⚙️ Настройки мульти-сессий для бота (TG_*)")
        choice = input_int("Выберите пункт (0–5): ", min_val=0, max_val=5, allow_cancel=True)
        if choice in (None, 0):
            return

        if choice == 1:
            sessions = _list_sessions()
            if not sessions:
                print("Сессий нет.")
            else:
                print("Сессии:")
                for s in sessions:
                    print(f"  • {s}")
            press_enter()

        elif choice == 2:
            sessions = _list_sessions()
            if not sessions:
                print("Сессий нет.")
                press_enter()
                continue
            for i, s in enumerate(sessions, start=1):
                print(f"{i}) {s}")
            idx = input_int("Выберите номер: ", min_val=1, max_val=len(sessions), allow_cancel=True)
            if idx is None:
                continue
            try:
                ok, info = _run_async(_session_info_async(sessions[idx - 1]))
                print(info if info else ("✅" if ok else "❌"))
            except Exception as e:
                print(str(e))
            press_enter()

        elif choice == 3:
            _ensure_sessions_dir()
            default_name = "stars"
            raw = input(f"Имя сессии (Enter — {default_name}): ").strip()
            if _is_cancel_token(raw):
                continue
            session_name = raw or default_name

            existing = (SESSIONS_DIR / f"{session_name}.session").exists()
            if existing:
                if yes_no("Сессия уже есть. Пересоздать (удалить старую и войти заново)?"):
                    _delete_session_files(session_name)

            try:
                ok, info = _run_async(_create_session_async(session_name))
                print(info if info else ("✅" if ok else "❌"))
            except Exception as e:
                print(str(e))
            press_enter()

        elif choice == 4:
            sessions = _list_sessions()
            if not sessions:
                print("Сессий нет.")
                press_enter()
                continue
            for i, s in enumerate(sessions, start=1):
                print(f"{i}) {s}")
            idx = input_int("Какую удалить (номер): ", min_val=1, max_val=len(sessions), allow_cancel=True)
            if idx is None:
                continue
            sname = sessions[idx - 1]
            if yes_no(f"Удалить сессию {sname}?"):
                n = _delete_session_files(sname)
                print(f"✅ Удалено файлов: {n}")
            else:
                print("Отменено.")
            press_enter()

        elif choice == 5:
            _edit_tg_sessions_env_settings()
            press_enter()

def _read_multiline_text() -> Optional[str]:
    print("Введите текст сообщением. Завершите строкой с одной точкой: .")
    print("0/отмена — отменить ввод.")
    lines: List[str] = []
    while True:
        line = input()
        if _is_cancel_token(line) and not lines:
            return None
        if line.strip() == ".":
            return "\n".join(lines)
        lines.append(line)

def _message_preview(s: str, n: int = 60) -> str:
    s = "" if s is None else str(s)
    s = s.replace("\r", "")
    first = s.split("\n", 1)[0].strip()
    if not first:
        first = s.strip()
    return first if len(first) <= n else first[: n - 1] + "…"

def _placeholders(s: str) -> List[str]:
    return sorted(set(re.findall(r"{([A-Za-z0-9_]+)}", s or "")))

def _current_message_map() -> Tuple[Dict[str, str], Dict[str, str]]:
    reload_messages()
    custom = load_messages()
    cur: Dict[str, str] = {}
    for k in list_message_keys():
        cur[k] = custom.get(k) if k in custom else DEFAULT_MESSAGES.get(k, "")
    return cur, custom

def menu_messages() -> None:
    while True:
        print("==============================")
        print("Тексты сообщений бота")
        print("==============================")
        print("0 / отмена — назад")
        print("1) Показать тексты")
        print("2) Изменить текст")
        print("3) Сбросить текст к дефолту")
        print("4) Сбросить ВСЁ к дефолту")
        choice = input_int("Выберите пункт (0–4): ", min_val=0, max_val=4, allow_cancel=True)
        if choice in (None, 0):
            return

        cur, custom = _current_message_map()
        keys = list_message_keys()

        if choice == 1:
            for i, k in enumerate(keys, start=1):
                tag = "custom" if k in custom else "default"
                print(f"{i}) {k} [{tag}] — {_message_preview(cur[k])}")
            press_enter()

        elif choice == 2:
            for i, k in enumerate(keys, start=1):
                tag = "custom" if k in custom else "default"
                print(f"{i}) {k} [{tag}] — {_message_preview(cur[k])}")
            idx = input_int("Номер текста: ", min_val=1, max_val=len(keys), allow_cancel=True)
            if idx is None:
                continue
            key = keys[idx - 1]
            current_text = cur.get(key, "")
            print("------------------------------")
            print(f"Ключ: {key}")
            ph = _placeholders(DEFAULT_MESSAGES.get(key, current_text))
            if ph:
                print("Плейсхолдеры:", " ".join(["{" + x + "}" for x in ph]))
            print("Текущий текст:")
            print(current_text)
            print("------------------------------")
            new_text = _read_multiline_text()
            if new_text is None:
                continue
            custom[key] = new_text
            save_messages(custom)
            reload_messages()
            print("✅ Сохранено.")
            press_enter()

        elif choice == 3:
            for i, k in enumerate(keys, start=1):
                tag = "custom" if k in custom else "default"
                print(f"{i}) {k} [{tag}] — {_message_preview(cur[k])}")
            idx = input_int("Какой сбросить (номер): ", min_val=1, max_val=len(keys), allow_cancel=True)
            if idx is None:
                continue
            key = keys[idx - 1]
            if key in custom:
                custom.pop(key, None)
                save_messages(custom)
                reload_messages()
                print("✅ Сброшено к дефолту.")
            else:
                print("Уже используется дефолт.")
            press_enter()

        elif choice == 4:
            if yes_no("Сбросить ВСЕ тексты к дефолту?"):
                save_messages({})
                reload_messages()
                print("✅ Сброшено.")
            else:
                print("Отменено.")
            press_enter()

def menu_bot_settings() -> None:
    while True:
        print("==============================")
        print("⚙️ Настройка бота (.env)")
        print("==============================")
        print("0 / отмена — назад")
        print("1) 📋 Показать текущие настройки")
        print("2) 🔐 Обязательные (FUNPAY_AUTH_TOKEN, API_ID, API_HASH)")
        print("3) 🤖 Поведение бота (AUTO_REFUND и т.д.)")
        print("4) 🗂️ Категории FunPay (CATEGORY_IDS)")
        print("5) 🏷️ Ключ параметра в лоте (GIFT_PARAM_KEY)")
        print("6) 🧩 Записать дефолты для необязательных")
        print("7) ✅ Проверить авторизацию FunPay (тест)")
        print("8) 💬 Тексты сообщений бота")
        print("9) 🚦 Telegram антифлуд (лимиты отправок)")
        choice = input_int("Выберите пункт (0–9): ", min_val=0, max_val=9, allow_cancel=True)
        if choice in (None, 0):
            return

        if choice == 1:
            _print_env_summary()
        elif choice == 2:
            _edit_required_env()
        elif choice == 3:
            _edit_bot_env_flags()
        elif choice == 4:
            _edit_category_ids()
        elif choice == 5:
            _edit_gift_param_key()
        elif choice == 6:
            _load_env_into_os()
            _ensure_optional_defaults_written()
        elif choice == 7:
            _check_funpay_auth()
        elif choice == 8:
            menu_messages()
        elif choice == 9:
            _edit_tg_env_settings()
        press_enter()

def menu_sets() -> None:
    while True:
        print("==============================")
        print("Наборы подарков")
        print("==============================")
        print("0 / отмена — назад")
        print("1) Создать обычный набор (фикс состав)")
        print("2) Создать набор-выбор")
        print("3) Редактировать набор")
        print("4) Удалить набор")
        print("5) Посмотреть наборы")
        choice = input_int("Выберите пункт (0–5): ", min_val=0, max_val=5, allow_cancel=True)
        if choice in (None, 0):
            return

        if choice == 1:
            cmd_create_fixed_set()
        elif choice == 2:
            cmd_create_choice_set()
        elif choice == 3:
            cmd_edit_set()
        elif choice == 4:
            cmd_delete_set()
        elif choice == 5:
            cmd_list_sets()
        press_enter()


def main_menu() -> None:
    while True:
        print("==============================")
        print("Меню управления")
        print("==============================")
        print("1) ⚙️ Настройка бота (.env)")
        print("2) 🎁 Наборы подарков")
        print("3) 📲 Telegram-сессия")
        print("0) Выход")
        choice = input_int("Выберите пункт (0–3): ", min_val=0, max_val=3, allow_cancel=True)
        if choice in (None, 0):
            print("Выход.")
            return
        if choice == 1:
            menu_bot_settings()
        elif choice == 2:
            menu_sets()
        elif choice == 3:
            menu_sessions()

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print("Выход (Ctrl+C).")
