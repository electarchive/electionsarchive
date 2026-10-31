import hashlib
import io
import json
import shlex
import subprocess
import traceback
import unicodedata
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, LiteralString, TypedDict, cast
from urllib.parse import unquote
from uuid import UUID, uuid7

import httpx
import polars as pl
import regex
import shapely
from PIL import Image, ImageOps
from psycopg import sql
from psycopg.types.json import Jsonb

from ea import boundaries, plane, raw
from ea.apply import _stage, apply, publish, revert
from ea.capture import capture, resume
from ea.changeset import Changeset, bcp47, slug
from ea.checks import Stage
from ea.sources import party_logos, wikidata_photos
from ea.store import DATA, ROOT, Connection, connect
from ea.uploads import process, sweep

VERSION = "1"

AuthorKind = Literal["user", "agent"]
Kind = Literal["edit", "correction", "digitization"]

EDITABLE: dict[str, dict[str, bool]] = {
    "name": {
        "entity_id": True,
        "text": True,
        "lang": True,
        "script": False,
        "kind": True,
        "valid_from": False,
        "valid_from_precision": False,
        "valid_to": False,
        "valid_to_precision": False,
        "is_preferred": False,
    },
    "note": {
        "entity_id": True,
        "kind": True,
        "body_md": True,
        "lang": True,
        "valid_from": False,
        "valid_from_precision": False,
        "valid_to": False,
        "valid_to_precision": False,
    },
    "external_id": {"entity_id": True, "scheme": True, "value": True},
}

CITED = {"name": True, "note": False, "external_id": False}

REFERENCES = {
    "wikidata": regex.compile(r"Q[1-9][0-9]*"),
    "bioguide": regex.compile(r"[A-Z][0-9]{6}"),
    "mnis": regex.compile(r"[1-9][0-9]*"),
    "parlgov": regex.compile(r"[1-9][0-9]*"),
}

DROPS = {"name": ("name_drop", "name_id", "text"), "note": ("note_drop", "note_id", "body_md")}

ROW_DROPS: dict[str, str | None] = {
    "citation": "entity_id",
    "label": "entity_id",
    "party_color": "party_id",
    "party_image": "party_id",
    "person_image": "person_id",
    "translation": None,
    "alliance_member": "alliance_id",
    "candidacy_nomination": "candidacy_id",
    "government_party": "government_id",
    "tenure_affiliation": "tenure_id",
    "area_event_member": "area_id",
    "party_relation": "from_party_id",
    "party_affiliation": "party_id",
}

PROVENANCE = {"name": "source_id"}

NOTE_LIMIT = 10_000

ORIGINS = ("human", "machine", "official")

QUALITIES = ("official", "osm", "academic", "digitized")

LISTED = 5

HEAVY = 50_000

SHOWN = 2_000

TIERS = {
    **dict.fromkeys(
        (
            "note",
            "citation",
            "source",
            "source_artifact",
            "source_page",
            "external_id",
            "name",
            "label",
            "party_color",
            "party_image",
            "person_image",
            "translation",
            "name_drop",
            "note_drop",
            "external_id_drop",
        ),
        1,
    ),
    **dict.fromkeys(("result_flow", "seat", "body_size"), 3),
}

KEYED = {"translation": ("source_md5", "lang"), "external_id": ("scheme", "value")}

COLOR = regex.compile(r"#[0-9a-f]{6}")

COMMONS_FILE = regex.compile(
    r"(?:https?://commons\.(?:m\.)?wikimedia\.org/wiki/)?(?:(?:File|Image):)?([^/#?|\[\]{}<>]+\.(?:svg|png|jpe?g|gif|tiff?|webp))",
    regex.IGNORECASE,
)

IMAGES = {"party_image": ("party", "party_id"), "person_image": ("person", "person_id")}

ROW_DROP_TIERS = {"label": 1}

LABELS = {
    "ballot": (("contest",), ("no_candidate_qualified",)),
    "integrity": (("election", "polity"), None),
    "recognition": (("polity", "area"), None),
    "data_quality": (None, None),
}

ASSESSED = ("integrity", "recognition")


SOURCE_KINDS = ("official_body", "aggregator", "compendium", "news_agency", "academic", "community")

STAGES = ("preliminary", "official", "certified", "court_revised", "recount", "alternative")

OUTCOMES = ("elected", "won", "advanced", "eliminated", "lost", "tied", "withdrew", "disqualified", "declined")

RESULT_TABLES = ("result_set", "result_value", "value_locator", "outcome")

UNIT_LABEL = sql.SQL(
    "coalesce((select n.text from name n where n.entity_id = u.id order by n.is_preferred desc, n.id limit 1), u.code)"
)

OPTION_LABEL = sql.SQL("""coalesce(
    o.ballot_label,
    (select string_agg(coalesce(c.ballot_name, (select n.text from name n where n.entity_id = c.person_id
                                                 order by n.is_preferred desc, n.id limit 1)), ' / ')
     from candidacy c where c.option_id = o.id),
    (select n.text from name n where n.entity_id = o.party_id order by n.is_preferred desc, n.id limit 1),
    o.kind::text)""")


class Evidence(TypedDict, total=False):
    source: str
    url: str
    capture: str
    upload: str
    title: str
    publisher: str
    kind: str
    locator: object
    page: int
    region: list[float]
    quote: str


class Of(TypedDict):
    table: str
    id: str


class Cell(TypedDict, total=False):
    unit: object
    option: str | None
    measure: str
    channel: str
    count_round: int
    value: object
    page: int
    region: list[float]


class Declared(TypedDict, total=False):
    option: str
    kind: str
    seats: int


class Op(TypedDict, total=False):
    op: str
    table: str
    id: str
    key: dict[str, object]
    reason: str
    ref: str
    values: dict[str, object]
    evidence: list[str]
    of: Of
    lang: str
    body: str
    origin: str
    contest_id: str
    stage: str
    as_of: str
    rows: list[Cell]
    outcomes: list[Declared]
    result_set: str
    cells: list[Cell]
    into: str
    election: dict[str, object]
    party: str
    other: str
    kind: str
    date: str
    election_id: str
    scheme: str
    field: str
    name_field: str
    valid_from: str
    valid_to: str
    quality: str
    exclude: list[str]


class Payload(TypedDict, total=False):
    summary: str
    rationale: str
    polity: str
    ops: list[Op]
    evidence: dict[str, Evidence]


def _uuid(value: object, where: str) -> UUID:
    if not isinstance(value, str):
        raise TypeError(f"{where}: expected an id, got {value!r}")
    try:
        return UUID(value)
    except ValueError:
        raise ValueError(f"{where}: {value!r} is not a UUID") from None


