#!/usr/bin/env bash
# Разовая подготовка чистого сервера (Ubuntu/Debian). Запускать от root:
#   sudo bash setup-server.sh "<публичный SSH-ключ для выкладки>"
# Делает: ставит Docker и rsync, создаёт пользователя deploy, папку /opt/goulash и открывает порты 22, 80, 443.
# Скрипт не проверялся на вашем сервере. Просмотрите его перед запуском.
set -euo pipefail

PUBKEY="${1:-}"
APP_DIR="${APP_DIR:-/opt/goulash}"

if [ "$(id -u)" -ne 0 ]; then echo "Запустите от root (sudo)." >&2; exit 1; fi
if [ -z "$PUBKEY" ]; then echo "Укажите публичный SSH-ключ первым аргументом." >&2; exit 1; fi

apt-get update -y
apt-get install -y ca-certificates curl rsync ufw
if ! command -v docker >/dev/null; then curl -fsSL https://get.docker.com | sh; fi

id deploy >/dev/null 2>&1 || adduser --disabled-password --gecos "" deploy
usermod -aG docker deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
grep -qxF "$PUBKEY" /home/deploy/.ssh/authorized_keys 2>/dev/null || echo "$PUBKEY" >> /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys && chmod 600 /home/deploy/.ssh/authorized_keys

install -d -o deploy -g deploy "$APP_DIR"

ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo
echo "Готово. Теперь положите файл настроек: $APP_DIR/.env (владелец deploy, права 600)."
