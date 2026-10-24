#!/usr/bin/env bash
# Для сервера, где УЖЕ работают другие проекты (Docker, файрвол и пользователи настроены).
# Создаёт отдельного пользователя goulash и папку /srv/goulash. Запускать один раз от root:
#   bash add-project-user.sh "<публичный SSH-ключ для выкладки>"
# Ничего другого на сервере не трогает: файрвол, Docker и чужие проекты остаются как есть.
set -euo pipefail

PUBKEY="${1:-}"
APP_DIR="${APP_DIR:-/srv/goulash}"

if [ "$(id -u)" -ne 0 ]; then echo "Запустите от root." >&2; exit 1; fi
if [ -z "$PUBKEY" ]; then echo "Укажите публичный SSH-ключ первым аргументом." >&2; exit 1; fi
if ! getent group docker >/dev/null; then echo "На сервере нет группы docker: Docker не установлен?" >&2; exit 1; fi

id goulash >/dev/null 2>&1 || adduser --disabled-password --gecos "" goulash
usermod -aG docker goulash
install -d -m 700 -o goulash -g goulash /home/goulash/.ssh
grep -qxF "$PUBKEY" /home/goulash/.ssh/authorized_keys 2>/dev/null || echo "$PUBKEY" >> /home/goulash/.ssh/authorized_keys
chown goulash:goulash /home/goulash/.ssh/authorized_keys
chmod 600 /home/goulash/.ssh/authorized_keys
install -d -m 750 -o goulash -g goulash "$APP_DIR"

echo "Готово: пользователь goulash, папка $APP_DIR."
id goulash