def _graphemes(text: str) -> int:
    return len(regex.findall(r"\X", text))


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _lang(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{where}: expected a BCP 47 language tag, got {value!r}")
    try:
        return bcp47(value)
    except ValueError as error:
        raise ValueError(f"{where}: {error}") from None


def _translate(
    conn: Connection, cs: Changeset, op: Op, where: str, source: UUID | None, seen: set[tuple[str, str]]
) -> None:
    of = op.get("of") or {}
    if of.get("table") != "note":
        raise ValueError(f"{where}: only notes can be translated yet")
    note = _uuid(of.get("id"), f"{where}.of.id")
    current = conn.execute("select body_md, lang from note where id = %s", [note]).fetchone()
    if current is None:
        raise ValueError(f"{where}: note {note} does not exist")
    original, source_lang = cast(tuple[str, str], current)
    lang = _lang(op.get("lang"), f"{where}.lang")
    if lang == source_lang:
        raise ValueError(f"{where}: note {note} is already in {lang}")
    origin = op.get("origin")
    if origin not in ORIGINS:
        raise ValueError(f"{where}: origin must be one of {', '.join(ORIGINS)}")
    if (origin == "official") != (source is not None):
        raise ValueError(f"{where}: an official translation needs evidence, and only an official one takes it")
    body = unicodedata.normalize("NFC", str(op.get("body") or "")).strip()
    if not body:
        raise ValueError(f"{where}: body is empty")
    if _graphemes(body) > max(NOTE_LIMIT, 2 * _graphemes(original)):
        raise ValueError(f"{where}: body is longer than the limit for this note")
    held = conn.execute(
        "select origin::text from translation where source_md5 = %s and lang = %s", [_md5(original), lang]
    ).fetchone()
    if origin == "machine" and held is not None and held[0] != "machine":
        raise ValueError(f"{where}: a machine translation cannot replace the {held[0]} one in {lang}")
    if (_md5(original), lang) in seen:
        raise ValueError(f"{where}: this text is already translated into {lang} in this draft")
    seen.add((_md5(original), lang))
    cs.add(
        "translation",
        source_md5=_md5(original),
        lang=lang,
        source_lang=source_lang,
        body=body,
        origin=origin,
        source_id=source,
        stale_from=None,
    )


def _day(value: object, where: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"{where}: expected a YYYY-MM-DD date, got {value!r}") from None


def _color(conn: Connection, cs: Changeset, op: Op, where: str, source: UUID) -> tuple[UUID, str, list[str]]:
    values = dict(op.get("values") or {})
    kind = op.get("op")
    allowed = {"add": {"party_id", "color", "valid_from", "valid_to"}, "set": {"color"}, "end": {"valid_to"}}
    if kind not in allowed:
        raise ValueError(f"{where}: a party color is added, set, ended, or dropped")
    if unknown := values.keys() - allowed[kind]:
        raise ValueError(f"{where}: {kind} party_color does not take {', '.join(sorted(unknown))}")
    precision = {"valid_from": "day", "valid_to": "day"}
    if kind == "add":
        party = _uuid(values.get("party_id"), f"{where}.values.party_id")
        color = values.get("color")
        start, end = (
            _day(values.get("valid_from"), f"{where}.valid_from"),
            _day(values.get("valid_to"), f"{where}.valid_to"),
        )
        changed = ["color"]
    else:
        key = op.get("key") or {}
        party = _uuid(key.get("party_id"), f"{where}.key.party_id")
        start = _day(key.get("valid_from"), f"{where}.key.valid_from")
        current = conn.execute(
            """select color, valid_to, valid_from_precision::text, valid_to_precision::text from party_color
               where party_id = %s and valid_from is not distinct from %s""",
            [party, start],
        ).fetchone()
        if current is None:
            raise ValueError(f"{where}: party {party} has no color from {start or 'the start'}")
        precision = {"valid_from": current[2], "valid_to": current[3]}
        color, end = (values.get("color"), current[1]) if kind == "set" else (current[0], None)
        if kind == "end":
            end = _day(values.get("valid_to"), f"{where}.values.valid_to")
            if end is None:
                raise ValueError(f"{where}: end needs valid_to")
            precision["valid_to"] = "day"
        if (color, end) == (current[0], current[1]):
            raise ValueError(f"{where}: {kind} changes nothing")
        changed = ["color"] if kind == "set" else ["valid_to"]
    if conn.execute("select from party where id = %s", [party]).fetchone() is None:
        raise ValueError(f"{where}: party {party} does not exist")
    color = str(color or "").strip().lower()
    if not COLOR.fullmatch(color):
        raise ValueError(f"{where}: color must look like #1a2b3c, got {color!r}")
    if start is not None and end is not None and start >= end:
        raise ValueError(f"{where}: valid_to must come after valid_from")
    for held_from, from_precision, held_to, to_precision, held_color, held_source in conn.execute(
        """select valid_from, valid_from_precision::text, valid_to, valid_to_precision::text, color, source_id
           from party_color where party_id = %s and valid && daterange(%s, %s, '[)')""",
        [party, start, end],
    ).fetchall():
        if kind == "end" and held_from == start:
            continue
        remainders = [
            *(
                [(held_from, from_precision, start, "day")]
                if start is not None and (held_from is None or held_from < start)
                else []
            ),
            *([(end, "day", held_to, to_precision)] if end is not None and (held_to is None or held_to > end) else []),
        ]
        for left, left_precision, right, right_precision in remainders:
            cs.add(
                "party_color",
                party_id=party,
                color=held_color,
                valid_from=left,
                valid_from_precision=left_precision,
                valid_to=right,
                valid_to_precision=right_precision,
                source_id=held_source,
            )
    cs.add(
        "party_color",
        party_id=party,
        color=color,
        valid_from=start,
        valid_from_precision=precision["valid_from"],
        valid_to=end,
        valid_to_precision=precision["valid_to"],
        source_id=source,
    )
    return party, f"{party}:{start or ''}", changed


def _an(word: str) -> str:
    return f"{'an' if word[0] in 'aeiou' else 'a'} {word}"


def _label(conn: Connection, cs: Changeset, op: Op, where: str, source: UUID) -> tuple[UUID, str]:
    values = dict(op.get("values") or {})
    if unknown := values.keys() - {"entity_id", "kind", "value", "assessor", "valid_from", "valid_to"}:
        raise ValueError(f"{where}: a label does not take {', '.join(sorted(unknown))}")
    entity = _uuid(values.get("entity_id"), f"{where}.values.entity_id")
    entity = cs.survivors.get(entity, entity)
    found = conn.execute("select type::text from entity where id = %s and retired_by is null", [entity]).fetchone()
    if found is None or entity in cs.retired:
        raise ValueError(f"{where}: entity {entity} does not exist")
    kind = str(values.get("kind"))
    if kind not in LABELS:
        raise ValueError(f"{where}: label kind must be one of {', '.join(LABELS)}")
    types, allowed = LABELS[kind]
    if types is not None and found[0] not in types:
        raise ValueError(
            f"{where}: {_an(kind)} label goes on {' or '.join(_an(t) for t in types)}, not {_an(found[0])}"
        )
    value = unicodedata.normalize("NFC", str(values.get("value") or "")).strip()
    if value == "" or len(value) > 200:
        raise ValueError(f"{where}: a label value is 1 to 200 characters")
    if allowed is not None and value not in allowed:
        raise ValueError(f"{where}: {_an(kind)} label is one of {', '.join(allowed)}")
    assessor = unicodedata.normalize("NFC", str(values.get("assessor") or "")).strip() or None
    if kind in ASSESSED and assessor is None:
        raise ValueError(f"{where}: {_an(kind)} label names its external assessor")
    if assessor is not None and len(assessor) > 200:
        raise ValueError(f"{where}: an assessor is at most 200 characters")
    start, end = (
        _day(values.get("valid_from"), f"{where}.valid_from"),
        _day(values.get("valid_to"), f"{where}.valid_to"),
    )
    if start is not None and end is not None and end <= start:
        raise ValueError(f"{where}: a label ends after it starts")
    if kind == "ballot":
        listed = conn.execute(
            "select count(*) from contest_option where contest_id = %s and kind in ('candidate', 'ticket', 'list')",
            [entity],
        ).fetchone()
        if listed is not None and listed[0] > 0:
            raise ValueError(f"{where}: this contest lists candidates, so nobody-qualified does not apply")
        if start is None:
            held = conn.execute(
                "select e.date_from from contest c join election e on e.id = c.election_id where c.id = %s", [entity]
            ).fetchone()
            start = None if held is None else held[0]
    if conn.execute(
        "select from label where entity_id = %s and kind = %s and value = %s and valid_to is null",
        [entity, kind, value],
    ).fetchone():
        raise ValueError(f"{where}: this record already has that {kind} label")
    id = uuid7()
    cs.add(
        "label",
        id=id,
        entity_id=entity,
        kind=kind,
        value=value,
        assessor=assessor,
        source_id=source,
        valid_from=start,
        valid_from_precision="day",
        valid_to=end,
        valid_to_precision="day",
    )
    return entity, str(id)


def _cite(
    cs: Changeset,
    entity: UUID,
    source: UUID,
    item: Evidence,
    proof: dict[str, str],
    locator: dict[str, object],
) -> None:
    cs.add(
        "citation",
        id=uuid7(),
        entity_id=entity,
        source_id=source,
        locator={
            **locator,
            **{field: item[field] for field in ("page", "region", "quote", "locator") if field in item},
            **proof,
        },
    )


def _carry(conn: Connection, cs: Changeset, before: str, after: str, lang: str) -> None:
    for target, body, origin, source, stale in conn.execute(
        """select t.lang, t.body, t.origin::text, t.source_id, coalesce(t.stale_from, t.source_md5)
           from translation t
           where t.source_md5 = %s and t.lang <> %s
             and not exists (select from translation n where n.source_md5 = %s and n.lang = t.lang)""",
        [_md5(before), lang, _md5(after)],
    ).fetchall():
        cs.add(
            "translation",
            source_md5=_md5(after),
            lang=target,
            source_lang=lang,
            body=body,
            origin=origin,
            source_id=source,
            stale_from=None if stale == _md5(after) else stale,
        )


def _webp(image: Image.Image, folder: Path, quality: int) -> str:
    buffer = io.BytesIO()
    image.save(buffer, "WEBP", quality=quality, method=6)
    content = buffer.getvalue()
    name = f"{hashlib.sha256(content).hexdigest()}.webp"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(content)
    return name


def _image(
    conn: Connection, cs: Changeset, op: Op, where: str, added: set[UUID], dated_url: str | None
) -> tuple[UUID, str]:
    table = str(op.get("table"))
    if op.get("op") != "image" or table not in IMAGES:
        raise ValueError(f"{where}: image adds a party_image or a person_image")
    kind, column = IMAGES[table]
    values = dict(op.get("values") or {})
    allowed = {column, "file"} | ({"valid_from", "valid_to"} if kind == "party" else set())
    if unknown := sorted(values.keys() - allowed):
        raise ValueError(f"{where}: an image takes no {', '.join(unknown)}")
    owner = _uuid(values.get(column), f"{where}.values.{column}")
    owner = cs.survivors.get(owner, owner)
    if conn.execute(sql.SQL("select from {} where id = %s").format(sql.Identifier(kind)), [owner]).fetchone() is None:
        raise ValueError(f"{where}: {kind} {owner} does not exist")
    found = COMMONS_FILE.fullmatch(unicodedata.normalize("NFC", str(values.get("file") or "")).strip())
    if found is None:
        raise ValueError(f"{where}: give a Wikimedia Commons file name or file page URL")
    file = unquote(found.group(1)).replace("_", " ").strip()
    module = party_logos if kind == "party" else wikidata_photos
    listing = raw.fetch("contrib", module._commons([file])[0], suffix=".json")
    pages = json.loads(listing.path.read_text(encoding="utf-8"))["query"].get("pages") or []
    page = pages[0] if pages else None
    if page is None or "imageinfo" not in page:
        raise ValueError(f"{where}: Wikimedia Commons has no file {file!r}")
    info = page["imageinfo"][0]
    meta = {key: str(value["value"]) for key, value in (info.get("extmetadata") or {}).items()}
    license = wikidata_photos._text(meta.get("LicenseShortName", ""))
    if not wikidata_photos._free({"code": meta.get("License", "").lower(), "license": license}):
        raise ValueError(
            f"{where}: {file} is under {license or 'no stated license'}; "
            "only public domain, CC0, CC BY, and CC BY-SA files can be added"
        )
    page_url = str(info["descriptionurl"])
    if conn.execute(
        sql.SQL("select from {} where {} = %s and source_url = %s").format(
            sql.Identifier(table), sql.Identifier(column)
        ),
        [owner, page_url],
    ).fetchone():
        raise ValueError(f"{where}: this {kind} already has {file}")
    thumb = str(info.get("thumburl") or info["url"])
    if httpx.URL(thumb).host not in ("upload.wikimedia.org", "thumb.wikimedia.org"):
        raise ValueError(f"{where}: Wikimedia Commons served {file} from an unexpected host")
    artifact = raw.fetch("contrib", thumb, suffix=Path(httpx.URL(thumb).path).suffix.lower() or ".png")
    title = str(page["title"])
    artist = wikidata_photos._credit(meta.get("Artist", "")) or wikidata_photos._credit(meta.get("Credit", ""))
    source = cs.source("Wikimedia Commons", title)
    if source in cs.minted and source not in added:
        added.add(source)
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="Wikimedia Commons",
            title=title,
            url=page_url,
            license=license,
            attribution=artist or None,
        )
    cs.artifact(listing, source)
    cs.artifact(artifact, source)
    uploaded = datetime.fromisoformat(info["timestamp"]) if info.get("timestamp") else None
    with Image.open(artifact.path) as opened:
        opened.load()
        image = ImageOps.exif_transpose(opened)
    if kind == "person":
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            image = background
        image = image.convert("RGB")
        if image.width > wikidata_photos.WIDTH:
            image = image.resize(
                (wikidata_photos.WIDTH, round(image.height * wikidata_photos.WIDTH / image.width)),
                Image.Resampling.LANCZOS,
            )
        taken, (taken_from, from_precision, taken_to, to_precision) = wikidata_photos._taken(
            meta.get("DateTimeOriginal", "")
        )
        latest = None if uploaded is None else uploaded.date() + timedelta(days=2)
        if (taken_from is not None and taken_to is not None and taken_from > taken_to) or (
            latest is not None and taken_from is not None and taken_from > latest
        ):
            taken_from, from_precision, taken_to, to_precision = None, "unknown", None, "unknown"
        if latest is not None and taken_to is not None and taken_to > latest and uploaded is not None:
            taken_to, to_precision = uploaded.date(), "day"
        cs.add(
            "person_image",
            id=uuid7(),
            person_id=owner,
            path=f"photos/{_webp(image, wikidata_photos.PHOTOS, 80)}",
            license=license,
            artist=artist or None,
            source_url=page_url,
            source_id=source,
            taken_text=taken,
            taken_from=taken_from,
            taken_from_precision=from_precision,
            taken_to=taken_to,
            taken_to_precision=to_precision,
            uploaded_at=uploaded,
        )
        return owner, title
    start = _day(values.get("valid_from"), f"{where}.values.valid_from")
    end = _day(values.get("valid_to"), f"{where}.values.valid_to")
    if start is not None and end is not None and start >= end:
        raise ValueError(f"{where}: valid_from must come before valid_to")
    dated = start is not None or end is not None
    if dated and dated_url is None:
        raise ValueError(f"{where}: a logo's dates need evidence that links to the page stating them")
    made = party_logos._renditions(image)
    if made is None:
        raise ValueError(f"{where}: {file} has nothing to show once its background is cleared")
    mark, lockup, shape, plate, light = made
    cs.add(
        "party_image",
        id=uuid7(),
        party_id=owner,
        path=f"logos/{_webp(lockup, party_logos.LOGOS, 90)}",
        width=lockup.width,
        height=lockup.height,
        mark_path=f"logos/{_webp(mark, party_logos.LOGOS, 90)}",
        shape=shape,
        is_plate=plate,
        is_light=light,
        license=license,
        artist=artist or None,
        source_url=page_url,
        source_id=source,
        valid_from=start,
        valid_from_precision="unknown" if start is None else "day",
        valid_to=end,
        valid_to_precision="unknown" if end is None else "day",
        dated_by="source" if dated else None,
        dated_url=dated_url if dated else None,
        uploaded_at=uploaded,
    )
    return owner, title


def _source(cs: Changeset, item: Evidence, where: str, added: set[UUID], url: str | None) -> UUID:
    title = unicodedata.normalize("NFC", str(item.get("title") or "")).strip()
    publisher = unicodedata.normalize("NFC", str(item.get("publisher") or "")).strip()
    if not title or not publisher:
        raise ValueError(f"{where}: a URL or an upload needs a title and a publisher")
    kind = item.get("kind")
    if kind not in SOURCE_KINDS:
        raise ValueError(f"{where}: kind must be one of {', '.join(SOURCE_KINDS)}")
    source = cs.source(publisher, title)
    if source in cs.minted and source not in added:
        added.add(source)
        cs.add("source", id=source, kind=kind, publisher=publisher, title=title, url=url)
    return source


def _linked(
    conn: Connection, cs: Changeset, item: Evidence, author: UUID, where: str, added: set[UUID]
) -> tuple[UUID, dict[str, str]]:
    capture = _uuid(item.get("capture"), f"{where}.capture")
    row = conn.execute(
        """select url, final_url, raw_sha256, raw_key, media_type, bytes, captured_at, wayback_url from archive_capture
           where id = %s and status = 'captured' and owner_user_id = %s""",
        [capture, author],
    ).fetchone()
    if row is None:
        raise ValueError(f"{where}: capture {capture} is not an archived copy of this author's")
    url, final_url, sha256, key, media_type, size, captured_at, wayback = row
    if url != item.get("url"):
        raise ValueError(f"{where}: capture {capture} archived {url}, not {item.get('url')}")
    source = _source(cs, item, where, added, url)
    cs.artifact(
        raw.Artifact(
            source="contrib",
            url=final_url,
            sha256=sha256,
            path=DATA / key,
            retrieved_at=captured_at,
            media_type=media_type,
            bytes=size,
        ),
        source,
    )
    return source, {"sha256": sha256} | ({} if wayback is None else {"wayback": wayback})


def _page(
    conn: Connection, cs: Changeset, upload: UUID, artifact: UUID, number: int, added: set[UUID], where: str
) -> UUID:
    image = conn.execute(
        "select image_key, width, height from upload_page where upload_id = %s and page_number = %s", [upload, number]
    ).fetchone()
    if image is None:
        raise ValueError(f"{where}: upload {upload} has no page {number}")
    page = cs.page(artifact, number)
    if page not in added:
        added.add(page)
        cs.add(
            "source_page",
            id=page,
            artifact_id=artifact,
            page_number=number,
            image_key=image[0],
            width=image[1],
            height=image[2],
        )
    return page


def _region(region: object, where: str) -> list[float]:
    if not (
        isinstance(region, list)
        and len(region) == 4
        and all(isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= 1 for value in region)
        and region[0] < region[2]
        and region[1] < region[3]
    ):
        raise ValueError(f"{where}: region must be [x0, y0, x1, y1] fractions of the page, with x0 < x1 and y0 < y1")
    return [float(value) for value in region]


def _uploaded(
    conn: Connection,
    cs: Changeset,
    item: Evidence,
    author: UUID,
    where: str,
    added: set[UUID],
    files: dict[UUID, tuple[UUID, UUID, int]],
) -> tuple[UUID, dict[str, str]]:
    upload = _uuid(item.get("upload"), f"{where}.upload")
    row = conn.execute(
        """select sha256, storage_key, media_type, bytes, created_at, pages from upload
           where id = %s and status = 'ready' and owner_user_id = %s""",
        [upload, author],
    ).fetchone()
    if row is None:
        raise ValueError(f"{where}: upload {upload} is not a ready upload of this author's")
    sha256, key, media_type, size, created_at, pages = row
    source = _source(cs, item, where, added, None)
    artifact = cs.artifact(
        raw.Artifact(
            source="contrib",
            url=f"upload:{sha256}",
            sha256=sha256,
            path=DATA / key,
            retrieved_at=created_at,
            media_type=media_type,
            bytes=size,
        ),
        source,
    )
    files[source] = (upload, artifact, pages or 0)
    proof = {"sha256": sha256}
    number = item.get("page")
    if number is None:
        if "region" in item:
            raise ValueError(f"{where}: a region needs a page")
        return source, proof
    if pages is None or not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= pages:
        raise ValueError(f"{where}: page must be between 1 and {pages or 0}")
    if "region" in item:
        _region(item["region"], where)
    page = _page(conn, cs, upload, artifact, number, added, where)
    return source, proof | {"page_id": str(page)}


def _columns(conn: Connection, table: str) -> dict[str, str]:
    return dict(
        conn.execute(
            """select attname, format_type(atttypid, atttypmod) from pg_attribute
               where attrelid = %s::regclass and attnum > 0 and not attisdropped and attgenerated = ''
                 and attname not in ('changeset_id', 'recorded_from') order by attnum""",
            [f"public.{table}"],
        ).fetchall()
    )


