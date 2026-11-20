"""The Bangladesh Election Commission's results portal for the 12 February 2026 general election to the 13th Jatiya
Sangsad. The portal's API lists, for each district, the result sheet that each constituency's returning officer
sent ("বার্তা প্রেরণ শিট", the preliminary unofficial result), with the constituency's registered voters, valid
and cancelled votes, and centres, and the same figures for every polling centre, with the centre's own result
sheet. The sheets are scans, so each constituency sheet's candidates and votes were read by eye into
`portal2026.json`, each checked against the sheet's printed totals and the portal's figures. The centres' sheets
are too many to read this way; their turnout figures come from the portal's lists."""

import functools
import json
import time
import unicodedata
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import quote

from ea import raw

BASE = "http://103.183.38.66:81"
PAGE = f"{BASE}/election-result"
ELECTION = 178  # the portal's id of the general election and referendum of 12 February 2026
TRANSCRIPT = Path(__file__).with_name("portal2026.json")
LIMIT = 500  # the page size the portal's own export asks for
PAUSE = 0.5


def zillas_url() -> str:
    return f"{BASE}/api/zillas?electionID={ELECTION}"


def constituencies_url(zilla: str) -> str:
    return f"{BASE}/api/constituencies?electionID={ELECTION}&zillaID={zilla}"


def query(zilla: str, page: int) -> str:
    return f"election_type_id=1&election_id={ELECTION}&candidate_type=1&zilla_id={zilla}&page={page}&limit={LIMIT}"


def sheets_url(zilla: str) -> str:
    return f"{BASE}/api/bartasheet-list?{query(zilla, 1)}"


def centres_url(zilla: str, page: int) -> str:
    return f"{BASE}/api/center-wise-results?{query(zilla, page)}"


def document_url(path: str, filename: str) -> str:
    return f"{BASE}/api/document?path={quote(path, safe='')}&filename={quote(filename, safe='')}"


class Sheet(TypedDict):
    id: int
    zilla_id: str
    zilla_name: str
    constituency_id: str
    constituency_name: str
    total_center: int
    cancel_center: int
    total_submit_center: int
    total_voters: int
    cancel_total_vote: int
    absent_voter: int
    closed_center_total_voter: int
    clear_total_vote: int
    aro_barta_sheet: str | None
    ro_barta_sheet: str | None
    ro_filename: str | None
    aro_filename: str | None


class Centre(TypedDict):
    id: int
    v_c_id: int
    zilla_id: str
    upazilla_id: str | None
    upazilla_name: str | None
    union_id: str | None
    union_name: str | None
    municipality_id: str | None
    municipality_name: str | None
    constituency_id: str
    constituency_name: str
    inst_id: str
    center_id: str
    center_serial: str
    center_name: str
    clear_total_vote: int
    cancel_total_vote: int
    absent_voter: int
    total_voter: int
    vc_doc: str | None


def sheet_document(sheet: Sheet) -> str | None:
    """The address of a constituency's result sheet, as the portal's download link builds it."""
    path = sheet["ro_barta_sheet"] or sheet["aro_barta_sheet"]
    if not path:
        return None
    filename = sheet["ro_filename"] or sheet["aro_filename"] or f"bartasheet_{sheet['id']}.pdf"
    return document_url(path, filename)


def _json(source: str, url: str) -> object:
    known = url in raw.keys(source)
    stored = raw.fetch(source, url, suffix=".json")
    if not known:
        time.sleep(PAUSE)
    return json.loads(stored.path.read_bytes())


def _cached(source: str, url: str) -> object:
    return json.loads(raw.cached(source, url).path.read_bytes())


def fetch(source: str, documents: bool = True) -> None:
    """Walk the portal: the districts of the election, then each district's constituencies, its constituencies'
    result sheets, and its polling centres, page by page, and each constituency's sheet."""
    zillas = _json(source, zillas_url())
    assert isinstance(zillas, list)
    for zilla in zillas:
        z = str(zilla["zillaID"])
        _json(source, constituencies_url(z))
        _json(source, sheets_url(z))
        page = 1
        while True:
            found = _json(source, centres_url(z, page))
            assert isinstance(found, dict)
            if page * LIMIT >= int(found["total"]):
                break
            page += 1
    if documents:
        for sheet in sheets():
            if (url := sheet_document(sheet)) is not None and url not in raw.keys(source):
                raw.fetch(source, url, suffix=".pdf")
                time.sleep(PAUSE)


SOURCE = "bd.ecs_parliament"


@functools.cache
def zillas() -> list[dict[str, str]]:
    found = _cached(SOURCE, zillas_url())
    assert isinstance(found, list)
    return found


@functools.cache
def sheets() -> list[Sheet]:
    out: list[Sheet] = []
    for zilla in zillas():
        found = _cached(SOURCE, sheets_url(str(zilla["zillaID"])))
        assert isinstance(found, dict)
        if int(found["total"]) > LIMIT:
            raise ValueError(f"district {zilla['zillaID']}: more result sheets than one page")
        out.extend(found["data"])
    return out


@functools.cache
def centres() -> list[Centre]:
    out: list[Centre] = []
    for zilla in zillas():
        z, page = str(zilla["zillaID"]), 1
        while True:
            found = _cached(SOURCE, centres_url(z, page))
            assert isinstance(found, dict)
            out.extend(found["data"])
            if page * LIMIT >= int(found["total"]):
                break
            page += 1
    return out


@functools.cache
def constituencies() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for zilla in zillas():
        found = _cached(SOURCE, constituencies_url(str(zilla["zillaID"])))
        assert isinstance(found, list)
        out.extend(found)
    return out


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


class Row(TypedDict):
    name: str
    party: str
    votes: int


class Entry(TypedDict):
    number: int  # the constituency's number, as the sheet prints it
    name: str  # Bengali, as the sheet prints it
    candidates: list[Row]
    registered: int
    valid: int
    rejected: int
    cast: int
    centres: int
    reported: int
    suspended: int  # the centres where polling was stopped or postponed


@functools.cache
def transcript() -> dict[str, Any]:
    """The sheets read by eye: "parties", each party code with its name as the sheets print it, and "sheets", each
    sheet by the portal's constituency id, its candidates as [name, party code, votes]."""
    return json.loads(TRANSCRIPT.read_text())
