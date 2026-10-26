"""Модели данных. Остальные таблицы из ТЗ (раздел 10) добавляются по мере этапов."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CatalogSync(Base):
    """Журнал синхронизаций каталога (старт приложения или кнопка «Обновить»)."""

    __tablename__ = "catalog_syncs"

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(16))  # startup | manual
    status: Mapped[str] = mapped_column(String(16))  # running | success | failed | rejected
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    partial: Mapped[bool] = mapped_column(Boolean, default=False)  # обход городов был неполным
    stats: Mapped[dict | None] = mapped_column(JSONB)  # добавлено / архивировано / изменено
    error: Mapped[str | None] = mapped_column(Text)


class Item(Base):
    """Уникальная позиция меню «Сушкофа» (по id с сайта, без привязки к городу). Сырой слой каталога."""

    __tablename__ = "items"
    __table_args__ = (Index("ix_items_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(unique=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    weight: Mapped[str | None] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(Text, default="")
    sections: Mapped[list] = mapped_column(JSONB, default=list)  # названия разделов меню
    content_hash: Mapped[str] = mapped_column(String(64))
    ingredients_hash: Mapped[str | None] = mapped_column(String(64))  # content_hash на момент последнего разбора на категории
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | archived (мягкое удаление)
    first_seen_sync_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_syncs.id"))
    last_seen_sync_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_syncs.id"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Category(Base):
    """Категория для поиска поставщиков: ингредиент (лист) или группа (родитель). Слой «категории» каталога."""

    __tablename__ = "categories"
    __table_args__ = (Index("ix_categories_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    normalized_name: Mapped[str] = mapped_column(String(120), unique=True)  # ключ уникальности, см. taxonomy.normalize_key
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    is_group: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[str] = mapped_column(String(16), default="ingredient")  # ingredient | packaging
    synonyms: Mapped[list] = mapped_column(JSONB, default=list)
    origin: Mapped[str] = mapped_column(String(16), default="derived")  # derived | manual (ручные не архивируются)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | archived
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ItemCategory(Base):
    __tablename__ = "item_categories"

    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True)


class Run(Base):
    """Запуск поиска поставщиков агентом."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"))
    category_name: Mapped[str] = mapped_column(String(120))
    location_kind: Mapped[str | None] = mapped_column(String(16))  # city | region | None (вся Россия)
    location_key: Mapped[str | None] = mapped_column(String(200))
    location_label: Mapped[str | None] = mapped_column(String(200))
    include_neighbors: Mapped[bool] = mapped_column(Boolean, default=False)
    regions: Mapped[list] = mapped_column(JSONB, default=list)  # [{id, name, role}]
    supplier_type: Mapped[str | None] = mapped_column(String(32))  # фильтр старых запусков; в форме его больше нет
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | failed
    stop_reason: Mapped[str | None] = mapped_column(String(32))
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunEvent(Base):
    """Шаги агента для ленты в интерфейсе; хранятся, чтобы лента восстанавливалась после обновления страницы."""

    __tablename__ = "run_events"
    __table_args__ = (Index("ix_run_events_run_seq", "run_id", "seq", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column()
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Supplier(Base):
    """Поставщик, уникальный по домену сайта."""

    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True)
    website: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(300))
    inn: Mapped[str | None] = mapped_column(String(12), index=True)
    research: Mapped[dict | None] = mapped_column(JSONB)  # какие страницы сайта изучены и чего на них не нашлось
    note: Mapped[str | None] = mapped_column(Text)  # заметка закупщика (в ТЗ: «комментарии или заметки по поставщику»)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SupplierField(Base):
    """Одно значение поля поставщика с источником и цитатой со страницы."""

    __tablename__ = "supplier_fields"
    __table_args__ = (Index("ix_supplier_fields_supplier", "supplier_id", "field"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id", ondelete="CASCADE"))
    field: Mapped[str] = mapped_column(String(32))
    value: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    quote: Mapped[str] = mapped_column(Text)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))  # последний запуск, подтвердивший поле
    # запуск, в котором поле впервые найдено: результаты прошлого запуска не меняются от того, что нашли позже
    first_run_id: Mapped[int | None] = mapped_column(index=True)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RunSupplier(Base):
    """Какие поставщики найдены в запуске. Балл, ранг и объяснение заполняются на этапе ранжирования."""

    __tablename__ = "run_suppliers"

    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id", ondelete="CASCADE"), primary_key=True)
    found_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    score: Mapped[float | None] = mapped_column()
    rank: Mapped[int | None] = mapped_column()
    location_tier: Mapped[str | None] = mapped_column(String(16))
    explanation: Mapped[str | None] = mapped_column(Text)


class FetchedPage(Base):
    """Кэш загруженных страниц, чтобы повторные запуски не ходили в сеть."""

    __tablename__ = "fetched_pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text)
    links: Mapped[list] = mapped_column(JSONB, default=list)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