def _snapshot(conn: Connection, table: str, match: dict[str, object], where: str) -> dict[str, object]:
    kinds = _columns(conn, table)
    if not match or match.keys() - kinds.keys():
        raise ValueError(f"{where}: a {table} key takes {', '.join(kinds)}")
    rows = conn.execute(
        sql.SQL("select jsonb_build_object({}) from public.{} p where {} limit 2").format(
            sql.SQL(", ").join(
                sql.SQL("{}, p.{}").format(sql.Literal(column), sql.Identifier(column)) for column in kinds
            ),
            sql.Identifier(table),
            sql.SQL(" and ").join(
                [
                    *(
                        sql.SQL("p.{} is not distinct from ({})").format(
                            sql.Identifier(column),
                            sql.SQL("%(key)s -> {}").format(sql.Literal(column))
                            if kinds[column] == "jsonb"
                            else sql.SQL("(%(key)s ->> {})::{}").format(
                                sql.Literal(column), sql.SQL(cast(LiteralString, kinds[column]))
                            ),
                        )
                        for column in match
                    ),
                    *(
                        sql.SQL("p.{} = (%(key)s ->> {})::uuid").format(sql.Identifier(column), sql.Literal(column))
                        for column in match
                        if kinds[column] == "uuid" and match[column] is not None
                    ),
                ]
            ),
        ),
        {"key": Jsonb(match)},
    ).fetchall()
    if len(rows) != 1:
        raise ValueError(f"{where}: {'more than one' if rows else 'no'} {table} row matches that key")
    return cast(dict[str, object], rows[0][0])


def _drop(conn: Connection, cs: Changeset, op: Op, where: str, reason: str) -> tuple[UUID | None, str]:
    table = op.get("table", "")
    if table in DROPS:
        drop, column, text = DROPS[table]
        id = _uuid(op.get("id"), f"{where}.id")
        current = conn.execute(
            sql.SQL("select entity_id, {} from public.{} where id = %s").format(
                sql.Identifier(text), sql.Identifier(table)
            ),
            [id],
        ).fetchone()
        if current is None:
            raise ValueError(f"{where}: {table} {id} does not exist")
        cs.add(drop, **{column: id, "entity_id": current[0], text: current[1], "reason": reason})
        return current[0], str(id)
    if table == "external_id":
        key = op.get("key") or {}
        scheme, value = key.get("scheme"), key.get("value")
        if scheme not in REFERENCES:
            raise ValueError(f"{where}: only {', '.join(REFERENCES)} IDs can be dropped")
        current = conn.execute(
            "select entity_id from external_id where scheme = %s and value = %s", [scheme, value]
        ).fetchone()
        if current is None:
            raise ValueError(f"{where}: no entity has {scheme} ID {value}")
        cs.add("external_id_drop", entity_id=current[0], scheme=scheme, value=value, reason=reason)
        return current[0], f"{scheme}:{value}"
    if table in ROW_DROPS:
        match = {"id": str(_uuid(op.get("id"), f"{where}.id"))} if "id" in op else dict(op.get("key") or {})
        row = _snapshot(conn, table, match, where)
        cs.add("row_drop", id=uuid7(), relation=table, key=row, reason=reason)
        owner = ROW_DROPS[table]
        label = str(row["id"]) if "id" in row else _dumps({column: row[column] for column in match})
        return None if owner is None else _uuid(row[owner], f"{where}.{owner}"), label
    raise ValueError(f"{where}: {table!r} rows cannot be dropped")


def _codes(conn: Connection, table: Literal["measure", "channel"]) -> dict[str, int]:
    return {code: id for id, code in conn.execute(sql.SQL("select id, code from {}").format(sql.Identifier(table)))}


def _code(codes: dict[str, int], value: object, where: str) -> int:
    if not isinstance(value, str) or value not in codes:
        raise ValueError(f"{where}: expected one of {', '.join(codes)}, got {value!r}")
    return codes[value]


def _unit(conn: Connection, value: object, on: date | None, where: str) -> UUID:
    if isinstance(value, dict):
        scheme, code = value.get("scheme"), value.get("code")
        found = (
            conn.execute(
                """select id from reporting_unit where scheme_code = %s and code = %s
                   and (%s::date is null or valid @> %s::date)""",
                [scheme, code, on, on],
            ).fetchone()
            if isinstance(scheme, str) and isinstance(code, str)
            else None
        )
        if found is None:
            raise ValueError(f"{where}: no reporting unit {scheme} {code} on {on}")
        return cast(UUID, found[0])
    unit = _uuid(value, where)
    if conn.execute("select from reporting_unit where id = %s", [unit]).fetchone() is None:
        raise ValueError(f"{where}: reporting unit {unit} does not exist")
    return unit


def _amount(value: object, where: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{where}: value must be a number of at least 0, got {value!r}")
    return float(value)


def _round(value: object, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{where}: count_round must be a whole number of at least 0")
    return value


Address = tuple[UUID, UUID | None, int, int, int]


def _address(
    conn: Connection,
    cell: Cell,
    on: date | None,
    options: set[UUID],
    codes: tuple[dict[str, int], dict[str, int]],
    where: str,
) -> Address:
    unit = _unit(conn, cell.get("unit"), on, f"{where}.unit")
    option = None if cell.get("option") is None else _uuid(cell.get("option"), f"{where}.option")
    if option is not None and option not in options:
        raise ValueError(f"{where}: option {option} is not on this contest's ballot")
    return (
        unit,
        option,
        _code(codes[1], cell.get("channel", "all"), f"{where}.channel"),
        _code(codes[0], cell.get("measure"), f"{where}.measure"),
        _round(cell.get("count_round", 0), f"{where}.count_round"),
    )


def _held(conn: Connection, election: UUID, contest: UUID, stage: str) -> bool:
    return (
        conn.execute(
            "select from result_set where election_id = %s and contest_id = %s and stage = %s and is_default",
            [election, contest, stage],
        ).fetchone()
        is not None
    )


def _results(
    conn: Connection,
    cs: Changeset,
    op: Op,
    where: str,
    source: UUID,
    files: dict[UUID, tuple[UUID, UUID, int]],
    evidence: Evidence,
    proof: dict[str, str],
    added: set[UUID],
    undefaulted: bool,
) -> tuple[UUID, UUID]:
    contest = _uuid(op.get("contest_id"), f"{where}.contest_id")
    found = conn.execute(
        "select c.election_id, e.date_from from contest c join election e on e.id = c.election_id where c.id = %s",
        [contest],
    ).fetchone()
    if found is None:
        raise ValueError(f"{where}: contest {contest} does not exist")
    election, on = cast(tuple[UUID, date | None], found)
    stage = op.get("stage")
    if stage not in STAGES:
        raise ValueError(f"{where}: stage must be one of {', '.join(STAGES)}")
    rows = op.get("rows") or []
    if not rows:
        raise ValueError(f"{where}: results need rows")
    if len(rows) > HEAVY:
        raise ValueError(f"{where}: a results op holds at most {HEAVY:,} rows")
    options = {id for (id,) in conn.execute("select id from contest_option where contest_id = %s", [contest])}
    codes = (_codes(conn, "measure"), _codes(conn, "channel"))
    result_set = uuid7()
    as_of = _day(op.get("as_of"), f"{where}.as_of")
    cs.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage=stage,
        kind="counted",
        source_id=source,
        as_of=None if as_of is None else datetime(as_of.year, as_of.month, as_of.day),
        status="active",
        is_default=not undefaulted and not _held(conn, election, contest, stage),
        supersedes_id=None,
        completeness=None,
    )
    upload = files.get(source)
    seen: set[Address] = set()
    for n, row in enumerate(rows):
        at = f"{where}.rows[{n}]"
        unit, option, channel, measure, count_round = address = _address(conn, row, on, options, codes, at)
        if address in seen:
            raise ValueError(f"{at}: this unit, option, channel, measure, and round appear twice")
        seen.add(address)
        keys = {
            "result_set_id": result_set,
            "unit_id": unit,
            "option_id": option,
            "channel": channel,
            "measure": measure,
            "count_round": count_round,
        }
        cs.add("result_value", **keys, flags=0, value=_amount(row.get("value"), f"{at}.value"))
        if "region" not in row and "page" not in row:
            continue
        if upload is None:
            raise ValueError(f"{at}: a page or region needs upload evidence")
        number = row.get("page", evidence.get("page"))
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= upload[2]:
            raise ValueError(f"{at}: page must be between 1 and {upload[2]}")
        region = _region(row["region"], f"{at}.region") if "region" in row else [None, None, None, None]
        cs.add(
            "value_locator",
            **keys,
            page_id=_page(conn, cs, upload[0], upload[1], number, added, at),
            source_id=None,
            x0=region[0],
            y0=region[1],
            x1=region[2],
            y1=region[3],
        )
    declared: set[UUID] = set()
    for n, outcome in enumerate(op.get("outcomes") or []):
        at = f"{where}.outcomes[{n}]"
        option = _uuid(outcome.get("option"), f"{at}.option")
        if option not in options:
            raise ValueError(f"{at}: option {option} is not on this contest's ballot")
        if option in declared:
            raise ValueError(f"{at}: option {option} has two outcomes")
        declared.add(option)
        kind = outcome.get("kind")
        if kind not in OUTCOMES:
            raise ValueError(f"{at}: kind must be one of {', '.join(OUTCOMES)}")
        seats = outcome.get("seats")
        if seats is not None and (not isinstance(seats, int) or isinstance(seats, bool) or seats < 0):
            raise ValueError(f"{at}: seats must be a whole number of at least 0")
        cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats, notes=None)
    _cite(cs, contest, source, evidence, proof, {"table": "result_set", "row": str(result_set), "op": "results"})
    return contest, result_set


class Corrected(TypedDict):
    result_set: UUID
    contest: UUID
    pipeline: str
    cells: list[tuple[Address, float | None, float]]


def _corrected(conn: Connection, op: Op, where: str) -> Corrected:
    result_set = _uuid(op.get("result_set"), f"{where}.result_set")
    found = conn.execute(
        """select r.k, r.contest_id, r.status::text, c.pipeline, e.date_from
           from result_set r join changeset c on c.id = r.changeset_id join election e on e.id = r.election_id
           where r.id = %s""",
        [result_set],
    ).fetchone()
    if found is None:
        raise ValueError(f"{where}: result set {result_set} does not exist")
    k, contest, status, pipeline, on = found
    if status != "active":
        raise ValueError(f"{where}: result set {result_set} is {status}; correct the active set")
    cells = op.get("cells") or []
    if not cells:
        raise ValueError(f"{where}: a correction needs cells")
    if len(cells) > SHOWN:
        raise ValueError(f"{where}: a correction holds at most {SHOWN:,} cells")
    options = {id for (id,) in conn.execute("select id from contest_option where contest_id = %s", [contest])}
    codes = (_codes(conn, "measure"), _codes(conn, "channel"))
    corrected: list[tuple[Address, float | None, float]] = []
    seen: set[Address] = set()
    for n, cell in enumerate(cells):
        at = f"{where}.cells[{n}]"
        address = _address(conn, cell, on, options, codes, at)
        if address in seen:
            raise ValueError(f"{at}: this cell appears twice")
        seen.add(address)
        unit, option, channel, measure, count_round = address
        value = _amount(cell.get("value"), f"{at}.value")
        held = conn.execute(
            """select v.value from result_value v join reporting_unit u on u.k = v.unit_k
               where v.result_set_k = %s and u.id = %s
                 and v.option_k = coalesce((select o.k from contest_option o where o.id = %s), 0)
                 and v.channel = %s and v.measure = %s and v.count_round = %s""",
            [k, unit, option, channel, measure, count_round],
        ).fetchone()
        before = None if held is None else float(held[0])
        if before == value:
            raise ValueError(f"{at}: the set already holds {value:g}")
        corrected.append((address, before, value))
    return {"result_set": result_set, "contest": contest, "pipeline": pipeline, "cells": corrected}


def _restage(
    conn: Connection,
    cs: Changeset,
    corrected: Corrected,
    source: UUID,
    evidence: Evidence,
    proof: dict[str, str],
) -> None:
    result_set = corrected["result_set"]
    changes = {address: value for address, _, value in corrected["cells"]}
    held = conn.execute(
        """select u.id, o.id, v.channel, v.measure, v.count_round, v.flags, v.value
           from result_value v join result_set r on r.k = v.result_set_k join reporting_unit u on u.k = v.unit_k
           left join contest_option o on o.k = v.option_k where r.id = %s""",
        [result_set],
    ).fetchall()
    for unit, option, channel, measure, count_round, flags, value in held:
        address = (unit, option, channel, measure, count_round)
        cs.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=channel,
            measure=measure,
            count_round=count_round,
            flags=flags,
            value=changes.pop(address, value),
        )
    for (unit, option, channel, measure, count_round), value in changes.items():
        cs.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=channel,
            measure=measure,
            count_round=count_round,
            flags=0,
            value=value,
        )
    for unit, option, channel, measure, count_round, page, located, x0, y0, x1, y1 in conn.execute(
        """select u.id, o.id, l.channel, l.measure, l.count_round, l.page_id, l.source_id, l.x0, l.y0, l.x1, l.y1
           from value_locator l join result_set r on r.k = l.result_set_k join reporting_unit u on u.k = l.unit_k
           left join contest_option o on o.k = l.option_k where r.id = %s""",
        [result_set],
    ).fetchall():
        cs.add(
            "value_locator",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=channel,
            measure=measure,
            count_round=count_round,
            page_id=page,
            source_id=located,
            x0=x0,
            y0=y0,
            x1=x1,
            y1=y1,
        )
    _cite(
        cs,
        corrected["contest"],
        source,
        evidence,
        proof,
        {"table": "result_set", "row": str(result_set), "op": "correct"},
    )


def _cell_rows(conn: Connection, corrected: Corrected) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for (unit, option, channel, measure, count_round), before, after in corrected["cells"]:
        labels = conn.execute(
            sql.SQL(
                """select u.code, {},
                          (select {} from contest_option o where o.id = %s),
                          (select code from measure where id = %s), (select code from channel where id = %s)
                   from reporting_unit u where u.id = %s"""
            ).format(UNIT_LABEL, OPTION_LABEL),
            [option, measure, channel, unit],
        ).fetchone()
        rows.append(
            {
                "table": "result_value",
                "key": {
                    "result_set_id": corrected["result_set"],
                    "unit_id": unit,
                    "option_id": option,
                    "channel": channel,
                    "measure": measure,
                    "count_round": count_round,
                },
                "labels": None
                if labels is None
                else {"unit": labels[1], "option": labels[2], "measure": labels[3], "channel": labels[4]},
                "entity_id": corrected["contest"],
                "op": "add" if before is None else "change",
                "fields": {"value": [before, after]},
            }
        )
    return rows


