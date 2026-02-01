set -euo pipefail

SVC_BASE="FunpayTelegramGifts"
APP_ROOT="/opt/funpay-telegram-gifts"
REPO_URL_DEFAULT="https://github.com/tinechelovec/Funpay-Telegram-Gifts.git"

INSTANCE=""
REPO_URL="$REPO_URL_DEFAULT"
BRANCH=""
FIRST_START_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --instance)
      INSTANCE="${2:-}"; shift 2 ;;
    --repo)
      REPO_URL="${2:-}"; shift 2 ;;
    --branch)
      BRANCH="${2:-}"; shift 2 ;;
    --set|--force-env|--non-interactive)
      FIRST_START_ARGS+=("$1")
      if [[ "$1" == "--set" ]]; then
        FIRST_START_ARGS+=("${2:-}"); shift 2
      else
        shift 1
      fi
      ;;
    --help|-h)
      echo "Использование:"
      echo "  sudo bash install-ftg.sh --instance ftg_myname"
      echo "Опционально:"
      echo "  --repo <url> --branch <name>"
      echo "  --set KEY=VALUE (можно много раз), --force-env, --non-interactive (уйдут в first_start.py)"
      exit 0
      ;;
    *)
      echo "Неизвестный аргумент: $1"
      exit 1
      ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "Запусти через sudo:"
  echo "  sudo bash install-ftg.sh --instance ftg_имя"
  exit 1
fi

BOT_USER="${SUDO_USER:-root}"
if [[ -z "$INSTANCE" ]]; then
  echo -n "Введите имя инстанса (пример: ftg_kseyhatemee): "
  read -r INSTANCE
fi
if [[ -z "$INSTANCE" ]]; then
  echo "Пустое имя инстанса нельзя."
  exit 1
fi

INST_DIR="${APP_ROOT}/instances/${INSTANCE}"
REPO_DIR="${INST_DIR}/repo"
VENV_DIR="${INST_DIR}/venv"

echo "============================================================"
echo "Установка: ${SVC_BASE}@${INSTANCE}"
echo "Пользователь: ${BOT_USER}"
echo "Папка: ${INST_DIR}"
echo "============================================================"

echo "[1/6] Ставлю системные пакеты..."
apt-get update -y
apt-get install -y \
  git curl ca-certificates \
  python3 python3-venv python3-pip \
  build-essential python3-dev \
  libssl-dev libffi-dev

pick_python() {
  for c in python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      "$c" - <<'PY' >/dev/null 2>&1 && { echo "$c"; return 0; }
import sys
sys.exit(0 if sys.version_info >= (3,11) else 1)
PY
    fi
  done
  return 1
}
PYBIN="$(pick_python || true)"
if [[ -z "${PYBIN}" ]]; then
  echo "ОШИБКА: нужен Python 3.11+ (Debian 12 / Ubuntu 24.04 подходят из коробки)."
  exit 2
fi
echo "Использую Python: ${PYBIN}"

echo "[2/6] Клонирую/обновляю репозиторий..."
mkdir -p "${INST_DIR}"
if [[ -d "${REPO_DIR}/.git" ]]; then
  git -C "${REPO_DIR}" fetch --all
  if [[ -n "${BRANCH}" ]]; then
    git -C "${REPO_DIR}" checkout "${BRANCH}"
  fi
  git -C "${REPO_DIR}" pull --rebase
else
  if [[ -n "${BRANCH}" ]]; then
    git clone -b "${BRANCH}" "${REPO_URL}" "${REPO_DIR}"
  else
    git clone "${REPO_URL}" "${REPO_DIR}"
  fi
fi

CODE_DIR="$(find "${REPO_DIR}" -maxdepth 6 -type f -name "first_start.py" -print -quit | xargs -r dirname)"
if [[ -z "${CODE_DIR}" ]]; then
  echo "ОШИБКА: не найден first_start.py внутри ${REPO_DIR}"
  exit 3
fi
BOT_FILE="$(find "${CODE_DIR}" -maxdepth 1 -type f -name "funpay_gift_bot.py" -print -quit || true)"
if [[ -z "${BOT_FILE}" ]]; then
  echo "ОШИБКА: не найден funpay_gift_bot.py рядом с first_start.py (${CODE_DIR})"
  exit 4
fi

SETTINGS_FILE="$(find "${CODE_DIR}" -maxdepth 1 -type f -name "settings.py" -print -quit || true)"

