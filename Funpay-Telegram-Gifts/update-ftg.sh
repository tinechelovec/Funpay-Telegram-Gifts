#!/usr/bin/env bash
set -euo pipefail

SVC_BASE="FunpayTelegramGifts"
APP_ROOT="/opt/funpay-telegram-gifts"
REPO_URL_DEFAULT="https://github.com/tinechelovec/Funpay-Telegram-Gifts.git"

INSTANCE=""
REPO_URL="$REPO_URL_DEFAULT"
BRANCH=""
FORCE=0
NO_RESTART=0
FIRST_START_ARGS=()

usage() {
  echo "Использование:"
  echo "  sudo bash update-ftg.sh --instance ftg_myname"
  echo
  echo "Опции:"
  echo "  --branch <name>        (переключить/закрепить ветку)"
  echo "  --repo <url>           (если нужно сменить origin)"
  echo "  --force                (сбросить локальные изменения в repo)"
  echo "  --no-restart           (не перезапускать systemd сервис)"
  echo "  --set KEY=VALUE        (прокинуть в first_start.py, можно много раз)"
  echo "  --non-interactive      (прокинуть в first_start.py)"
  echo "  --force-env            (прокинуть в first_start.py, осторожно: может перезаписать .env)"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --instance) INSTANCE="${2:-}"; shift 2 ;;
    --repo) REPO_URL="${2:-}"; shift 2 ;;
    --branch) BRANCH="${2:-}"; shift 2 ;;
    --force) FORCE=1; shift 1 ;;
    --no-restart) NO_RESTART=1; shift 1 ;;
    --set|--force-env|--non-interactive)
      FIRST_START_ARGS+=("$1")
      if [[ "$1" == "--set" ]]; then
        FIRST_START_ARGS+=("${2:-}"); shift 2
      else
        shift 1
      fi
      ;;
    --help|-h) usage ;;
    *) echo "Неизвестный аргумент: $1"; exit 1 ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "Запусти через sudo:"
  echo "  sudo bash update-ftg.sh --instance ftg_имя"
  exit 1
fi

if [[ -z "$INSTANCE" ]]; then
  echo "ОШИБКА: нужен --instance"
  exit 2
fi

INST_DIR="${APP_ROOT}/instances/${INSTANCE}"
REPO_DIR="${INST_DIR}/repo"
VENV_DIR="${INST_DIR}/venv"

if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "ОШИБКА: repo не найден: $REPO_DIR"
  echo "Сначала установи инстанс install-ftg.sh"
  exit 3
fi

CODE_DIR="$(find "${REPO_DIR}" -maxdepth 6 -type f -name "first_start.py" -print -quit | xargs -r dirname)"
if [[ -z "${CODE_DIR}" ]]; then
  echo "ОШИБКА: не найден first_start.py внутри ${REPO_DIR}"
  exit 4
fi

BOT_FILE="$(find "${CODE_DIR}" -maxdepth 1 -type f -name "funpay_gift_bot.py" -print -quit || true)"
if [[ -z "${BOT_FILE}" ]]; then
  echo "ОШИБКА: не найден funpay_gift_bot.py рядом с first_start.py (${CODE_DIR})"
  exit 5
fi

BOT_USER="$(stat -c %U "${INST_DIR}" 2>/dev/null || echo root)"
if [[ "$BOT_USER" == "root" ]]; then
  BOT_USER="$(stat -c %U "${CODE_DIR}" 2>/dev/null || echo root)"
fi

echo "============================================================"
echo "UPDATE: ${SVC_BASE}@${INSTANCE}"
echo "User:   ${BOT_USER}"
echo "Repo:   ${REPO_DIR}"
echo "Code:   ${CODE_DIR}"
echo "Bot:    ${BOT_FILE}"
echo "============================================================"

TS="$(date +%Y%m%d-%H%M%S)"
BK_DIR="${INST_DIR}/backups/update-${TS}"
mkdir -p "${BK_DIR}"

backup_if_exists() {
  local p="$1"
  if [[ -e "$p" ]]; then
    cp -a "$p" "${BK_DIR}/"
  fi
}

echo "[1/7] Останавливаю сервис (если запущен)..."
systemctl stop "${SVC_BASE}@${INSTANCE}" >/dev/null 2>&1 || true

echo "[2/7] Делаю бэкап настроек..."
backup_if_exists "${CODE_DIR}/.env"
backup_if_exists "${CODE_DIR}/sessions"
backup_if_exists "${CODE_DIR}/manual_orders.json"
backup_if_exists "${CODE_DIR}/log.txt"
echo "Бэкап: ${BK_DIR}"

echo "[3/7] Обновляю git репозиторий..."
git -C "${REPO_DIR}" remote set-url origin "${REPO_URL}"
git -C "${REPO_DIR}" fetch --all --prune

if [[ "${FORCE}" -eq 1 ]]; then
  echo "FORCE=1 -> сбрасываю локальные изменения"
  git -C "${REPO_DIR}" reset --hard
  git -C "${REPO_DIR}" clean -fd