def _fold(conn: Connection, payload: Payload) -> dict[str, object] | None:
    ops = payload.get("ops") or []
    corrections = [(n, op) for n, op in enumerate(ops) if op.get("op") == "correct"]
    if not corrections:
        return None
    folded = [(n, _corrected(conn, op, f"ops[{n}]")) for n, op in corrections]
    foreign = [corrected for _, corrected in folded if corrected["pipeline"] != "contrib"]
    if not foreign:
        return None
    if len(foreign) != len(ops):
        raise ValueError(
            "a correction to a set a pipeline wrote is folded into that pipeline: put it in its own proposal, "
            "with only correct ops on such sets"
        )
    evidence = payload.get("evidence") or {}
    for n, op in corrections:
        cited = op.get("evidence") or []
        if not cited:
            raise ValueError(f"ops[{n}]: a correction needs evidence")
        missing = [key for key in cited if key not in evidence]
        if missing:
            raise ValueError(f"ops[{n}]: unknown evidence {', '.join(missing)}")
    rows = [row for _, corrected in folded for row in _cell_rows(conn, corrected)]
    return {
        "rows": rows,
        "truncated": False,
        "entities": sorted({str(corrected["contest"]) for _, corrected in folded}),
        "fold": {
            "sets": [
                {"result_set": str(corrected["result_set"]), "pipeline": corrected["pipeline"]}
                for _, corrected in folded
            ],
            "cells": len(rows),
        },
    }


ELECTION_KINDS = ("general", "by_election", "primary", "runoff", "referendum", "recall", "local", "special")
CONTEST_KINDS = ("office", "body_seats", "list_allocation", "ballot_question")
OPTION_KINDS: dict[str, tuple[str, ...]] = {
    "office": ("candidate", "write_in", "others"),
    "body_seats": ("candidate", "write_in", "others"),
    "list_allocation": ("list", "others"),
    "ballot_question": ("answer",),
}
QUESTION_TYPES = ("initiative", "mandatory_referendum", "optional_referendum", "plebiscite", "recall_question")

RELATION_KINDS = (
    "merged_into",
    "split_from",
    "successor_of",
    "refounded_as",
    "absorbed_by",
    "closed_by_court",
    "closed_by_government",
    "list_vehicle_for",
)

CLOSURES = ("closed_by_court", "closed_by_government")


def _typed(conn: Connection, cs: Changeset, value: object, where: str, kind: str) -> UUID:
    entity = _uuid(value, where)
    row = conn.execute(
        """select type::text, retired_by is not null or exists (select from entity_merge m where m.entity_id = e.id)
           from entity e where id = %s""",
        [entity],
    ).fetchone()
    if row is None:
        raise ValueError(f"{where}: entity {entity} does not exist")
    if row[1] or entity in cs.retired:
        raise ValueError(f"{where}: entity {entity} was merged or retired")
    if row[0] != kind:
        raise ValueError(f"{where}: {entity} is a {row[0]}, not a {kind}")
    return entity


def _text(value: object, where: str, limit: int = 300) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).strip()
    if not text:
        raise ValueError(f"{where} is required")
    if _graphemes(text) > limit:
        raise ValueError(f"{where}: at most {limit} characters")
    return text


def _election(conn: Connection, cs: Changeset, op: Op, where: str) -> list[UUID]:
    spec = op.get("election") or {}
    polity = _typed(conn, cs, spec.get("polity"), f"{where}.election.polity", "polity")
    scope = cast(dict[str, object], cs.manifest["scope"])
    if scope.get("polity") is None:
        country = conn.execute(
            """with recursive up (id, depth) as (
                 select %s::uuid, 0
                 union select pp.parent_id, up.depth + 1 from polity_parent pp join up on pp.polity_id = up.id
                 where up.depth < 20
               )
               select p.iso_code from up join polity p using (id)
               where p.iso_code is not null and p.iso_code not like '%%-%%' order by up.depth limit 1""",
            [polity],
        ).fetchone()
        scope["polity"] = None if country is None else country[0]
    parent = (
        None
        if spec.get("parent") is None
        else _typed(conn, cs, spec.get("parent"), f"{where}.election.parent", "election")
    )
    kind = str(spec.get("kind"))
    if kind not in ELECTION_KINDS:
        raise ValueError(f"{where}.election.kind: {kind!r} is not one of {', '.join(ELECTION_KINDS)}")
    status = str(spec.get("status"))
    if status not in ("held", "scheduled"):
        raise ValueError(f"{where}.election.status: {status!r} is not held or scheduled")
    try:
        day = date.fromisoformat(str(spec.get("date")))
    except ValueError:
        raise ValueError(f"{where}.election.date: {spec.get('date')!r} is not a date (YYYY-MM-DD)") from None
    if (status == "held") != (day <= date.today()):
        raise ValueError(f"{where}.election.status: an election on {day} is not {status}")
    lang = _lang(spec.get("lang") or "en", f"{where}.election.lang")
    name = _text(spec.get("name"), f"{where}.election.name")
    root = conn.execute("select path from slug where entity_id = %s order by length(path) limit 1", [polity]).fetchone()
    if root is None:
        raise ValueError(f"{where}.election.polity: polity {polity} has no address")
    path = f"{root[0]}/{day}-{kind.replace('_', '-')}"
    held = conn.execute("select entity_id from slug where path = %s", [path]).fetchone()
    if held is not None:
        raise ValueError(f"{where}.election: election {held[0]} already has the address /{path}")
    contests = cast(list[dict[str, object]], spec.get("contests") or [])
    if not contests or len(contests) > 200:
        raise ValueError(f"{where}.election.contests: list 1 to 200 contests")
    election = uuid7()
    cs.add(
        "election",
        id=election,
        polity_id=polity,
        parent_id=parent,
        kind=kind,
        date_from=day,
        date_to=day,
        date_precision="day",
        status=status,
    )
    cs.add("name", entity_id=election, text=name, lang=lang, kind="official", is_preferred=True)
    cs.add("slug", path=path, entity_id=election)
    created = [election]
    paths: set[str] = set()
    for n, contest in enumerate(contests):
        at = f"{where}.election.contests[{n}]"
        title = _text(contest.get("name"), f"{at}.name")
        sort = str(contest.get("kind"))
        if sort not in CONTEST_KINDS:
            raise ValueError(f"{at}.kind: {sort!r} is not one of {', '.join(CONTEST_KINDS)}")
        tail = slug(title)
        if not tail or f"{path}/{tail}" in paths:
            raise ValueError(f"{at}.name: {title!r} needs a name that differs from the other contests")
        paths.add(f"{path}/{tail}")
        magnitude = contest.get("magnitude")
        if magnitude is not None and (not isinstance(magnitude, int) or isinstance(magnitude, bool) or magnitude < 1):
            raise ValueError(f"{at}.magnitude: {magnitude!r} is not a positive whole number")
        id = uuid7()
        cs.add(
            "contest",
            id=id,
            election_id=election,
            kind=sort,
            office_id=None
            if contest.get("office") is None
            else _typed(conn, cs, contest.get("office"), f"{at}.office", "office"),
            body_id=None
            if contest.get("body") is None
            else _typed(conn, cs, contest.get("body"), f"{at}.body", "body"),
            district_area_id=None
            if contest.get("area") is None
            else _typed(conn, cs, contest.get("area"), f"{at}.area", "area"),
            magnitude=magnitude,
            round_number=1,
            default_result_kind="counted",
        )
        cs.add("name", entity_id=id, text=title, lang=lang, kind="official", is_preferred=True)
        cs.add("slug", path=f"{path}/{tail}", entity_id=id)
        if sort == "ballot_question":
            question = str(contest.get("question"))
            if question not in QUESTION_TYPES:
                raise ValueError(f"{at}.question: {question!r} is not one of {', '.join(QUESTION_TYPES)}")
            cs.add("ballot_question", contest_id=id, type=question)
        created.append(id)
        options = cast(list[dict[str, object]], contest.get("options") or [])
        if len(options) > 500:
            raise ValueError(f"{at}.options: at most 500 options")
        for k, option in enumerate(options):
            on = f"{at}.options[{k}]"
            variety = str(option.get("kind"))
            if variety not in OPTION_KINDS[sort]:
                raise ValueError(f"{on}.kind: a {sort} contest takes {', '.join(OPTION_KINDS[sort])} options")
            party = (
                None if option.get("party") is None else _typed(conn, cs, option.get("party"), f"{on}.party", "party")
            )
            person = None
            if variety == "candidate":
                if option.get("person") is not None:
                    person = _typed(conn, cs, option.get("person"), f"{on}.person", "person")
                else:
                    person = uuid7()
                    cs.add("person", id=person)
                    cs.add(
                        "name",
                        entity_id=person,
                        text=_text(option.get("person_name"), f"{on}.person_name"),
                        lang=lang,
                        kind="official",
                        is_preferred=True,
                    )
                    created.append(person)
            elif variety == "list" and party is None:
                raise ValueError(f"{on}.party: a list option needs its party")
            label = option.get("label")
            choice = uuid7()
            cs.add(
                "contest_option",
                id=choice,
                contest_id=id,
                kind=variety,
                party_id=party,
                ballot_position=k + 1,
                ballot_label=None if label is None else _text(label, f"{on}.label"),
            )
            if person is not None:
                cs.add(
                    "candidacy",
                    id=uuid7(),
                    person_id=person,
                    option_id=choice,
                    role="head",
                    member_of_party_id=party,
                    is_independent=party is None,
                )
    return created


def _when(value: object, where: str) -> tuple[date | None, str]:
    text = str(value or "").strip()
    if not text:
        return None, "unknown"
    match = regex.fullmatch(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", text)
    if match is None:
        raise ValueError(f"{where}: {text!r} is not YYYY, YYYY-MM, or YYYY-MM-DD")
    year, month, day = match.groups()
    try:
        when = date(int(year), int(month or 1), int(day or 1))
    except ValueError:
        raise ValueError(f"{where}: {text!r} is not a date") from None
    return when, "day" if day else "month" if month else "year"


def _lineage(conn: Connection, cs: Changeset, op: Op, where: str, source: UUID, unlinked: set[UUID]) -> UUID:
    kind = str(op.get("kind"))
    if kind not in RELATION_KINDS:
        raise ValueError(f"{where}.kind: {kind!r} is not one of {', '.join(RELATION_KINDS)}")
    party = _typed(conn, cs, op.get("party"), f"{where}.party", "party")
    other = None if op.get("other") is None else _typed(conn, cs, op.get("other"), f"{where}.other", "party")
    if kind in CLOSURES and other is not None:
        raise ValueError(f"{where}.other: a closure has no other party")
    if kind not in CLOSURES and other is None:
        raise ValueError(f"{where}.other: a {kind} link needs the other party")
    if other == party:
        raise ValueError(f"{where}.other: a party cannot link to itself")
    election = (
        None
        if op.get("election_id") is None
        else _typed(conn, cs, op.get("election_id"), f"{where}.election_id", "election")
    )
    if (kind == "list_vehicle_for") != (election is not None):
        raise ValueError(f"{where}.election_id: only a list_vehicle_for link names its election, and it must")
    when, precision = _when(op.get("date"), f"{where}.date")
    held = {
        row[0]
        for row in conn.execute(
            """select id from party_relation where from_party_id = %s and to_party_id is not distinct from %s
               and kind = %s and election_id is not distinct from %s""",
            [party, other, kind, election],
        )
    }
    if held - unlinked:
        raise ValueError(f"{where}: the archive already has this {kind} link")
    cs.add(
        "party_relation",
        id=uuid7(),
        from_party_id=party,
        to_party_id=other,
        kind=kind,
        date=when,
        date_precision=precision,
        election_id=election,
        source_id=source,
    )
    return party


def _shape(
    conn: Connection, cs: Changeset, op: Op, where: str, source: UUID, files: dict[UUID, tuple[UUID, UUID, int]]
) -> None:
    if source not in files:
        raise ValueError(f"{where}: a shape cites its uploaded boundary file as its first evidence")
    scheme = str(op.get("scheme") or "")
    if conn.execute("select from scheme where code = %s", [scheme]).fetchone() is None:
        raise ValueError(f"{where}: scheme {scheme!r} does not exist")
    field = str(op.get("field") or "")
    named = op.get("name_field")
    lang = None if named is None else _lang(op.get("lang"), f"{where}.lang")
    start = _day(op.get("valid_from"), f"{where}.valid_from")
    if start is None:
        raise ValueError(f"{where}: valid_from is required")
    end = _day(op.get("valid_to"), f"{where}.valid_to")
    if end is not None and end <= start:
        raise ValueError(f"{where}: valid_to must come after valid_from")
    quality = op.get("quality")
    if quality not in QUALITIES:
        raise ValueError(f"{where}: quality must be one of {', '.join(QUALITIES)}")
    excluded = {str(code) for code in op.get("exclude") or []}
    found = conn.execute("select storage_key from upload where id = %s", [files[source][0]]).fetchone()
    if found is None:
        raise ValueError(f"{where}: the boundary file is gone")
    try:
        features = boundaries.read(DATA / found[0])
    except boundaries.Unreadable as error:
        raise ValueError(f"{where}: {error}") from None
    if not features:
        raise ValueError(f"{where}: the boundary file has no polygons")
    if field not in features[0][0]:
        raise ValueError(f"{where}: the boundary file has no field {field!r}")
    if named is not None and named not in features[0][0]:
        raise ValueError(f"{where}: the boundary file has no field {named!r}")
    parts: dict[str, list[shapely.Geometry]] = {}
    labels: dict[str, str] = {}
    blank = 0
    for properties, geometry in features:
        code = properties.get(field, "")
        if not code:
            blank += 1
            continue
        if code in excluded:
            continue
        parts.setdefault(code, []).append(geometry)
        if named is not None and properties.get(named):
            labels.setdefault(code, unicodedata.normalize("NFC", properties[named]).strip())
    if blank:
        raise ValueError(f"{where}: {blank:,} features have no {field}; exclude them or pick another field")
    if not parts:
        raise ValueError(f"{where}: every feature is excluded")
    conn.execute("drop table if exists shape_in")
    conn.execute("create temp table shape_in (code text primary key, geom geometry(MultiPolygon, 4326)) on commit drop")
    with conn.cursor() as cursor, cursor.copy("copy shape_in (code, geom) from stdin") as copy:
        for code, pieces in parts.items():
            merged = pieces[0] if len(pieces) == 1 else boundaries.polygonal(shapely.union_all(pieces))
            copy.write_row((code, shapely.to_wkb(merged, hex=True, include_srid=False)))
    conn.execute("update shape_in set geom = st_setsrid(geom, 4326)")
    rows = conn.execute(
        """select s.code, a.id, encode(st_asewkb(s.geom), 'hex'),
                  encode(st_asewkb(st_pointonsurface(s.geom)), 'hex'),
                  exists (select from area_geometry g where g.area_id = a.id
                          and g.valid = daterange(%(start)s, %(end)s, '[)') and g.quality = %(quality)s::geometry_quality
                          and g.source_id = %(source)s and st_equals(g.geom, s.geom))
           from shape_in s
           left join (area a join area_code c on c.area_id = a.id and c.valid @> %(start)s::date)
             on a.scheme_code = %(scheme)s and c.code = s.code
           order by s.code""",
        {"start": start, "end": end, "quality": quality, "source": source, "scheme": scheme},
    ).fetchall()
    matched = [code for code, area, *_ in rows if area is not None]
    if len(matched) != len(set(matched)):
        twice = sorted({code for code in matched if matched.count(code) > 1})
        raise ValueError(f"{where}: codes {', '.join(twice[:LISTED])} match more than one {scheme} area")
    unmatched = [code for code, area, *_ in rows if area is None]
    if unmatched and named is None:
        raise ValueError(
            f"{where}: {len(unmatched):,} codes match no {scheme} area on {start}"
            f" ({', '.join(unmatched[:LISTED])}); add them as new areas or exclude them"
        )
    nameless = [code for code in unmatched if not labels.get(code)]
    if nameless:
        raise ValueError(f"{where}: new areas {', '.join(nameless[:LISTED])} have no {named}")
    held: dict[UUID, list[tuple[date | None, str, date | None, str, str, str | None, str, UUID]]] = {}
    for area, *row in conn.execute(
        """select area_id, valid_from, valid_from_precision::text, valid_to, valid_to_precision::text,
                  encode(st_asewkb(geom), 'hex'), encode(st_asewkb(label_point), 'hex'), quality::text, source_id
           from area_geometry where area_id = any(%s) and valid && daterange(%s, %s, '[)')""",
        [[area for _, area, *_ in rows if area is not None], start, end],
    ).fetchall():
        held.setdefault(area, []).append((row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7]))
    parent = _parents(conn, scheme, start) if unmatched else None
    changed = 0
    for code, area, geom, point, same in rows:
        if same:
            continue
        changed += 1
        if area is None:
            area = uuid7()
            cs.add("area", id=area, scheme_code=scheme)
            cs.add("area_code", area_id=area, code=code, valid_from=start, valid_to=end)
            cs.add(
                "name",
                entity_id=area,
                text=labels[code],
                lang=lang,
                kind="official",
                is_preferred=True,
                source_id=source,
            )
            if parent is not None:
                over = conn.execute(
                    """select a.id from area a join area_geometry g on g.area_id = a.id and g.valid @> %s::date
                       where a.scheme_code = %s and st_covers(g.geom, %s::geometry) limit 1""",
                    [start, parent, point],
                ).fetchone()
                if over is not None:
                    cs.add("area_parent", area_id=area, parent_id=over[0], valid_from=start, valid_to=end)
        for (
            held_from,
            from_precision,
            held_to,
            to_precision,
            held_geom,
            held_point,
            held_quality,
            held_source,
        ) in held.get(area, []):
            remainders = [
                *([(held_from, from_precision, start, "day")] if held_from is None or held_from < start else []),
                *(
                    [(end, "day", held_to, to_precision)]
                    if end is not None and (held_to is None or held_to > end)
                    else []
                ),
            ]
            for left, left_precision, right, right_precision in remainders:
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=left,
                    valid_from_precision=left_precision,
                    valid_to=right,
                    valid_to_precision=right_precision,
                    geom=held_geom,
                    label_point=held_point,
                    quality=held_quality,
                    source_id=held_source,
                )
        cs.add(
            "area_geometry",
            area_id=area,
            valid_from=start,
            valid_to=end,
            geom=geom,
            label_point=point,
            quality=quality,
            source_id=source,
        )
    if not changed:
        raise ValueError(f"{where}: every shape matches the archive's already")


