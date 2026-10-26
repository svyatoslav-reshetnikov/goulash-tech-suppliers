"""Настройки приложения.

Значения читаются из переменных окружения, а при локальном запуске ещё и из файла
`.env` в корне репозитория. Шаблон: `.env.example`. В Docker Compose переменные
передаются через `env_file`, отдельного файла внутри образа нет.
"""

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class MissingSettingError(RuntimeError):
    """Не задана обязательная настройка или секрет."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    database_url: str | None = None
    postgres_user: str = "suppliers"
    postgres_password: SecretStr = SecretStr("change-me")
    postgres_db: str = "suppliers"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    yandex_cloud_folder_id: str | None = None

    # Вход по логину и паролю. Хеш пароля: `python3 backend/scripts/hash_password.py`. Хеш пустой: вход отключён
    auth_user: str | None = None
    auth_password_hash: SecretStr | None = None
    auth_session_days: int = 7

    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None

    gigachat_credentials: SecretStr | None = None
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str = "GigaChat-2-Max"
    gigachat_auth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    gigachat_base_url: str = "https://gigachat.devices.sberbank.ru/api/v1"
    # Сертификаты НУЦ Минцифры: путь к PEM-файлу (относительный путь считается от корня репозитория)
    gigachat_ca_bundle: str | None = None

    yandex_search_api_key: SecretStr | None = None  # если не задан, используется LLM_API_KEY (тот же ключ Yandex Cloud)
    dgis_api_key: SecretStr | None = None

    # Дополнительные корневые сертификаты (НУЦ Минцифры) для обхода сайтов; относительный путь — от корня репозитория
    extra_ca_bundle: str | None = "certs/russian_trusted_ca.pem"

    # Ограничения одного запуска агента (деньги и время). Приоритет — полнота данных по ТЗ, поэтому
    # лимит токенов высокий, а запусков в сутки немного: запуск платный.
    agent_max_turns: int = 24
    agent_max_seconds: int = 1800
    agent_max_total_tokens: int = 800_000  # главный предохранитель расхода: сайтов изучается столько, сколько нашлось кандидатов
    agent_target_suppliers: int = 20  # на стольких подтверждённых поставщиках поиск останавливается
    agent_max_fetches: int = 500  # загрузка страниц бесплатна; расход ограничен бюджетом токенов
    agent_auto_discovery: bool = True  # код сам перебирает регионы и формулировки и изучает всех найденных кандидатов
    agent_max_runs_per_day: int = 10

    crawl_delay_seconds: float = 1.0
    crawl_user_agent: str = "goulash-suppliers-test/0.1 (test assignment)"

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        password = quote_plus(self.postgres_password.get_secret_value())
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def search_api_key(self) -> SecretStr | None:
        """Ключ Yandex Search API: отдельный или общий с моделью (проверено: ключ LLM подходит для поиска)."""
        return self.yandex_search_api_key if _is_filled(self.yandex_search_api_key) else self.llm_api_key

    def require(self, *names: str) -> None:
        """Проверяет, что настройки заданы и не пусты; иначе понятная ошибка.

        Вызывается там, где настройка реально нужна, чтобы приложение стартовало
        и без ключей (например, для работы с каталогом).
        """
        missing = [name for name in names if not _is_filled(getattr(self, name.lower()))]
        if missing:
            raise MissingSettingError(
                f"Не задано: {', '.join(n.upper() for n in missing)}. "
                f"Добавьте значения в файл .env (шаблон: .env.example)."
            )


def _is_filled(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return str(value).strip() != ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
