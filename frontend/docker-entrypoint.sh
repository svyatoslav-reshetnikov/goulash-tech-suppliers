#!/bin/sh
# Включает вход по логину и паролю, если в .env задан BASIC_AUTH_HASH (хеш bcrypt, см. README).
set -e
mkdir -p /etc/caddy/auth
rm -f /etc/caddy/auth/basic.caddy
if [ -n "$BASIC_AUTH_HASH" ]; then
	printf 'basic_auth {\n\t%s %s\n}\n' "${BASIC_AUTH_USER:-admin}" "$BASIC_AUTH_HASH" > /etc/caddy/auth/basic.caddy
	echo "Вход по паролю включён (пользователь: ${BASIC_AUTH_USER:-admin})"
else
	echo "ВНИМАНИЕ: BASIC_AUTH_HASH не задан, вход без пароля"
fi
exec "$@"