def _parents(conn: Connection, scheme: str, on: date) -> str | None:
    found = conn.execute(
        """select p.scheme_code from area a join area_parent ap on ap.area_id = a.id and ap.valid @> %s::date
           join area p on p.id = ap.parent_id where a.scheme_code = %s
           group by 1 order by count(*) desc limit 1""",
        [on, scheme],
    ).fetchone()
    return None if found is None else str(found[0])


def compile(
    conn: Connection,
    payload: Payload,
    author: UUID,
    *,
    author_kind: AuthorKind = "user",
    kind: Kind = "edit",
    proposal: UUID | None = None,
    undefaulted: bool = False,
) -> Path:
    summary = payload.get("summary", "").strip()
    if not summary:
        raise ValueError("summary is required")
    ops = payload.get("ops") or []
    if not ops:
        raise ValueError("ops is empty")
    if conn.execute("select from users where id = %s", [author]).fetchone() is None:
        raise ValueError(f"author {author} is not a user")
    evidence = payload.get("evidence") or {}
    sources: dict[str, UUID] = {}
    cs = Changeset(conn, pipeline="contrib", version=VERSION, summary=summary, polity=payload.get("polity"), kind=kind)
    added: set[UUID] = set()
    proofs: dict[str, dict[str, str]] = {}
    files: dict[UUID, tuple[UUID, UUID, int]] = {}
    for key, item in evidence.items():
        if "source" in item:
            source = _uuid(item["source"], f"evidence {key}.source")
            if conn.execute("select from source where id = %s", [source]).fetchone() is None:
                raise ValueError(f"evidence {key}: source {source} does not exist")
            sources[key] = source
        elif "url" in item:
            sources[key], proofs[key] = _linked(conn, cs, item, author, f"evidence {key}", added)
        elif "upload" in item:
            sources[key], proofs[key] = _uploaded(conn, cs, item, author, f"evidence {key}", added, files)
        else:
            raise ValueError(f"evidence {key}: needs a source, a url, or an upload")

    cs.manifest["author_kind"] = author_kind
    cs.manifest["author_user_id"] = str(author)
    if payload.get("rationale"):
        cs.manifest["rationale"] = payload.get("rationale")
    if proposal is not None:
        cast(dict[str, object], cs.manifest["scope"])["proposal"] = str(proposal)

    refs: dict[str, UUID] = {}
    for n, op in enumerate(ops):
        where = f"ops[{n}]"
        if "ref" in op:
            if op.get("op") != "add":
                raise ValueError(f"{where}: only add takes a ref")
            if op["ref"] in refs:
                raise ValueError(f"{where}: ref {op['ref']!r} is used twice")
            refs[op["ref"]] = uuid7()

    targets: set[tuple[object, str]] = set()
    colored: set[UUID] = set()
    merging: set[UUID] = set()
    unlinked: set[UUID] = set()
    translated: set[tuple[str, str]] = set()
    shaped: set[str] = set()
    links: set[tuple[str, ...]] = set()
    dropped = {
        (str((op.get("key") or {}).get("scheme")), str((op.get("key") or {}).get("value")))
        for op in ops
        if op.get("op") == "drop" and op.get("table") == "external_id"
    }
    for n, op in enumerate(ops):
        where = f"ops[{n}]"
        cited = op.get("evidence") or []
        missing = [key for key in cited if key not in sources]
        if missing:
            raise ValueError(f"{where}: unknown evidence {', '.join(missing)}")
        target = (op.get("table"), str(op.get("id") or _dumps(op.get("key"))))
        if op.get("op") in ("set", "drop"):
            if target in targets:
                raise ValueError(f"{where}: {target[0]} {target[1]} is changed twice")
            targets.add(target)
        if op.get("op") == "drop":
            if op.get("table") == "party_relation":
                unlinked.add(_uuid(op.get("id"), f"{where}.id"))
            reason = unicodedata.normalize("NFC", str(op.get("reason") or summary)).strip()
            owner, label = _drop(conn, cs, op, where, reason)
            for key in cited:
                if owner is not None:
                    _cite(
                        cs,
                        owner,
                        sources[key],
                        evidence[key],
                        proofs.get(key, {}),
                        {"table": op.get("table"), "row": label, "op": "drop"},
                    )
            continue
        if op.get("table") == "party_color":
            if not cited:
                raise ValueError(f"{where}: a change to party_color needs evidence")
            party, label, changed = _color(conn, cs, op, where, sources[cited[0]])
            if party in colored:
                raise ValueError(f"{where}: change one color period per party in a proposal")
            colored.add(party)
            for key in cited:
                for column in changed:
                    _cite(
                        cs,
                        party,
                        sources[key],
                        evidence[key],
                        proofs.get(key, {}),
                        {"table": "party_color", "row": label, "column": column, "op": op.get("op")},
                    )
            continue
        if op.get("op") == "label":
            if not cited:
                raise ValueError(f"{where}: a label needs evidence")
            owner, label = _label(conn, cs, op, where, sources[cited[0]])
            for key in cited:
                _cite(
                    cs,
                    owner,
                    sources[key],
                    evidence[key],
                    proofs.get(key, {}),
                    {"table": "label", "row": label, "op": "add"},
                )
            continue
        if op.get("op") == "image":
            linked = None
            if cited:
                linked = str(evidence[cited[0]].get("url") or "") or None
                if linked is None:
                    row = conn.execute("select url from source where id = %s", [sources[cited[0]]]).fetchone()
                    linked = None if row is None else row[0]
            owner, label = _image(conn, cs, op, where, added, linked)
            for key in cited:
                _cite(
                    cs,
                    owner,
                    sources[key],
                    evidence[key],
                    proofs.get(key, {}),
                    {"table": op.get("table"), "row": label, "op": "image"},
                )
            continue
        if op.get("op") in ("results", "correct"):
            if not cited:
                raise ValueError(f"{where}: {op.get('op')} needs evidence")
            first = cited[0]
            if op.get("op") == "results":
                contest, result_set = _results(
                    conn,
                    cs,
                    op,
                    where,
                    sources[first],
                    files,
                    evidence[first],
                    proofs.get(first, {}),
                    added,
                    undefaulted,
                )
            else:
                corrected = _corrected(conn, op, where)
                contest, result_set = corrected["contest"], corrected["result_set"]
                if ("result_set", str(result_set)) in targets:
                    raise ValueError(f"{where}: result set {result_set} is corrected twice")
                targets.add(("result_set", str(result_set)))
                if corrected["pipeline"] == "contrib":
                    _restage(conn, cs, corrected, sources[first], evidence[first], proofs.get(first, {}))
                else:
                    cited = [first, *cited]
            for key in cited[1:]:
                _cite(
                    cs,
                    contest,
                    sources[key],
                    evidence[key],
                    proofs.get(key, {}),
                    {"table": "result_set", "row": str(result_set), "op": op.get("op")},
                )
            continue
        if op.get("op") == "election":
            if not cited:
                raise ValueError(f"{where}: a new election needs evidence")
            created = _election(conn, cs, op, where)
            for entity in created:
                for key in cited:
                    _cite(
                        cs,
                        entity,
                        sources[key],
                        evidence[key],
                        proofs.get(key, {}),
                        {"op": "election"},
                    )
            continue
        if op.get("op") == "lineage":
            if not cited:
                raise ValueError(f"{where}: a lineage link needs evidence")
            link = tuple(str(op.get(field)) for field in ("party", "other", "kind", "election_id"))
            if link in links:
                raise ValueError(f"{where}: this link is added twice")
            links.add(link)
            party = _lineage(conn, cs, op, where, sources[cited[0]], unlinked)
            for key in cited:
                _cite(
                    cs,
                    party,
                    sources[key],
                    evidence[key],
                    proofs.get(key, {}),
                    {"table": "party_relation", "op": "lineage"},
                )
            continue
        if op.get("op") == "merge":
            id = _uuid(op.get("id"), f"{where}.id")
            into = _uuid(op.get("into"), f"{where}.into")
            if id == into:
                raise ValueError(f"{where}: a record cannot merge into itself")
            if id in merging:
                raise ValueError(f"{where}: {id} is merged twice")
            types: dict[UUID, str] = {}
            for entity, field in ((id, "id"), (into, "into")):
                row = conn.execute(
                    """select type::text, retired_by is not null,
                              exists (select from entity_merge m where m.entity_id = e.id)
                       from entity e where id = %s""",
                    [entity],
                ).fetchone()
                if row is None:
                    raise ValueError(f"{where}.{field}: entity {entity} does not exist")
                if row[1] or entity in cs.retired:
                    raise ValueError(f"{where}.{field}: entity {entity} is retired")
                if row[2]:
                    raise ValueError(f"{where}.{field}: entity {entity} is already merged")
                types[entity] = row[0]
            if types[id] != types[into]:
                raise ValueError(f"{where}: a {types[id]} cannot merge into a {types[into]}")
            if cs.survivors.get(into, into) == id:
                raise ValueError(f"{where}: {into} already merges into {id}")
            merging.add(id)
            cs.merge(id, into)
            for key in cited:
                _cite(
                    cs,
                    cs.survivors.get(into, into),
                    sources[key],
                    evidence[key],
                    proofs.get(key, {}),
                    {"table": "entity_merge", "row": str(id), "op": "merge"},
                )
            continue
        if op.get("op") == "cite":
            if not cited:
                raise ValueError(f"{where}: a citation needs evidence")
            entity = _uuid(op.get("entity_id"), f"{where}.entity_id")
            entity = cs.survivors.get(entity, entity)
            if entity in cs.retired or conn.execute("select from entity where id = %s", [entity]).fetchone() is None:
                raise ValueError(f"{where}: entity {entity} does not exist")
            for key in cited:
                held = conn.execute(
                    "select from citation where entity_id = %s and source_id = %s", [entity, sources[key]]
                ).fetchone()
                if held is not None:
                    raise ValueError(f"{where}: this record already cites source {sources[key]}")
                _cite(cs, entity, sources[key], evidence[key], proofs.get(key, {}), {"op": "cite"})
            continue
        if op.get("op") == "translate":
            note = str((op.get("of") or {}).get("id"))
            if any(
                other.get("op") == "set" and other.get("table") == "note" and other.get("id") == note for other in ops
            ):
                raise ValueError(f"{where}: translate note {note} in a separate proposal from its edit")
            _translate(conn, cs, op, where, sources[cited[0]] if cited else None, translated)
            continue
        if op.get("op") == "shape":
            if not cited:
                raise ValueError(f"{where}: a shape needs its uploaded boundary file as evidence")
            if str(op.get("scheme")) in shaped:
                raise ValueError(f"{where}: upload one boundary file per scheme in a proposal")
            shaped.add(str(op.get("scheme")))
            _shape(conn, cs, op, where, sources[cited[0]], files)
            continue
        table = op.get("table", "")
        if table not in EDITABLE:
            raise ValueError(f"{where}: table {table!r} is not editable yet")
        columns = EDITABLE[table]
        values = dict(op.get("values") or {})
        unknown = values.keys() - columns.keys()
        if unknown:
            raise ValueError(f"{where}: {table} has no editable column {', '.join(sorted(unknown))}")
        for column, value in values.items():
            if isinstance(value, str) and value.startswith("$"):
                if value[1:] not in refs:
                    raise ValueError(f"{where}.{column}: unknown ref {value}")
                values[column] = refs[value[1:]]
            elif column == "lang":
                values[column] = _lang(value, f"{where}.values.lang")
            elif isinstance(value, str):
                values[column] = unicodedata.normalize("NFC", value).strip()
        if table == "note" and _graphemes(str(values.get("body_md") or "")) > NOTE_LIMIT:
            raise ValueError(f"{where}: a note is at most {NOTE_LIMIT:,} characters")
        if CITED[table] and not cited:
            raise ValueError(f"{where}: a change to {table} needs evidence")
        if table == "external_id":
            if op.get("op") != "add":
                raise ValueError(f"{where}: an external ID is added or dropped, never changed")
            scheme, value = values.get("scheme"), str(values.get("value") or "")
            pattern = REFERENCES.get(str(scheme))
            if pattern is None:
                raise ValueError(f"{where}: only {', '.join(REFERENCES)} IDs can be added")
            if not pattern.fullmatch(value):
                raise ValueError(f"{where}: {value!r} is not a {scheme} ID")
            held = conn.execute(
                "select entity_id from external_id where scheme = %s and value = %s", [scheme, value]
            ).fetchone()
            if held is not None:
                raise ValueError(f"{where}: {scheme} ID {value} is already on {held[0]}")
            other = conn.execute(
                "select value from external_id where scheme = %s and entity_id = %s",
                [scheme, _uuid(str(values.get("entity_id")), f"{where}.entity_id")],
            ).fetchone()
            if other is not None and (str(scheme), other[0]) not in dropped:
                raise ValueError(
                    f"{where}: this entity already has {scheme} ID {other[0]}; drop it in the same proposal"
                )

        match op.get("op"):
            case "add":
                absent = [column for column, required in columns.items() if required and values.get(column) is None]
                if absent:
                    raise ValueError(f"{where}: add {table} needs {', '.join(absent)}")
                row: dict[str, object] = (
                    values if table in KEYED else {"id": refs.get(op.get("ref", "")) or uuid7(), **values}
                )
                changed = [column for column in values if column != "entity_id"]
            case "set":
                id = _uuid(op.get("id"), f"{where}.id")
                current = conn.execute(
                    sql.SQL(
                        "select to_jsonb(t) - 'changeset_id' - 'recorded_from' - 'valid' from public.{} t where id = %s"
                    ).format(sql.Identifier(table)),
                    [id],
                ).fetchone()
                if current is None:
                    raise ValueError(f"{where}: {table} {id} does not exist")
                before = cast(dict[str, object], current[0])
                if not values:
                    raise ValueError(f"{where}: set needs values")
                changed = [column for column, value in values.items() if str(before.get(column)) != str(value)]
                if not changed:
                    raise ValueError(f"{where}: set changes nothing")
                row = before | values
                if table == "note" and "body_md" in changed:
                    _carry(conn, cs, str(before["body_md"]), str(row["body_md"]), str(row["lang"]))
            case other:
                raise ValueError(f"{where}: op {other!r} is not supported yet")

        entity = _uuid(str(row["entity_id"]), f"{where}.entity_id")
        entity = cs.survivors.get(entity, entity)
        if entity in cs.retired:
            raise ValueError(f"{where}: entity {entity} is retired")
        if conn.execute("select from entity where id = %s", [entity]).fetchone() is None:
            raise ValueError(f"{where}: entity {entity} does not exist")
        row["entity_id"] = entity
        if table in PROVENANCE and cited and (op.get("op") == "add" or "text" in changed):
            row[PROVENANCE[table]] = sources[cited[0]]
        cs.add(table, **row)
        for key in cited:
            for column in changed:
                _cite(
                    cs,
                    entity,
                    sources[key],
                    evidence[key],
                    proofs.get(key, {}),
                    {
                        "table": table,
                        "row": str(row["id"]) if "id" in row else ":".join(str(row[c]) for c in KEYED[table]),
                        "column": column,
                    },
                )
    return cs.write()


