"""People first seen on a Form 144 get EDGAR's conformed name, or are fixed later.

Real fixtures: the SNOW Form 144 (0001959173-26-006819) and a Form 4
(0001821737-26-000013) were both filed for Benoit Dageville, CIK 0001821737.
His notice happens to spell the name "Dageville Benoit" (last name first);
other filing agents write "First Last", which is what the free-text tests
simulate.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from smt.db.models import Entity
from smt.db.session import session_scope
from smt.ingest.entities import NAME_UNVERIFIED
from smt.ingest.form144 import load_form144
from smt.ingest.ownership import FilingMeta, load_ownership
from smt.sources.edgar.daily_index import parse_master_index
from smt.sources.edgar.names import conformed_name_from_submissions, verify_names
from smt.sources.forms.form144 import parse_form144
from smt.sources.forms.ownership import PARSER_VERSION, parse_ownership
from smt.sources.forms.types import Form144Notice
from smt.worker.scheduler import JobContext

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
NOTICE = "0001959173-26-006819"
FORM4 = "0001821737-26-000013"
CIK = "0001821737"


def meta(accession: str, form: str, filer_names: dict[str, str] | None = None) -> FilingMeta:
    return FilingMeta(
        accession=accession, form_type=form, filed_on=date(2026, 9, 9), accepted_at=None,
        url="https://www.sec.gov/", primary_doc_url=None, detected_via="manual",
        parser_version=PARSER_VERSION, live=False, filer_names=filer_names or {},
    )


def free_text_notice() -> Form144Notice:
    """The real notice, with the seller's name written "First Last" as some agents do."""
    notice = parse_form144((FIX / "form144" / f"{NOTICE}.xml").read_bytes())
    return dataclasses.replace(notice, person_name="Benoit Dageville")


async def seller() -> Entity:
    async with session_scope() as session:
        return (await session.execute(
            select(Entity).where(Entity.slug.like(f"%-{CIK.lstrip('0')}"))
        )).scalar_one()


# ---------------------------------------------------------------- pure functions


def test_daily_index_keeps_conformed_names() -> None:
    text = (
        "Description: Daily Index\n\nCIK|Company Name|Form Type|Date Filed|File Name\n"
        "--------------------------------------------------------------------------------\n"
        f"1821737|Dageville Benoit|144|20260909|edgar/data/1821737/{NOTICE}.txt\n"
        f"1640147|Snowflake Inc.|144|20260909|edgar/data/1640147/{NOTICE}.txt\n"
    )
    [filing] = parse_master_index(text, frozenset({"144"}))
    assert dict(filing.names) == {CIK: "Dageville Benoit", "0001640147": "Snowflake Inc."}


def test_submissions_name_parsing() -> None:
    assert conformed_name_from_submissions(b'{"cik": "1821737", "name": "Dageville Benoit"}') == "Dageville Benoit"
    assert conformed_name_from_submissions(b'{"name": "  "}') is None
    assert conformed_name_from_submissions(b"not json") is None
    assert conformed_name_from_submissions(b"[1, 2]") is None


# ---------------------------------------------------------------- loading


@pytest.mark.db
@pytest.mark.usefixtures("db")
async def test_conformed_name_wins_over_free_text() -> None:
    async with session_scope() as session:
        await load_form144(session, meta(NOTICE, "144", {CIK: "DAGEVILLE BENOIT"}), free_text_notice())
    person = await seller()
    assert person.name == "Benoit Dageville"
    assert NAME_UNVERIFIED not in person.tags


@pytest.mark.db
@pytest.mark.usefixtures("db")
async def test_free_text_name_is_flagged_then_fixed_by_a_form4() -> None:
    async with session_scope() as session:
        await load_form144(session, meta(NOTICE, "144"), free_text_notice())
    person = await seller()
    # Read as EDGAR's "LAST FIRST", "Benoit Dageville" comes out backwards: that's the bug being guarded.
    assert person.name == "Dageville Benoit"
    assert NAME_UNVERIFIED in person.tags
    old_slug = person.slug

    doc = parse_ownership((FIX / "ownership" / f"{FORM4}.xml").read_bytes())
    async with session_scope() as session:
        await load_ownership(session, meta(FORM4, "4"), doc)
    person = await seller()
    assert person.name == "Benoit Dageville"
    assert person.slug == f"benoit-dageville-{CIK.lstrip('0')}"
    assert NAME_UNVERIFIED not in person.tags

    # The old link still works: the API resolves the slug through its CIK suffix.
    from smt.api.app import create_app

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/people/{old_slug}")
    assert response.status_code == 200
    assert response.json()["entity"]["slug"] == person.slug


