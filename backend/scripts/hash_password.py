"""Хеш пароля для AUTH_PASSWORD_HASH в .env. Запуск (нужен только Python 3, зависимости не требуются):

    python3 backend/scripts/hash_password.py 'ВАШ_ПАРОЛЬ'

Результат вставьте в .env в одинарных кавычках: AUTH_PASSWORD_HASH='pbkdf2_sha256$600000$...'
"""

import hashlib
import secrets
import sys

ITERATIONS = 600_000  # те же параметры, что в app/auth.py

if len(sys.argv) != 2 or not sys.argv[1]:
    sys.exit("Использование: python3 backend/scripts/hash_password.py 'ПАРОЛЬ'")
salt = secrets.token_hex(16)
digest = hashlib.pbkdf2_hmac("sha256", sys.argv[1].encode(), salt.encode(), ITERATIONS).hex()
print(f"pbkdf2_sha256${ITERATIONS}${salt}${digest}")