def compile_file(conn: Connection, path: Path, author: UUID, author_kind: AuthorKind) -> Path:
    return compile(conn, cast(Payload, json.loads(path.read_text())), author, author_kind=author_kind)


def _dumps(value: object) -> str:
    return json.dumps(value, default=str)


def _rows(folder: Path) -> int:
    return sum(pl.scan_parquet(path).select(pl.len()).collect().item() for path in folder.glob("*.parquet"))


def _gated(*args: str) -> None:
    subprocess.run(
        [
            "bash",
            str(DATA / "logs" / "gate.sh"),
            "systemd-run",
            "--user",
            "--wait",
            "--collect",
            "-p",
            "MemoryHigh=5G",
            "-p",
            "MemoryMax=6G",
            "-p",
            f"WorkingDirectory={ROOT / 'pipelines'}",
            "bash",
            "-lc",
            shlex.join(["uv", "run", "ea", *args]),
        ],
        check=True,
    )


def _dropped(conn: Connection, table: str, staged: sql.Composable) -> list[tuple[str, dict[str, object], str | None]]:
    if table == "row_drop":
        return [
            (relation, key, None if ROW_DROPS[relation] is None else str(key[ROW_DROPS[relation]]))
            for relation, key in conn.execute(
                sql.SQL("select relation, key from {} order by id").format(staged)
            ).fetchall()
        ]
    relation = table.removesuffix("_drop")
    return [
        (
            relation,
            {
                column: value
                for column, value in row.items()
                if column not in ("reason", "changeset_id", "recorded_from")
            },
            str(row["entity_id"]),
        )
        for (row,) in conn.execute(sql.SQL("select to_jsonb(s) from {} s").format(staged)).fetchall()
    ]


def _periods(conn: Connection, staged: sql.Composable) -> list[dict[str, object]]:
    compared = ("color", "valid_from", "valid_from_precision", "valid_to", "valid_to_precision", "source_id")
    after = [
        cast(dict[str, object], row)
        for (row,) in conn.execute(sql.SQL("select to_jsonb(s) from {} s").format(staged)).fetchall()
    ]
    before = [
        cast(dict[str, object], row)
        for (row,) in conn.execute(
            sql.SQL(
                """select distinct to_jsonb(p) from public.party_color p
                   join {} s on p.party_id = s.party_id and p.valid && daterange(s.valid_from, s.valid_to, '[)')"""
            ).format(staged)
        ).fetchall()
    ]

    def same(row: dict[str, object]) -> tuple[object, ...]:
        return (row["party_id"], *(row.get(column) for column in compared))

    kept = {same(row) for row in before} & {same(row) for row in after}
    after = [row for row in after if same(row) not in kept]
    replaced = {(row["party_id"], row["valid_from"]): row for row in before if same(row) not in kept}
    rows: list[dict[str, object]] = []
    for row in after:
        old = replaced.pop((row["party_id"], row["valid_from"]), None)
        rows.append(
            {
                "table": "party_color",
                "key": {
                    "party_id": row["party_id"],
                    "valid_from": row["valid_from"],
                    "valid_from_precision": row["valid_from_precision"],
                },
                "entity_id": row["party_id"],
                "op": "add" if old is None else "change",
                "fields": {
                    column: [None if old is None else old.get(column), row.get(column)]
                    for column in compared
                    if old is None or old.get(column) != row.get(column)
                },
            }
        )
    rows.extend(
        {
            "table": "party_color",
            "key": {
                "party_id": row["party_id"],
                "valid_from": row["valid_from"],
                "valid_from_precision": row["valid_from_precision"],
            },
            "entity_id": row["party_id"],
            "op": "drop",
            "fields": {column: [row.get(column), None] for column in compared if row.get(column) is not None},
        }
        for row in replaced.values()
    )
    return rows


def _graded(conn: Connection, stage: Stage, findings: dict[str, int]) -> int:
    if "result_set" not in stage.tables:
        return 3
    restaged = conn.execute(
        sql.SQL(
            """select exists (select from {sets} s join public.result_set p on p.id = s.id)
                   or exists (select from {values} v where not exists (select from {sets} s where s.id = v.result_set_id))"""
        ).format(
            sets=stage.staged("result_set"),
            values=stage.staged("result_value")
            if "result_value" in stage.tables
            else sql.SQL("(select null::uuid as result_set_id where false)"),
        )
    ).fetchone()
    if restaged is not None and restaged[0]:
        return 3
    sets = conn.execute(
        sql.SQL(
            """select s.kind = 'official_body' and s.url is not null
                      and exists (select from {artifacts} a where a.source_id = r.source_id),
                      exists (select from public.result_set p where p.election_id = r.election_id and p.is_default
                              and p.contest_id is not distinct from r.contest_id and p.stage = r.stage)
               from {sets} r join {sources} s on s.id = r.source_id"""
        ).format(
            artifacts=stage.either("source_artifact", "source_id"),
            sets=stage.staged("result_set"),
            sources=stage.either("source", "id", "kind", "url"),
        )
    ).fetchall()
    if any(held for _, held in sets):
        return 3
    if "outcome" in stage.tables or any(findings.values()) or not all(official for official, _ in sets):
        return 2
    return 1


def _undefault(conn: Connection, changeset_id: str, checks: object) -> bool:
    findings = cast(dict[str, int], (checks or {}).get("findings") or {}) if isinstance(checks, dict) else {}
    if not any(count for code, count in findings.items() if code.startswith("accounting.")):
        return False
    stage = _stage(conn, changeset_id)
    if "result_set" not in stage.tables:
        return False
    found = conn.execute(sql.SQL("select bool_or(is_default) from {}").format(stage.staged("result_set"))).fetchone()
    return found is not None and bool(found[0])


def _cells(conn: Connection, stage: Stage, limit: int) -> list[dict[str, object]]:
    located = "value_locator" in stage.tables
    rows = conn.execute(
        sql.SQL(
            """with changed as (
                 select s.result_set_id, s.unit_id, s.option_id, s.channel, s.measure, s.count_round,
                        p.value as before, s.value as after
                 from {values} s
                 join reporting_unit u on u.id = s.unit_id
                 left join contest_option o on o.id = s.option_id
                 left join public.result_set r on r.id = s.result_set_id
                 left join public.result_value p on p.result_set_k = r.k and p.unit_k = u.k
                      and p.option_k = coalesce(o.k, 0) and p.channel = s.channel and p.measure = s.measure
                      and p.count_round = s.count_round
                 where p.value is distinct from s.value
                 order by u.code, o.ballot_position nulls first, o.k nulls first, s.measure, s.channel, s.count_round
                 limit %s)
               select c.result_set_id, c.unit_id, c.option_id, c.channel, c.measure, c.count_round, c.before, c.after,
                      {unit}, case when o.id is null then null else {option} end, m.code, h.code, x.contest_id,
                      {locator}
               from changed c
               join reporting_unit u on u.id = c.unit_id
               left join contest_option o on o.id = c.option_id
               join measure m on m.id = c.measure
               join channel h on h.id = c.channel
               left join {sets} x on x.id = c.result_set_id
               {located}
               order by u.code, o.ballot_position nulls first, o.k nulls first, c.measure, c.channel, c.count_round"""
        ).format(
            values=stage.staged("result_value"),
            unit=UNIT_LABEL,
            option=OPTION_LABEL,
            sets=stage.either("result_set", "id", "contest_id"),
            locator=sql.SQL("l.page_id, l.x0, l.y0, l.x1, l.y1" if located else "null, null, null, null, null"),
            located=sql.SQL(
                """left join {} l on l.result_set_id = c.result_set_id and l.unit_id = c.unit_id
                        and l.option_id is not distinct from c.option_id and l.channel = c.channel
                        and l.measure = c.measure and l.count_round = c.count_round"""
            ).format(stage.staged("value_locator"))
            if located
            else sql.SQL(""),
        ),
        [limit],
    ).fetchall()
    cells: list[dict[str, object]] = []
    for (
        result_set,
        unit,
        option,
        channel,
        measure,
        count_round,
        before,
        after,
        unit_label,
        option_label,
        measure_code,
        channel_code,
        contest,
        page,
        x0,
        y0,
        x1,
        y1,
    ) in rows:
        cells.append(
            {
                "table": "result_value",
                "key": {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option,
                    "channel": channel,
                    "measure": measure,
                    "count_round": count_round,
                },
                "labels": {
                    "unit": unit_label,
                    "option": option_label,
                    "measure": measure_code,
                    "channel": channel_code,
                },
                "entity_id": contest,
                "op": "add" if before is None else "change",
                "fields": {"value": [None if before is None else float(before), float(after)]},
            }
            | (
                {}
                if page is None
                else {"locator": {"page_id": page, "region": None if x0 is None else [x0, y0, x1, y1]}}
            )
        )
    return cells