@pytest.mark.db
@pytest.mark.usefixtures("db")
async def test_verify_names_job_uses_the_persons_own_form4() -> None:
    doc = parse_ownership((FIX / "ownership" / f"{FORM4}.xml").read_bytes())
    async with session_scope() as session:
        await load_ownership(session, meta(FORM4, "4"), doc)
    # Simulate a person named backwards before this fix shipped (migration 0004 tags them).
    async with session_scope() as session:
        person = (await session.execute(select(Entity).where(Entity.slug.like(f"%-{CIK.lstrip('0')}")))).scalar_one()
        person.name = "Dageville Benoit"
        person.tags = [*person.tags, NAME_UNVERIFIED]

    ctx = JobContext(job_name="edgar.verify_names", run_id=0, cursor={})
    await verify_names(ctx)
    person = await seller()
    assert person.name == "Benoit Dageville"
    assert NAME_UNVERIFIED not in person.tags
    assert ctx.stats["refreshed_from_filings"] == 1  # fixed from the person's own Form 4 first
    assert ctx.docs_fetched == 0  # no network needed


@pytest.mark.db
@pytest.mark.usefixtures("db")
async def test_verify_names_with_nothing_to_do() -> None:
    ctx = JobContext(job_name="edgar.verify_names", run_id=0, cursor={})
    await verify_names(ctx)
    assert ctx.stats == {"refreshed_from_filings": 0, "unverified": 0}


@pytest.mark.db
@pytest.mark.usefixtures("db")
async def test_signatures_are_stored_backfilled_and_fix_a_double_surname(tmp_path: Path) -> None:
    """EDGAR's "PAZ MONTOYA ESTHER AMERICA" can't be split by rule; her own signature can."""
    import io
    import zipfile

    from sqlalchemy import text

    from smt.sources.edgar.names import backfill_signatures, refresh_from_own_filings

    doc = parse_ownership((FIX / "ownership" / f"{FORM4}.xml").read_bytes())
    owner = dataclasses.replace(doc.owners[0], name="Paz Montoya Esther America")
    doc = dataclasses.replace(doc, owners=(owner,), signatures=(("/s/ Jane Roe, attorney-in-fact", None),))
    async with session_scope() as session:
        await load_ownership(session, meta(FORM4, "4"), doc)
        stored = (await session.execute(text("SELECT flags->'signatures' FROM filings"))).scalar_one()
        # As if loaded before signatures were kept.
        await session.execute(text("UPDATE filings SET flags = flags - 'signatures'"))
    assert stored == ["/s/ Jane Roe, attorney-in-fact"]
    assert (await seller()).name == "Montoya Esther America Paz"  # the rule's best guess

    # A cached quarterly data set whose OWNER_SIGNATURE table has her own signature.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("OWNER_SIGNATURE.tsv", "ACCESSION_NUMBER\tOWNERSIGNATURENAME\tOWNERSIGNATUREDATE\n"
                                                f"{FORM4}\t/s/ Esther America Paz Montoya\t09-SEP-2026\n")
    (tmp_path / "2026q3_form345.zip").write_bytes(buffer.getvalue())
    counts = await backfill_signatures(cache_dir=tmp_path)
    assert counts["from_datasets"] == 1
    async with session_scope() as session:
        assert await refresh_from_own_filings(session) == 1
    person = await seller()
    assert (person.name, person.sort_name) == ("Esther America Paz Montoya", "Paz Montoya, Esther America")
    # Running again changes nothing.
    assert (await backfill_signatures(cache_dir=tmp_path))["from_datasets"] == 0


# ---------------------------------------------------------------- round 6: kinds and SEC-record names


