"""Сравнение скачанного каталога с базой: что добавить, обновить, вернуть из архива и заархивировать."""

from dataclasses import dataclass, field

from app.catalog.eda1 import MenuItem

MIN_ACTIVE_FOR_GUARD = 20
MIN_SHARE = 0.5


@dataclass(frozen=True)
class Existing:
    content_hash: str
    status: str  # active | archived


@dataclass
class Plan:
    add: list[MenuItem] = field(default_factory=list)
    update: list[MenuItem] = field(default_factory=list)
    restore: list[MenuItem] = field(default_factory=list)  # были в архиве, снова есть на сайте
    archive: list[int] = field(default_factory=list)  # source_id
    unchanged: list[int] = field(default_factory=list)


def plan_changes(existing: dict[int, Existing], fetched: dict[int, MenuItem], complete: bool) -> Plan:
    """Архивирует пропавшие позиции только при полном обходе всех городов (complete=True).

    При неполном обходе позиция, которая есть лишь в недоступном городе, ошибочно выглядела бы пропавшей.
    """
    plan = Plan()
    for source_id, item in fetched.items():
        known = existing.get(source_id)
        if known is None:
            plan.add.append(item)
        elif known.status == "archived":
            plan.restore.append(item)
        elif known.content_hash != item.content_hash:
            plan.update.append(item)
        else:
            plan.unchanged.append(source_id)
    if complete:
        plan.archive = [sid for sid, known in existing.items() if known.status == "active" and sid not in fetched]
    return plan


def check_sane(active_before: int, fetched_count: int) -> str | None:
    """Причина отказа применять результат, если он подозрительно мал (например, изменилась вёрстка сайта)."""
    if fetched_count == 0:
        return "с сайта не получено ни одной позиции"
    if active_before >= MIN_ACTIVE_FOR_GUARD and fetched_count < active_before * MIN_SHARE:
        return (
            f"получено {fetched_count} позиций при {active_before} в базе: меньше половины, "
            "данные в базе не изменены"
        )
    return None