echo "Код: ${CODE_DIR}"
echo "Бот: ${BOT_FILE}"
if [[ -n "${SETTINGS_FILE}" ]]; then
  echo "Настройки: ${SETTINGS_FILE}"
fi

echo "[3/6] Создаю venv и ставлю зависимости..."
"${PYBIN}" -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install -U pip wheel setuptools

if [[ -f "${CODE_DIR}/requirements.txt" ]]; then
  "${VENV_DIR}/bin/pip" install -r "${CODE_DIR}/requirements.txt"
fi

"${VENV_DIR}/bin/pip" uninstall -y pyrogram >/dev/null 2>&1 || true
"${VENV_DIR}/bin/pip" install -U pyrofork tgcrypto

echo "[4/6] Права на папки (sessions/.env)..."
mkdir -p "${CODE_DIR}/sessions"
chown -R "${BOT_USER}:${BOT_USER}" "${INST_DIR}"
chmod 700 "${CODE_DIR}/sessions" || true

echo "[5/6] Запускаю первичную настройку (first_start.py)..."
sudo -u "${BOT_USER}" -H bash -lc "cd '${CODE_DIR}' && '${VENV_DIR}/bin/python' first_start.py ${FIRST_START_ARGS[*]}"

echo "[6/6] Создаю systemd template unit..."
UNIT_PATH="/etc/systemd/system/${SVC_BASE}@.service"

if [[ ! -f "${UNIT_PATH}" ]]; then
  cat > "${UNIT_PATH}" <<EOF
[Unit]
Description=Funpay Telegram Gifts bot (%i)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${BOT_USER}
WorkingDirectory=${APP_ROOT}/instances/%i/repo/$(python3 - <<PY
import os
p="${CODE_DIR}"
print(os.path.relpath(p, "${REPO_DIR}"))
PY
)
EnvironmentFile=${APP_ROOT}/instances/%i/repo/$(python3 - <<PY
import os
p="${CODE_DIR}"
print(os.path.relpath(p, "${REPO_DIR}"))
PY
)/.env
ExecStart=${APP_ROOT}/instances/%i/venv/bin/python ${APP_ROOT}/instances/%i/repo/$(python3 - <<PY
import os
print(os.path.relpath("${BOT_FILE}", "${REPO_DIR}"))
PY
)
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
fi

systemctl daemon-reload
systemctl start "${SVC_BASE}@${INSTANCE}" || true

echo
echo "############################################################"
echo "Готово. Инстанс: ${SVC_BASE}@${INSTANCE}"
echo
echo "Для остановки используй:"
echo "  sudo systemctl stop ${SVC_BASE}@${INSTANCE}"
echo
echo "Для запуска используй:"
echo "  sudo systemctl start ${SVC_BASE}@${INSTANCE}"
echo
echo "Для перезапуска используй:"
echo "  sudo systemctl restart ${SVC_BASE}@${INSTANCE}"
echo
echo "Для просмотра логов:"
echo "  sudo systemctl status ${SVC_BASE}@${INSTANCE} -n 100"
echo "  sudo journalctl -u ${SVC_BASE}@${INSTANCE} -n 200 --no-pager"
echo "  sudo journalctl -u ${SVC_BASE}@${INSTANCE} -f"
echo
echo "Для добавления в автозагрузку:"
echo "  sudo systemctl enable ${SVC_BASE}@${INSTANCE}"
echo

if [[ -n "${SETTINGS_FILE}" ]]; then
  echo "Чтобы зайти в настройки бота (settings.py):"
  echo "  sudo -u ${BOT_USER} -H bash -lc \"cd '${CODE_DIR}' && '${VENV_DIR}/bin/python' settings.py\""
  echo
else
  echo "settings.py не найден рядом с кодом (${CODE_DIR})."
  echo
fi

echo "Для запуска/повторного запуска первичной настройки (first_start.py):"
echo "  sudo -u ${BOT_USER} -H bash -lc \"cd '${CODE_DIR}' && '${VENV_DIR}/bin/python' first_start.py\""
echo "  (можно добавить опции: --set KEY=VALUE, --force-env, --non-interactive)"
echo
echo "Для ручного запуска бота (без systemd, для теста):"
echo "  sudo -u ${BOT_USER} -H bash -lc \"cd '${CODE_DIR}' && '${VENV_DIR}/bin/python' '${BOT_FILE}'\""
echo
echo "❗Перед enable убедись, что бот работает корректно."
echo "############################################################"