@pytest.mark.db
@pytest.mark.usefixtures("db")
async def test_refresh_rereads_names_from_the_stored_sec_record() -> None:
    """62148: Metropolitan Life Insurance Co sold under a Form 144 and was named from its SEC
    submissions record under older rules, as the person "Life Insurance Co Metropolitan". The
    refresh reads the stored record again (no request to the SEC) and makes it a company."""
    from sqlalchemy import insert, text

    from smt.db.models import EntityId, RawDocument
    from smt.net.rawstore import RawStore
    from smt.sources.edgar.names import refresh_from_own_filings

    content = b'{"cik": "0000728618", "name": "METROPOLITAN LIFE INSURANCE CO/NY"}'
    sha, rel = RawStore().put(content)
    async with session_scope() as session:
        entity = Entity(kind="person", name="Life Insurance Co Metropolitan",
                        sort_name="Metropolitan, Life Insurance Co", slug="life-insurance-co-metropolitan-728618",
                        tags=["form144_filer", "name_from_sec_record"])
        session.add(entity)
        await session.flush()
        await session.execute(insert(EntityId).values(entity_id=entity.id, scheme="sec_cik", value="0000728618",
                                                      source="edgar"))
        await session.execute(insert(RawDocument).values(
            source="edgar", url="https://data.sec.gov/submissions/CIK0000728618.json", sha256=sha,
            http_status=200, byte_size=len(content), storage_path=rel, doc_role="submissions"))
    async with session_scope() as session:
        assert await refresh_from_own_filings(session) == 1
    async with session_scope() as session:
        row = (await session.execute(text(
            "SELECT kind, name, slug, tags FROM entities WHERE slug LIKE '%-728618'"))).one()
    assert (row.kind, row.name, row.slug) == ("company", "METROPOLITAN LIFE INSURANCE CO",
                                              "metropolitan-life-insurance-co-728618")
    assert "name_unverified" not in row.tags  # the SEC record is a trusted name: no lookup queued
    async with session_scope() as session:
        assert await refresh_from_own_filings(session) == 0  # idempotent


