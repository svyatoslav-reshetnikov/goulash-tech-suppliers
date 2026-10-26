"""Restores the visibility of fields in old searches (dry run by default).

Migration 0007 gave every old field `first_run_id` = the LAST run that confirmed it ("the earlier value cannot be known"). A search that
was finished long ago and was later confirmed by newer searches then lost those fields on screen: its result only shows fields with
`first_run_id <= run_id`.

The step log of every search (`run_events`) records which fields of which supplier each run saved (`supplier_saved`) and which pages it read
(`fetch`). A field cannot have been found before the run that first read its source page, nor before the first run that saved a field of that
name for that supplier. So this script sets `first_run_id` to the later of those two runs, when that is earlier than the stored value. It only
ever LOWERS first_run_id, so old searches show more and nothing is deleted or rewritten. A value found later on a page that an earlier run had
also read may still appear in the older search: the log does not allow to tell more precisely. Before changing anything it copies the
affected rows to the table `supplier_fields_first_run_backup`, from which the change is reversible.

    python -m scripts.restore_first_run            # shows what would change
    python -m scripts.restore_first_run --apply    # backs up and changes
"""

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

PROPOSED = """
with saved as (
    select s.id as supplier_id, e.run_id, jsonb_array_elements_text(e.payload::jsonb -> 'fields') as field
    from run_events e join suppliers s on s.domain = e.payload ->> 'domain'
    where e.type = 'supplier_saved'
), earliest as (
    select supplier_id, field, min(run_id) as run_id from saved group by supplier_id, field
), pages as (
    select e.payload ->> 'url' as url, min(e.run_id) as run_id from run_events e where e.type = 'fetch' group by 1
), candidates as (
    select f.id, s.domain, f.field, left(f.value, 40) as value, f.first_run_id as current,
           greatest(e.run_id, coalesce(p.run_id, e.run_id)) as proposed
    from supplier_fields f
    join suppliers s on s.id = f.supplier_id
    join earliest e on e.supplier_id = f.supplier_id and e.field = f.field
    left join pages p on p.url = f.source_url
    where f.first_run_id is not null
)
select * from candidates where proposed < current order by domain, field, id
"""

VISIBLE = """
select rs.run_id, s.domain, count(f.id) filter (where f.first_run_id is null or f.first_run_id <= rs.run_id) as visible
from run_suppliers rs join suppliers s on s.id = rs.supplier_id
left join supplier_fields f on f.supplier_id = s.id
group by rs.run_id, s.domain order by rs.run_id, s.domain
"""


async def plan(session: AsyncSession) -> list[dict]:
    return [dict(row._mapping) for row in await session.execute(text(PROPOSED))]


async def visible_fields(session: AsyncSession) -> dict[tuple[int, str], int]:
    return {(r.run_id, r.domain): r.visible for r in await session.execute(text(VISIBLE))}


async def apply(session: AsyncSession, commit: bool = True) -> int:
    """Backs up the rows that change, then lowers first_run_id. Returns the number of changed rows; safe to run again."""
    changes = await plan(session)
    if not changes:
        return 0
    await session.execute(text(
        "create table if not exists supplier_fields_first_run_backup "
        "(field_id integer, old_first_run_id integer, new_first_run_id integer, backed_up_at timestamptz default now())"
    ))
    for change in changes:
        await session.execute(
            text("insert into supplier_fields_first_run_backup (field_id, old_first_run_id, new_first_run_id) values (:id, :old, :new)"),
            {"id": change["id"], "old": change["current"], "new": change["proposed"]},
        )
        await session.execute(text("update supplier_fields set first_run_id = :new where id = :id"), {"id": change["id"], "new": change["proposed"]})
    if commit:
        await session.commit()
    return len(changes)


def report(changes: list[dict], before: dict[tuple[int, str], int], after: dict[tuple[int, str], int]) -> str:
    lines = [f"Fields whose first_run_id would be lowered: {len(changes)}"]
    for c in changes:
        lines.append(f"  {c['domain']:<20} {c['field']:<15} run {c['current']} -> {c['proposed']}   {c['value']}")
    lines.append("Visible fields per search and supplier (before -> after):")
    for key in sorted(before):
        if before[key] != after[key]:
            lines.append(f"  search {key[0]}  {key[1]:<20} {before[key]} -> {after[key]}")
    return "\n".join(lines)


async def main(do_apply: bool) -> None:
    engine = create_async_engine(get_settings().db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        before = await visible_fields(session)
        changes = await plan(session)
        if do_apply:
            print(f"Changed rows: {await apply(session)} (backup: supplier_fields_first_run_backup)")
            after = await visible_fields(session)
        else:
            # what the numbers would be: apply inside a transaction that is rolled back (the backup table is rolled back with it)
            await apply(session, commit=False)
            after = await visible_fields(session)
            await session.rollback()
    print(report(changes, before, after))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
