"""The repair of first_run_id: only lowers it, only where the step log proves an earlier run, keeps a backup."""

import pytest
from sqlalchemy import text

from app.db.models import Run, RunEvent, RunSupplier, Supplier, SupplierField
from scripts.restore_first_run import apply, plan, visible_fields
from tests.test_runs import needs_db, with_db


async def seed(factory):
    """Supplier S found by runs 1, 2 and 3. Run 1 saved phone and email, run 3 saved moq. Fields carry the legacy "last confirming run"."""
    async with factory() as session:
        await session.execute(text("drop table if exists supplier_fields_first_run_backup"))  # it is not in the metadata, so it survives between tests
        runs = [Run(category_name=f"Товар {i}", status="done") for i in (1, 2, 3)]
        session.add_all(runs)
        supplier = Supplier(domain="s.test", website="https://s.test/", name="S")
        other = Supplier(domain="o.test", website="https://o.test/", name="O")
        session.add_all([supplier, other])
        await session.flush()
        r1, r2, r3 = (r.id for r in runs)
        for run_id in (r1, r2, r3):
            session.add(RunSupplier(run_id=run_id, supplier_id=supplier.id))
        session.add(RunSupplier(run_id=r2, supplier_id=other.id))

        def field(sup, name, value, first):
            f = SupplierField(supplier_id=sup.id, field=name, value=value, source_url="https://s.test/", quote=value, run_id=r3, first_run_id=first)
            session.add(f)
            return f

        phone = field(supplier, "phone", "+7 (495) 111-22-33", r3)  # legacy value: really found by run 1
        email = field(supplier, "email", "a@s.test", r3)  # legacy value: really found by run 1
        moq = field(supplier, "moq", "от 5 000 ₽", r3)  # really found by run 3: must stay hidden from runs 1 and 2
        early = field(supplier, "name", "S", r1)  # already correct
        unlogged = field(supplier, "certificates", "ХАССП", r3)  # no event mentions it: left alone
        theirs = field(other, "phone", "+7 (495) 999-99-99", r2)  # a supplier of another run, already correct
        for run_id, sup, fields in ((r1, supplier, ["email", "name", "phone"]), (r2, supplier, ["phone"]), (r3, supplier, ["moq"]), (r2, other, ["phone"])):
            session.add(RunEvent(run_id=run_id, seq=1 + (sup.id == other.id), type="supplier_saved",
                                 payload={"domain": sup.domain, "fields": fields, "message": "Сохранён поставщик"}))
        await session.commit()
        return supplier.id, (r1, r2, r3), {"phone": phone.id, "email": email.id, "moq": moq.id, "name": early.id, "cert": unlogged.id, "other": theirs.id}


@needs_db
@with_db
async def test_plan_lowers_only_what_the_step_log_proves_and_never_raises(factory):
    _, (r1, r2, r3), ids = await seed(factory)
    async with factory() as session:
        proposed = {c["id"]: c["proposed"] for c in await plan(session)}
        assert proposed == {ids["phone"]: r1, ids["email"]: r1}  # moq (run 3), name, certificates and the other supplier stay as they are
        assert all(c["proposed"] < c["current"] for c in await plan(session))


@needs_db
@with_db
async def test_apply_restores_visibility_keeps_a_backup_and_is_safe_to_repeat(factory):
    _, (r1, r2, r3), ids = await seed(factory)
    async with factory() as session:
        before = await visible_fields(session)
        assert before[(r1, "s.test")] == 1  # only "name" is visible in run 1 before the repair
        assert await apply(session) == 2
        after = await visible_fields(session)
        assert after[(r1, "s.test")] == 3 and after[(r2, "s.test")] == 3 and after[(r3, "s.test")] == 5  # run 3 still sees everything
        assert after[(r2, "o.test")] == before[(r2, "o.test")]
        rows = (await session.execute(text("select field_id, old_first_run_id, new_first_run_id from supplier_fields_first_run_backup order by field_id"))).all()
        assert [(r.field_id, r.old_first_run_id, r.new_first_run_id) for r in rows] == sorted([(ids["phone"], r3, r1), (ids["email"], r3, r1)])
        assert await apply(session) == 0  # nothing left to change
        count = (await session.execute(text("select count(*) from supplier_fields"))).scalar()
        assert count == 6  # nothing was deleted
        moq_first = (await session.execute(text("select first_run_id from supplier_fields where id = :id"), {"id": ids["moq"]})).scalar()
        assert moq_first == r3  # a value that really appeared later stays hidden from the older searches


@needs_db
@with_db
async def test_dry_run_changes_nothing(factory):
    _, (r1, _r2, _r3), ids = await seed(factory)
    async with factory() as session:
        assert await apply(session, commit=False) == 2
        await session.rollback()
    async with factory() as session:
        value = (await session.execute(text("select first_run_id from supplier_fields where id = :id"), {"id": ids["phone"]})).scalar()
        assert value != r1
        exists = (await session.execute(text("select to_regclass('supplier_fields_first_run_backup')"))).scalar()
        assert exists is None  # the backup table of a dry run is rolled back as well