def _geometries(
    conn: Connection, stage: Stage
) -> tuple[list[dict[str, object]], dict[str, int], dict[str, list[dict[str, object]]], dict[str, object]]:
    conn.execute("drop table if exists shape_after, shape_present")
    conn.execute(
        sql.SQL(
            """create temp table shape_after on commit drop as
               select s.area_id, a.scheme_code as scheme, s.valid_from as day, s.valid_to, s.quality::text as quality,
                      s.geom, p.geom as before, p.quality::text as before_quality, p.valid_to as before_to,
                      coalesce((select n.text from {names} n where n.entity_id = s.area_id
                                order by n.is_preferred desc limit 1), c.code) as label,
                      c.code, st_area(s.geom::geography) as km
               from {staged} s
               join {areas} a on a.id = s.area_id
               left join public.area_geometry p on p.area_id = s.area_id and p.valid @> s.valid_from
               left join lateral (select x.code from {codes} x where x.area_id = s.area_id
                                  and daterange(x.valid_from, x.valid_to, '[)') @> s.valid_from limit 1) c on true
               where s.valid_from is not null and not exists (
                 select from public.area_geometry o where o.area_id = s.area_id
                 and o.valid && daterange(s.valid_from, s.valid_to, '[)')
                 and md5(st_asbinary(o.geom)) = md5(st_asbinary(s.geom)))"""
        ).format(
            staged=stage.staged("area_geometry"),
            areas=stage.either("area", "id", "scheme_code"),
            names=stage.either("name", "entity_id", "text", "is_preferred"),
            codes=stage.either("area_code", "area_id", "code", "valid_from", "valid_to"),
        )
    )
    conn.execute("create index on shape_after using gist (geom)")
    conn.execute(
        """create temp table shape_present on commit drop as
           select area_id, scheme, day, geom from shape_after
           union all
           select p.area_id, s.scheme, s.day, p.geom
           from (select scheme, day, st_extent(geom)::geometry as box from shape_after group by 1, 2) s
           join public.area a on a.scheme_code = s.scheme
           join public.area_geometry p on p.area_id = a.id and p.valid @> s.day and p.geom && s.box
           where not exists (select from shape_after x where x.area_id = p.area_id)"""
    )
    conn.execute("create index on shape_present using gist (geom)")
    floor = "greatest(1000, 0.0005 * least(st_area({0}::geography), st_area({1}::geography)))"
    parents = stage.either("area_parent", "area_id", "parent_id", "valid_from", "valid_to").as_string(conn)
    problems = conn.execute(
        cast(
            LiteralString,
            f"""select 'overlap', a.area_id, o.area_id, a.scheme, st_multi(st_collectionextract(st_intersection(a.geom, o.geom), 3))
            from shape_after a join shape_present o on o.scheme = a.scheme and o.day = a.day and o.area_id <> a.area_id
                 and st_intersects(a.geom, o.geom)
                 and not (o.area_id < a.area_id and exists (select from shape_after x where x.area_id = o.area_id))
            where st_area(st_intersection(a.geom, o.geom)::geography) > {floor.format("a.geom", "o.geom")}
            union all
            select 'gap', null, null, g.scheme, g.geom
            from (select b.scheme, (st_dump(st_difference(
                    st_union(b.before),
                    (select st_union(p.geom) from shape_present p where p.scheme = b.scheme and p.day = b.day
                     and p.geom && st_extent(b.before)::geometry)))).geom
                  from shape_after b where b.before is not null group by b.scheme, b.day) g
            where st_area(g.geom::geography) > 1000
            union all
            select 'parent', a.area_id, ap.parent_id, a.scheme, st_multi(st_collectionextract(st_difference(a.geom, pg.geom), 3))
            from shape_after a
            join {parents} ap on ap.area_id = a.area_id and daterange(ap.valid_from, ap.valid_to, '[)') @> a.day
            join public.area_geometry pg on pg.area_id = ap.parent_id and pg.valid @> a.day
            where st_area(st_difference(a.geom, pg.geom)::geography) > 0.01 * a.km""",
        )
    ).fetchall()
    findings: dict[str, int] = {}
    examples: dict[str, list[dict[str, object]]] = {}
    for kind, area, other, _, _ in problems:
        code = f"geometry.{kind}"
        findings[code] = findings.get(code, 0) + 1
        examples.setdefault(code, [])
        if len(examples[code]) < LISTED:
            examples[code].append({"entity_id": area, "result_set_id": None, "details": {"other": other}})
    rows: list[dict[str, object]] = [
        {
            "table": "area_geometry",
            "key": {"area_id": area, "valid_from": day},
            "entity_id": area,
            "op": "add" if before is None else "change",
            "fields": {
                column: values
                for column, values in {
                    "area_km2": [None if before is None else round(float(before) / 1e6, 2), round(float(km) / 1e6, 2)],
                    "quality": [before_quality, quality],
                    "valid_to": [before_to, valid_to],
                }.items()
                if before is None or column == "area_km2" or values[0] != values[1]
            },
        }
        for area, day, valid_to, quality, before, before_quality, before_to, km in conn.execute(
            """select area_id, day, valid_to, quality, st_area(before::geography), before_quality, before_to, km
               from shape_after order by label, area_id"""
        ).fetchall()
    ]
    features: list[str] = []
    schemes = [scheme for (scheme,) in conn.execute("select distinct scheme from shape_after").fetchall()]
    for scheme in schemes:
        projected = plane.sql("l.geom", scheme)
        features.extend(
            feature
            for (feature,) in conn.execute(
                cast(
                    LiteralString,
                    f"""with layers as (
                      select 'after' as kind, area_id, label, code,
                             case when before is null then 'new' else 'changed' end as status, geom
                      from shape_after where scheme = %(scheme)s
                      union all
                      select 'before', area_id, label, code, null, before from shape_after
                      where scheme = %(scheme)s and before is not null
                      union all
                      select 'moved', area_id, label, code, null,
                             st_multi(st_collectionextract(st_symdifference(geom, before), 3))
                      from shape_after where scheme = %(scheme)s and before is not null
                      union all
                      select (%(kinds)s::text[])[k], area_id, null, null, other::text, shape::geometry
                      from unnest(%(k)s::int[], %(areas)s::uuid[], %(others)s::uuid[], %(geoms)s::text[])
                           as t(k, area_id, other, shape)
                    ),
                    planar as (select l.*, {projected} as plane from layers l),
                    tolerance as (select greatest(1, sqrt(st_area(st_extent(plane)::geometry)) / 1500) as t from planar)
                    select json_build_object(
                      'type', 'Feature',
                      'properties', json_build_object('kind', kind, 'area', area_id, 'label', label, 'code', code,
                                                      'status', status),
                      'geometry', st_asgeojson(st_transform(st_setsrid(
                                    st_simplifypreservetopology(plane, (select t from tolerance)), 3857), 4326), 6)::json)::text
                    from planar where plane is not null and not st_isempty(plane)""",
                ),
                {
                    "scheme": scheme,
                    "kinds": ["overlap", "gap", "parent"],
                    "k": [("overlap", "gap", "parent").index(kind) + 1 for kind, *_, at, _ in problems if at == scheme],
                    "areas": [area for _, area, _, at, _ in problems if at == scheme],
                    "others": [other for _, _, other, at, _ in problems if at == scheme],
                    "geoms": [geom for *_, at, geom in problems if at == scheme],
                },
            ).fetchall()
        )
    tiles = [
        key
        for (key,) in conn.execute(
            """select distinct on (s.scheme) coalesce(b.tiles_key, b.scheme_code || '/' || b.vintage)
               from (select distinct scheme, day from shape_after) s
               join boundary_set b on b.scheme_code = s.scheme
               order by s.scheme, b.valid @> s.day desc, b.as_of desc"""
        ).fetchall()
        if (DATA / "plane" / f"{key}.pmtiles").exists()
    ]
    return (
        rows,
        findings,
        examples,
        {"type": "FeatureCollection", "tiles": tiles, "features": [json.loads(f) for f in features]},
    )


def _review(conn: Connection, changeset_id: str) -> tuple[int, dict[str, object], object]:
    stage = _stage(conn, changeset_id)
    shown: list[dict[str, object]] = []
    entities: set[str] = set()
    truncated = False
    found = conn.execute("select checks from changeset where id = %s", [changeset_id]).fetchone()
    checks = found and found[0]
    tiers = [TIERS.get(table, 2) for table in stage.tables if table not in ("row_drop", *RESULT_TABLES)]
    if any(table in stage.tables for table in RESULT_TABLES):
        findings = checks.get("findings") if isinstance(checks, dict) else None
        tiers.append(_graded(conn, stage, cast(dict[str, int], findings or {})))
    for table, columns in stage.tables.items():
        staged = stage.staged(table)
        if table in ("value_locator", "area_geometry"):
            continue
        if table == "result_value":
            cells = _cells(conn, stage, SHOWN - len(shown) + 1)
            entities.update(str(cell["entity_id"]) for cell in cells if cell["entity_id"] is not None)
            if len(shown) + len(cells) > SHOWN:
                truncated = True
                cells = cells[: SHOWN - len(shown)]
            shown.extend(cells)
            continue
        if table == "result_set":
            entities.update(
                str(contest)
                for (contest,) in conn.execute(
                    sql.SQL("select distinct contest_id from {} where contest_id is not null").format(staged)
                ).fetchall()
            )
        if table in ("row_drop", "name_drop", "note_drop", "external_id_drop"):
            for relation, key, owner in _dropped(conn, table, staged):
                if table == "row_drop":
                    tiers.append(ROW_DROP_TIERS.get(relation, 2))
                if owner is not None:
                    entities.add(owner)
                if len(shown) == SHOWN:
                    truncated = True
                    continue
                shown.append(
                    {
                        "table": relation,
                        "key": key,
                        "entity_id": owner,
                        "op": "drop",
                        "fields": {
                            column: [value, None]
                            for column, value in key.items()
                            if value is not None and column not in ("entity_id", "name_id", "note_id")
                        },
                    }
                )
            continue
        if table == "party_color":
            for row in _periods(conn, staged):
                entities.add(str(row["entity_id"]))
                if len(shown) == SHOWN:
                    truncated = True
                    break
                shown.append(row)
            continue
        if "entity_id" in columns:
            entities.update(
                str(entity)
                for (entity,) in conn.execute(sql.SQL("select distinct entity_id from {}").format(staged)).fetchall()
            )
        key = ("id",) if "id" in columns else KEYED.get(table, ())
        compared = [column for column in columns if column not in key]
        rows = conn.execute(
            sql.SQL("select to_jsonb(s), {} from {} s {} limit %s").format(
                sql.SQL("to_jsonb(p)") if key else sql.SQL("null::jsonb"),
                staged,
                sql.SQL("left join public.{} p using ({})").format(
                    sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, key))
                )
                if key
                else sql.SQL(""),
            ),
            [SHOWN - len(shown) + 1],
        ).fetchall()
        for after, before in rows:
            fields = {
                column: [None if before is None else before.get(column), after.get(column)]
                for column in compared
                if before is None or before.get(column) != after.get(column)
            }
            if not fields:
                continue
            if len(shown) == SHOWN:
                truncated = True
                break
            shown.append(
                {
                    "table": table,
                    "key": {column: after[column] for column in key},
                    "entity_id": after.get("entity_id", after.get("contest_id")),
                    "op": "add" if before is None else "change",
                    "fields": fields,
                }
            )
    diff: dict[str, object] = {"rows": shown, "truncated": truncated, "entities": sorted(entities)}
    if "area_geometry" in stage.tables:
        rows, findings, examples, drawn = _geometries(conn, stage)
        entities.update(str(row["entity_id"]) for row in rows)
        diff |= {"rows": [*rows, *shown], "entities": sorted(entities), "map": drawn}
        checks = cast(dict[str, object], checks or {})
        checks |= {
            "findings": cast(dict[str, int], checks.get("findings") or {}) | findings,
            "examples": cast(dict[str, object], checks.get("examples") or {}) | examples,
        }
    return max(tiers), diff, checks


def _apply(folder: Path) -> str:
    if _rows(folder) <= HEAVY:
        return apply(folder)
    _gated("apply", str(folder))
    with connect() as conn:
        status = conn.execute("select status::text from changeset where id = %s", [folder.name]).fetchone()
    return "unchanged" if status is None else str(status[0])


def _supersede(conn: Connection, changeset_id: str) -> None:
    conn.execute(
        sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(f"cs_{changeset_id.replace('-', '')}"))
    )
    conn.execute(
        "update changeset set status = 'superseded' where id = %s and status in ('draft', 'pending_review')",
        [changeset_id],
    )
    folder = DATA / "changesets" / changeset_id
    if folder.exists():
        folder.rename(DATA / "retired" / "changesets" / changeset_id)


def _activity(
    conn: Connection, proposal: UUID, kind: str, actor: UUID | None, changeset_id: object, summary: str
) -> None:
    conn.execute(
        """insert into activity (actor_user_id, agent_key_id, kind, proposal_id, changeset_id, entity_ids, polity_ids, summary)
           select coalesce(%s, p.author_user_id), case when %s::uuid is null then p.agent_key_id end, %s, p.id, %s,
                  coalesce((select array_agg(e::uuid) from proposal_revision r,
                                   jsonb_array_elements_text(r.diff -> 'entities') e
                            where r.proposal_id = p.id and r.changeset_id = %s), '{}'),
                  array_remove(array[p.polity_id], null), format(%s, p.number)
           from proposal p where p.id = %s""",
        [actor, actor, kind, changeset_id, changeset_id, summary, proposal],
    )


def _compiled(conn: Connection, proposal: UUID, payload: object, undefaulted: bool) -> Path:
    row = conn.execute(
        "select author_user_id, agent_key_id is not null from proposal where id = %s", [proposal]
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"proposal {proposal} has no author")
    folder = compile(
        conn,
        cast(Payload, payload),
        row[0],
        author_kind="agent" if row[1] else "user",
        proposal=proposal,
        undefaulted=undefaulted,
    )
    conn.commit()
    return folder


def _discard(conn: Connection, changeset_id: str) -> None:
    conn.execute(
        sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(f"cs_{changeset_id.replace('-', '')}"))
    )
    conn.execute("delete from changeset where id = %s and status = 'pending_review'", [changeset_id])
    conn.commit()
    folder = DATA / "changesets" / changeset_id
    if folder.exists():
        folder.rename(DATA / "retired" / "changesets" / changeset_id)