else
  if [[ -n "$(git -C "${REPO_DIR}" status --porcelain)" ]]; then
    echo "ОШИБКА: в repo есть локальные изменения."
    echo "Либо закоммить/убери их, либо запусти с --force"
    exit 6
  fi
fi

if [[ -n "${BRANCH}" ]]; then
  git -C "${REPO_DIR}" checkout "${BRANCH}"
fi

git -C "${REPO_DIR}" pull --rebase

CODE_DIR="$(find "${REPO_DIR}" -maxdepth 6 -type f -name "first_start.py" -print -quit | xargs -r dirname)"
if [[ -z "${CODE_DIR}" ]]; then
  echo "ОШИБКА: после обновления пропал first_start.py"
  exit 7
fi
BOT_FILE="$(find "${CODE_DIR}" -maxdepth 1 -type f -name "funpay_gift_bot.py" -print -quit || true)"
if [[ -z "${BOT_FILE}" ]]; then
  echo "ОШИБКА: после обновления пропал funpay_gift_bot.py"
  exit 8
fi

echo "[4/7] Сохраняю старые настройки обратно (если вдруг затёрлись)..."
restore_if_missing() {
  local name="$1"
  local dst="$2"
  local src="${BK_DIR}/${name}"
  if [[ ! -e "$dst" && -e "$src" ]]; then
    cp -a "$src" "$dst"
  fi
}
restore_if_missing ".env" "${CODE_DIR}/.env"
restore_if_missing "manual_orders.json" "${CODE_DIR}/manual_orders.json"
if [[ -d "${BK_DIR}/sessions" && ! -d "${CODE_DIR}/sessions" ]]; then
  cp -a "${BK_DIR}/sessions" "${CODE_DIR}/sessions"
fi

echo "[5/7] Добавляю новые env-переменные дефолтами (если есть .env.example)..."
ENV_EXAMPLE="$(find "${REPO_DIR}" -maxdepth 6 -type f -name ".env.example" -print -quit || true)"
ENV_FILE="${CODE_DIR}/.env"

if [[ -n "${ENV_EXAMPLE}" ]]; then
  if [[ ! -f "${ENV_FILE}" ]]; then
    cp -a "${ENV_EXAMPLE}" "${ENV_FILE}"
  else
    while IFS= read -r line; do
      [[ -z "${line// /}" ]] && continue
      [[ "${line}" =~ ^[[:space:]]*# ]] && continue
      if [[ "${line}" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)= ]]; then
        key="${BASH_REMATCH[1]}"
        if ! grep -qE "^[[:space:]]*${key}=" "${ENV_FILE}"; then
          echo "${line}" >> "${ENV_FILE}"
        fi
      fi
    done < "${ENV_EXAMPLE}"
  fi
fi

chown -R "${BOT_USER}:${BOT_USER}" "${INST_DIR}" || true
mkdir -p "${CODE_DIR}/sessions"
chmod 700 "${CODE_DIR}/sessions" || true

echo "[6/7] Обновляю зависимости venv..."
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "venv не найден -> создаю заново"
  python3 -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/python" -m pip install -U pip wheel setuptools

if [[ -f "${CODE_DIR}/requirements.txt" ]]; then
  "${VENV_DIR}/bin/pip" install -r "${CODE_DIR}/requirements.txt"
fi

"${VENV_DIR}/bin/pip" uninstall -y pyrogram >/dev/null 2>&1 || true
"${VENV_DIR}/bin/pip" install -U pyrofork tgcrypto

echo "[6.5/7] Прогоняю first_start.py (чтобы новые настройки добавились по дефолту)..."
EXTRA_ARGS=("${FIRST_START_ARGS[@]}")
if [[ ! " ${EXTRA_ARGS[*]} " =~ " --non-interactive " ]]; then
  EXTRA_ARGS+=("--non-interactive")
fi

sudo -u "${BOT_USER}" -H bash -lc "cd '${CODE_DIR}' && '${VENV_DIR}/bin/python' first_start.py ${EXTRA_ARGS[*]}"

echo "[7/7] Перезапуск сервиса..."
systemctl daemon-reload || true
if [[ "${NO_RESTART}" -eq 0 ]]; then
  systemctl restart "${SVC_BASE}@${INSTANCE}" || systemctl start "${SVC_BASE}@${INSTANCE}" || true
else
  echo "NO_RESTART=1 -> сервис не трогаю"
fi

echo
echo "############################################################"
echo "Обновление завершено: ${SVC_BASE}@${INSTANCE}"
echo "Логи:"
echo "  sudo journalctl -u ${SVC_BASE}@${INSTANCE} -n 200 --no-pager"
echo "  sudo journalctl -u ${SVC_BASE}@${INSTANCE} -f"
echo "Бэкап настроек: ${BK_DIR}"
echo "############################################################"