@pytest.mark.db
@pytest.mark.usefixtures("db")
async def test_an_unreadable_sec_record_leaves_that_name_and_the_refresh_goes_on() -> None:
    """A stored submissions file that is missing, truncated or not zstd at all leaves that filer's
    name as stored; MetLife beside them is still renamed. The refresh runs every 30 minutes for
    every filer, so one bad file must not stop it."""
    from compression import zstd

    from sqlalchemy import insert, text

    from smt.db.models import EntityId, RawDocument
    from smt.net.rawstore import RawStore
    from smt.sources.edgar.names import refresh_from_own_filings

    store = RawStore()
    good = b'{"cik": "0000728618", "name": "METROPOLITAN LIFE INSURANCE CO/NY"}'
    _, good_rel = store.put(good)
    whole = zstd.compress(b'{"cik": "0000000002", "name": "SECOND FILER LLC"}')
    files = {"0000728618": good_rel, "0000000001": "zz/00/not-zstd.zst", "0000000002": "zz/00/truncated.zst",
             "0000000003": "zz/00/missing.zst"}
    (store.root / "zz" / "00").mkdir(parents=True, exist_ok=True)
    (store.root / "zz/00/not-zstd.zst").write_bytes(b'{"name": "PLAIN JSON, NEVER COMPRESSED"}')
    (store.root / "zz/00/truncated.zst").write_bytes(whole[: len(whole) // 2])
    async with session_scope() as session:
        for cik, rel in files.items():
            entity = Entity(kind="person", name=f"Stored Name {cik[-1]}", sort_name=f"Name, Stored {cik[-1]}",
                            slug=f"stored-name-{cik.lstrip('0')}", tags=["form144_filer", "name_from_sec_record"])
            session.add(entity)
            await session.flush()
            await session.execute(insert(EntityId).values(entity_id=entity.id, scheme="sec_cik", value=cik,
                                                          source="edgar"))
            await session.execute(insert(RawDocument).values(
                source="edgar", url=f"https://data.sec.gov/submissions/CIK{cik}.json", sha256=cik * 6 + "0000",
                http_status=200, byte_size=10, storage_path=rel, doc_role="submissions"))
    async with session_scope() as session:
        assert await refresh_from_own_filings(session) == 1
    async with session_scope() as session:
        names = dict((await session.execute(text(
            "SELECT x.value, e.name FROM entities e JOIN entity_ids x ON x.entity_id = e.id"))).tuples().all())
    assert names == {"0000728618": "METROPOLITAN LIFE INSURANCE CO", "0000000001": "Stored Name 1",
                     "0000000002": "Stored Name 2", "0000000003": "Stored Name 3"}


@pytest.mark.db
@pytest.mark.usefixtures("db")
async def test_refresh_makes_a_misfiled_person_a_person_and_old_links_still_work() -> None:
    """8400: EDGAR's "Trust Jane" reads like a company; her filings are signed "for Jane E. Trust"."""
    from smt.api.app import create_app
    from smt.sources.edgar.names import refresh_from_own_filings

    doc = parse_ownership((FIX / "ownership" / f"{FORM4}.xml").read_bytes())
    owner = dataclasses.replace(doc.owners[0], name="Trust Jane")
    doc = dataclasses.replace(doc, owners=(owner,), signatures=(
        ("/s/ Marc A. DeOliveira by Power of Attorney for Jane E. Trust", None),))
    async with session_scope() as session:
        await load_ownership(session, meta(FORM4, "4"), doc)
    # As stored by the older rules.
    async with session_scope() as session:
        person = (await session.execute(select(Entity).where(Entity.slug.like(f"%-{CIK.lstrip('0')}")))).scalar_one()
        person.kind, person.name, person.sort_name = "company", "Trust Jane", "Trust Jane"
        person.slug = f"trust-jane-{CIK.lstrip('0')}"
    async with session_scope() as session:
        assert await refresh_from_own_filings(session) == 1
    person = await seller()
    assert (person.kind, person.name, person.sort_name) == ("person", "Jane Trust", "Trust, Jane")
    assert person.slug == f"jane-trust-{CIK.lstrip('0')}"

    # People and organizations share /people/<slug>; the old company-style link resolves by CIK.
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/people/trust-jane-{CIK.lstrip('0')}")
    assert response.status_code == 200
    entity = response.json()["entity"]
    assert (entity["name"], entity["slug"], entity["kind"]) == ("Jane Trust", person.slug, "person")


@pytest.mark.db
@pytest.mark.usefixtures("db")
@pytest.mark.parametrize(("conformed", "signature", "stored", "expected"), [
    # 19442: Concentrix's 10% owner, stored as the person "Bruxelles Lambert Groupe".
    ("Groupe Bruxelles Lambert",
     "Groupe Bruxelles Lambert., By: /s/ Priscilla Maters, Name: Priscilla Maters, Title: General Counsel",
     ("person", "Bruxelles Lambert Groupe", "Groupe, Bruxelles Lambert", "bruxelles-lambert-groupe"),
     ("company", "Groupe Bruxelles Lambert", "Groupe Bruxelles Lambert", "groupe-bruxelles-lambert")),
    # 63772: a NACCO director, stored as the company "Burns Patrick Joseph (2026)".
    ("Burns Patrick Joseph (2026)", "/s/ Patrick J. Burns",
     ("company", "Burns Patrick Joseph (2026)", "Burns Patrick Joseph (2026)", "burns-patrick-joseph-2026"),
     ("person", "Patrick Joseph Burns", "Burns, Patrick Joseph", "patrick-joseph-burns")),
])
async def test_refresh_corrects_the_round_7_kinds(conformed: str, signature: str, stored: tuple[str, ...],
                                                  expected: tuple[str, ...]) -> None:
    """The refresh re-derives each owner's kind and name from the conformed name on their own
    Form 3/4/5 and its signatures, so the stored rows are repaired without a new filing."""
    from smt.sources.edgar.names import refresh_from_own_filings

    doc = parse_ownership((FIX / "ownership" / f"{FORM4}.xml").read_bytes())
    owner = dataclasses.replace(doc.owners[0], name=conformed)
    doc = dataclasses.replace(doc, owners=(owner,), signatures=((signature, None),))
    async with session_scope() as session:
        await load_ownership(session, meta(FORM4, "4"), doc)
    cik = CIK.lstrip("0")
    async with session_scope() as session:  # as stored by the older rules
        filer = (await session.execute(select(Entity).where(Entity.slug.like(f"%-{cik}")))).scalar_one()
        filer.kind, filer.name, filer.sort_name, filer.slug = *stored[:3], f"{stored[3]}-{cik}"
    async with session_scope() as session:
        assert await refresh_from_own_filings(session) == 1
    filer = await seller()
    assert (filer.kind, filer.name, filer.sort_name, filer.slug) == (*expected[:3], f"{expected[3]}-{cik}")
    async with session_scope() as session:
        assert await refresh_from_own_filings(session) == 0  # idempotent