def _prepared(conn: Connection, proposal: UUID, payload: object) -> tuple[str, int, dict[str, object], object] | None:
    fold = _fold(conn, cast(Payload, payload))
    undefaulted = False
    while True:
        folder = _compiled(conn, proposal, payload, undefaulted)
        if _apply(folder) == "unchanged":
            return None
        tier, diff, checks = _review(conn, folder.name)
        if undefaulted or not _undefault(conn, folder.name, checks):
            break
        _discard(conn, folder.name)
        undefaulted = True
    if fold is None:
        return folder.name, tier, diff, checks
    rows = cast(list[dict[str, object]], fold["rows"])
    return (
        folder.name,
        3,
        diff
        | {
            "rows": [*rows, *cast(list[dict[str, object]], diff["rows"])],
            "entities": sorted({*cast(list[str], fold["entities"]), *cast(list[str], diff["entities"])}),
            "fold": fold["fold"],
        },
        checks,
    )


def _payload(conn: Connection, proposal: UUID, n: int | None) -> object:
    row = conn.execute(
        "select payload from proposal_revision where proposal_id = %s and n = %s"
        if n is not None
        else "select draft from proposal where id = %s",
        [proposal, n] if n is not None else [proposal],
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"proposal {proposal} has no {'revision ' + str(n) if n is not None else 'draft'}")
    return row[0]


def _captured(conn: Connection, proposal: UUID, n: int | None) -> object:
    payload = cast(Payload, _payload(conn, proposal, n))
    pending = {
        key: item for key, item in (payload.get("evidence") or {}).items() if "url" in item and "capture" not in item
    }
    if not pending:
        return payload
    found = conn.execute("select author_user_id from proposal where id = %s", [proposal]).fetchone()
    author = None if found is None else found[0]
    for key, item in pending.items():
        id, status, error = capture(conn, item["url"], author)
        conn.commit()
        if status != "captured":
            raise ValueError(f"evidence {key}: {item['url']} could not be archived: {error}")
        item["capture"] = str(id)
    conn.execute(
        "update proposal_revision set payload = %s where proposal_id = %s and n = %s"
        if n is not None
        else "update proposal set draft = %s where id = %s",
        [Jsonb(payload), proposal, n] if n is not None else [Jsonb(payload), proposal],
    )
    conn.commit()
    return payload


def _check(proposal: UUID, n: int | None, requested: UUID | None, output: dict[str, object]) -> dict[str, object]:
    with connect() as conn:
        prepared = _prepared(conn, proposal, _captured(conn, proposal, n))
        if prepared is None:
            return {"status": "unchanged"}
        changeset_id, tier, diff, checks = prepared
        _discard(conn, changeset_id)
    return {"status": "checked", "tier": tier, "diff": diff, "checks": checks}


def _submit(proposal: UUID, n: int | None, requested: UUID | None, output: dict[str, object]) -> dict[str, object]:
    if n is None:
        raise ValueError("a submit job needs revision_n")
    with connect() as conn:
        prepared = _prepared(conn, proposal, _captured(conn, proposal, n))
        if prepared is None:
            conn.execute(
                "update proposal_revision set checks = %s where proposal_id = %s and n = %s",
                [Jsonb({"unchanged": True}), proposal, n],
            )
            return {"status": "unchanged"}
        changeset_id, tier, diff, checks = prepared
        conn.execute(
            "update proposal_revision set changeset_id = %s, diff = %s, checks = %s where proposal_id = %s and n = %s",
            [changeset_id, Jsonb(diff, dumps=_dumps), Jsonb(checks, dumps=_dumps), proposal, n],
        )
        head = conn.execute("select head_changeset_id from proposal where id = %s", [proposal]).fetchone()
        previous = None if head is None else head[0]
        if previous is not None:
            _supersede(conn, str(previous))
        conn.execute(
            """update proposal set head_changeset_id = %s, tier = %s, state = 'open',
                      submitted_at = coalesce(submitted_at, now()), updated_at = now()
               where id = %s""",
            [changeset_id, tier, proposal],
        )
        _activity(
            conn,
            proposal,
            "proposal_submitted" if previous is None else "proposal_revised",
            None,
            changeset_id,
            "Proposal %s submitted" if previous is None else "Proposal %s revised",
        )
    return {"status": "pending_review", "changeset_id": changeset_id, "tier": tier}


def _decided(conn: Connection, proposal: UUID, requested: UUID | None, states: tuple[str, ...]) -> tuple[str, str]:
    if requested is None:
        raise ValueError("a decision needs requested_by")
    row = conn.execute(
        """select p.head_changeset_id::text, p.state::text, p.tier,
                  exists (select from user_role r where r.user_id = %s and r.role = 'admin'),
                  exists (select from user_role r where r.user_id = %s and r.role = 'reviewer'
                          and (r.polity_scope_id is null or r.polity_scope_id = p.polity_id))
           from proposal p where p.id = %s""",
        [requested, requested, proposal],
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"proposal {proposal} has no submitted revision")
    head, state, tier, admin, reviewer = row
    if state not in states:
        raise ValueError(f"proposal {proposal} is {state}, not {' or '.join(states)}")
    if not (admin or (reviewer and tier == 1)):
        raise ValueError(f"user {requested} may not decide a tier {tier} proposal")
    return head, state


def _close(conn: Connection, proposal: UUID, state: str, requested: UUID, reason: object) -> None:
    conn.execute(
        """update proposal set state = %s, decided_by = %s, decided_at = now(),
                  decision_reason = coalesce(%s, decision_reason), updated_at = now()
           where id = %s""",
        [state, requested, reason, proposal],
    )


def _folded(conn: Connection, head: str) -> dict[str, object] | None:
    row = conn.execute("select diff -> 'fold' from proposal_revision where changeset_id = %s", [head]).fetchone()
    return None if row is None else cast(dict[str, object] | None, row[0])


def _publish(proposal: UUID, n: int | None, requested: UUID | None, output: dict[str, object]) -> dict[str, object]:
    with connect() as conn:
        head, _ = _decided(conn, proposal, requested, ("open", "approved"))
        fold = _folded(conn, head)
        if fold is not None:
            _close(conn, proposal, "approved", cast(UUID, requested), output.get("reason"))
            number = conn.execute("select number from proposal where id = %s", [proposal]).fetchone()
            with (DATA / "logs" / "folds.tsv").open("a", encoding="utf-8") as log:
                log.writelines(
                    f"{datetime.now().isoformat(timespec='seconds')}\t{number and number[0]}\t{proposal}"
                    f"\t{held['pipeline']}\t{held['result_set']}\t{fold['cells']}\n"
                    for held in cast(list[dict[str, str]], fold["sets"])
                )
            return {"status": "awaiting_fold", "changeset_id": head}
        if _rows(DATA / "changesets" / head) > HEAVY:
            conn.commit()
            _gated("publish", head, "--reviewer", str(requested))
        else:
            publish(conn, head, str(requested))
        _close(conn, proposal, "published", cast(UUID, requested), output.get("reason"))
        _activity(conn, proposal, "proposal_published", requested, head, "Proposal %s published")
        sets = conn.execute(
            """select distinct bs.scheme_code || '/' || bs.vintage
               from area_geometry g join area a on a.id = g.area_id
               join boundary_set bs on bs.scheme_code = a.scheme_code and g.valid @> bs.as_of
               where g.changeset_id = %s""",
            [head],
        ).fetchall()
    for (boundary_set,) in sets:
        _gated("tiles", boundary_set)
        _gated("rasters", boundary_set)
    return {"status": "published", "changeset_id": head}


def _reverse(state: str, accepted: tuple[str, ...], activity: str, summary: str) -> Callable[..., dict[str, object]]:
    def run(proposal: UUID, n: int | None, requested: UUID | None, output: dict[str, object]) -> dict[str, object]:
        with connect() as conn:
            head, _ = _decided(conn, proposal, requested, accepted)
            stored = conn.execute("select decision_reason from proposal where id = %s", [proposal]).fetchone()
            reason = output.get("reason") or (stored and stored[0])
            if not reason:
                raise ValueError(f"a {state} decision needs a reason")
            if state == "reverted" and _folded(conn, head) is not None:
                raise ValueError(
                    "a folded correction lives in its pipeline's re-emit; propose a new correction instead"
                )
            if _rows(DATA / "changesets" / head) > HEAVY:
                conn.commit()
                _gated("revert", head, str(reason), "--reviewer", str(requested))
            else:
                revert(conn, head, str(reason), str(requested))
            _close(conn, proposal, state, cast(UUID, requested), reason)
            _activity(conn, proposal, activity, requested, head, summary)
        return {"status": state, "changeset_id": head}

    return run


def _withdraw(proposal: UUID, n: int | None, requested: UUID | None, output: dict[str, object]) -> dict[str, object]:
    with connect() as conn:
        row = conn.execute(
            """select p.author_user_id, p.state::text, p.head_changeset_id::text, c.status::text
               from proposal p left join changeset c on c.id = p.head_changeset_id
               where p.id = %s for update of p""",
            [proposal],
        ).fetchone()
        if row is None:
            raise ValueError(f"proposal {proposal} does not exist")
        author, state, head, status = row
        if requested is None or requested != author:
            raise ValueError("only the author can withdraw a proposal")
        if state == "published" or status == "published":
            raise ValueError("this proposal is already published; open a new proposal to change it")
        if state not in ("open", "changes_requested", "approved"):
            raise ValueError(f"proposal {proposal} is {state}, not open, changes_requested, or approved")
        reason = str(output.get("reason") or "withdrawn by the author")
        if head is not None and status in ("draft", "pending_review"):
            revert(conn, head, reason, str(requested))
        _close(conn, proposal, "withdrawn", requested, reason)
        _activity(conn, proposal, "proposal_withdrawn", requested, head, "Proposal %s withdrawn")
    return {"status": "withdrawn", "changeset_id": head}


JOBS: dict[str, Callable[[UUID, int | None, UUID | None, dict[str, object]], dict[str, object]]] = {
    "check": _check,
    "submit": _submit,
    "publish": _publish,
    "reject": _reverse(
        "rejected", ("open", "changes_requested", "approved"), "proposal_rejected", "Proposal %s rejected"
    ),
    "revert": _reverse("reverted", ("published",), "proposal_reverted", "Proposal %s reverted"),
    "withdraw": _withdraw,
}


def _notify(conn: Connection, job: object, proposal: object, kind: str, status: str) -> None:
    conn.execute(
        "select pg_notify('proposal_job', %s)",
        [_dumps({"id": job, "proposal_id": proposal, "kind": kind, "status": status})],
    )


def work() -> bool:
    with connect() as conn:
        claimed = conn.execute(
            """update proposal_job set status = 'running', started_at = now()
               where id = (select id from proposal_job where status = 'queued'
                           order by created_at for update skip locked limit 1)
               returning id, proposal_id, revision_n, kind::text, requested_by, coalesce(output, '{}'::jsonb)"""
        ).fetchone()
        if claimed is None:
            return False
        job, proposal, n, kind, requested, output = claimed
        _notify(conn, job, proposal, kind, "running")
    try:
        result = {**output, **JOBS[kind](proposal, n, requested, output)}
        status = "done"
    except Exception as error:
        traceback.print_exc()
        result = {**output, "error": f"{type(error).__name__}: {error}"}
        status = "failed"
    with connect() as conn:
        conn.execute(
            "update proposal_job set status = %s, finished_at = now(), output = %s where id = %s",
            [status, Jsonb(result, dumps=_dumps), job],
        )
        _notify(conn, job, proposal, kind, status)
    print(f"{kind} {proposal} {status}", flush=True)
    return True


def _folds() -> None:
    with connect() as conn:
        waiting = conn.execute(
            """select p.id, p.head_changeset_id::text, p.decided_by, r.diff -> 'fold', r.diff -> 'rows', c.id::text
               from proposal p
               join proposal_revision r on r.proposal_id = p.id and r.changeset_id = p.head_changeset_id
               join lateral (select c.id from changeset c
                             where c.pipeline <> 'contrib' and c.status = 'published'
                               and (c.scope -> 'proposal' = to_jsonb(p.id::text)
                                    or c.scope -> 'proposal' @> jsonb_build_array(p.id::text))
                             order by c.id desc limit 1) c on true
               where p.state = 'approved' and p.decided_by is not null and r.diff ? 'fold'
                 and (r.diff -> 'fold' ->> 'checked') is distinct from c.id::text"""
        ).fetchall()
        for proposal, head, reviewer, fold, rows, carried in waiting:
            missed = 0
            for row in rows[: fold["cells"]]:
                key = row["key"]
                held = conn.execute(
                    """select v.value from result_value v join result_set r on r.k = v.result_set_k
                       join reporting_unit u on u.k = v.unit_k
                       where r.id = %s and r.status = 'active' and u.id = %s
                         and v.option_k = coalesce((select o.k from contest_option o where o.id = %s), 0)
                         and v.channel = %s and v.measure = %s and v.count_round = %s""",
                    [
                        key["result_set_id"],
                        key["unit_id"],
                        key["option_id"],
                        key["channel"],
                        key["measure"],
                        key["count_round"],
                    ],
                ).fetchone()
                if held is None or float(held[0]) != row["fields"]["value"][1]:
                    missed += 1
            checked = {"checked": carried} | (
                {"published_by": carried} if missed == 0 else {"mismatch": {"changeset": carried, "cells": missed}}
            )
            conn.execute(
                "update proposal_revision set diff = jsonb_set(diff, '{fold}', (diff -> 'fold') || %s) where changeset_id = %s",
                [Jsonb(checked), head],
            )
            if missed:
                print(f"fold {proposal}: {carried} misses {missed} of {fold['cells']} cells", flush=True)
                conn.commit()
                continue
            conn.commit()
            publish(conn, head, str(reviewer))
            conn.execute("update proposal set state = 'published', updated_at = now() where id = %s", [proposal])
            _activity(conn, proposal, "proposal_published", reviewer, head, "Proposal %s published")
            conn.commit()
            print(f"fold {proposal}: published by {carried}", flush=True)


def serve() -> None:
    with connect(autocommit=True) as listener:
        listener.execute(
            """update proposal_job set status = 'failed', finished_at = now(),
                      output = coalesce(output, '{}'::jsonb) || '{"error": "interrupted by a worker restart"}'
               where status = 'running'"""
        )
        listener.execute("listen proposal_job")
        resume(listener)
        while True:
            sweep()
            _folds()
            while work() or process():
                pass
            for _ in listener.notifies(timeout=60, stop_after=1):
                pass


__all__: list[LiteralString] = ["compile", "compile_file", "serve", "work"]
