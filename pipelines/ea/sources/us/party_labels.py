import difflib
import re
import tarfile
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import legislators
from ea.store import connect

SOURCE = "us.party_labels"
VERSION = "1"
COMMIT = "13e2f82692c76e3695f6cd609a5e710d6cab5e26"
ARCHIVE = f"https://codeload.github.com/mapping-elections/nnv-xml/tar.gz/{COMMIT}"
PUBLISHER = "American Antiquarian Society and Tufts University"
TITLE = "A New Nation Votes: American Election Returns, 1787-1825"
LANDING = "https://elections.lib.tufts.edu/"
LICENSE = "CC0 1.0"
LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
NS = {"a": "http://dca.tufts.edu/aas"}
NATIONAL = {"Republican": "democratic-republican-party", "Federalist": "federalist-party", "Jackson": "jacksonian"}
UNAFFILIATED = {"Independent", "Pro-Slavery", "Anti-Slavery"}


def fetch() -> None:
    raw.fetch(SOURCE, ARCHIVE, suffix=".tar.gz")


def parse() -> None:
    pass


def _words(text: str) -> list[str]:
    plain = re.sub(r"[^a-z ]", "", unicodedata.normalize("NFKD", re.sub(r"\(.*\)", "", text)).lower())
    return [word for word in plain.split() if word not in ("jr", "sr")]


def emit() -> Path:
    archive = raw.cached(SOURCE, ARCHIVE)
    annv: dict[str, list[dict[str, str | int | list[str] | None]]] = {}
    with tarfile.open(archive.path) as tar:
        for member in tar.getmembers():
            path = member.name.partition("/")[2]
            if not member.isfile() or not re.fullmatch(r"[^/]+/182[45]/[^/]+\.xml", path):
                continue
            record = ET.parse(tar.extractfile(member)).getroot()
            if not any(
                office.get("name") == "U.S. House of Representatives" for office in record.iterfind("a:office", NS)
            ):
                continue
            overview = record.find(".//a:overview", NS)
            totals = {
                summary.get("candidate_ref"): summary.get("vote_total")
                for summary in record.iterfind(".//a:overview/a:candidate_summary", NS)
            }
            images = [
                reference.get("urn")
                for reference in (overview.iterfind("a:reference", NS) if overview is not None else [])
                if reference.get("type") == "page_image"
            ]
            surnames = {
                words[-1]
                for candidate in record.iterfind(".//a:ballot/a:candidate", NS)
                if (words := _words(candidate.get("name", "")))
            }
            for candidate in record.iterfind(".//a:ballot/a:candidate", NS):
                affiliation = candidate.get("affiliation")
                votes = totals.get(candidate.get("candidate_num"))
                annv.setdefault(record.get("election_id", "")[:2].upper(), []).append(
                    {
                        "file": path,
                        "election": record.get("election_id"),
                        "handle": record.get("handle"),
                        "surnames": sorted(surnames),
                        "id": candidate.get("name_id"),
                        "name": candidate.get("name"),
                        "affiliation": None if affiliation in (None, "null") else affiliation,
                        "votes": int(float(votes)) if votes not in (None, "null", "") else None,
                        "images": images,
                    }
                )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Party labels for THEA's unlabeled House candidates of 1824 to 1833: winners from congress-legislators, "
            "losers of 1824 and 1825 from A New Nation Votes",
            polity="US",
            kind="correction",
        )
        roster = cs.source(legislators.PUBLISHER, legislators.TITLE)
        if roster in cs.minted:
            raise ValueError("no congress-legislators source")
        nnv = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=nnv,
            kind="academic",
            publisher=PUBLISHER,
            title=TITLE,
            url=LANDING,
            license=LICENSE,
            license_url=LICENSE_URL,
        )
        cs.artifact(archive, nnv)
        rows = conn.execute(
            """with keyed as materialized (
                 select x.entity_id id, x.value key from external_id x
                 where x.scheme = 'us.contest' and x.value like any (array['182%%', '183%%'])
                   and x.value like '%%:house:%%' and left(x.value, 4)::int between 1824 and 1833),
               sets as materialized (
                 select r.id, r.k, keyed.id contest, keyed.key from result_set r
                 join keyed on keyed.id = r.contest_id and r.is_default)
               select o.id, o.contest_id, o.kind, o.parent_option_id, o.alliance_id, o.ballot_position,
                      o.ballot_label, sets.key,
                      (select sum(v.value)::bigint from result_value v
                       where v.result_set_k = sets.k and v.option_k = o.k and v.measure = 1),
                      exists (select from outcome oc where oc.result_set_id = sets.id
                              and oc.option_id = coalesce(o.parent_option_id, o.id) and oc.kind = 'elected'),
                      (select ta.party_id from tenure t
                       join candidacy cd on cd.person_id = t.person_id
                         and cd.option_id = coalesce(o.parent_option_id, o.id) and cd.role = 'head'
                       join tenure_affiliation ta on ta.tenure_id = t.id and ta.kind = 'party'
                       where t.contest_id = o.contest_id order by ta.valid_from limit 1),
                      (select b.value || ':' || t.from_date from tenure t
                       join candidacy cd on cd.person_id = t.person_id
                         and cd.option_id = coalesce(o.parent_option_id, o.id) and cd.role = 'head'
                       join external_id b on b.entity_id = t.person_id and b.scheme = 'bioguide'
                       where t.contest_id = o.contest_id limit 1)
               from contest_option o join sets on sets.contest = o.contest_id
               where o.kind = 'candidate' and o.party_id is null
                 and not exists (select from contest_option ch where ch.parent_option_id = o.id)"""
        ).fetchall()
        rivals: dict[UUID, set[str]] = {}
        for contest, label in conn.execute(
            """select o.contest_id, o.ballot_label from contest_option o
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
               where x.value like any (array['1824%%', '1825%%']) and x.value like '%%:house:%%' and o.kind = 'candidate'"""
        ).fetchall():
            if words := _words(label or ""):
                rivals.setdefault(contest, set()).add(words[-1])
        tally: Counter[tuple[str, str]] = Counter()
        created: set[str] = set()
        for option, contest, kind, parent, alliance, position, label, key, votes, won, seated, term in rows:
            year = key[:4]
            state = key.split(":")[1]
            party, cited, locator = None, None, None
            if won:
                if seated is not None and term is not None:
                    bioguide, _, start = term.partition(":")
                    party, cited, locator = seated, roster, {"bioguide": bioguide, "term_start": start}
            elif year in ("1824", "1825"):
                words = _words(label or "")
                hits = [
                    entry
                    for entry in annv.get(state, [])
                    if words
                    and (found := _words(str(entry["name"])))
                    and difflib.SequenceMatcher(None, words[-1], found[-1]).ratio() >= 0.8
                ]
                exact = [entry for entry in hits if votes is not None and entry["votes"] == votes]
                if exact:
                    hits = exact
                else:
                    hits = [
                        entry
                        for entry in hits
                        if len(
                            {
                                name
                                for name in entry["surnames"]
                                if any(
                                    difflib.SequenceMatcher(None, name, other).ratio() >= 0.8
                                    for other in rivals[contest]
                                )
                            }
                        )
                        >= 2
                    ]
                if len({entry["id"] for entry in hits}) > 1 and len(words) > 1:
                    hits = [entry for entry in hits if _words(str(entry["name"]))[0][:1] == words[0][:1]]
                printed = {entry["affiliation"] for entry in hits}
                if len({entry["id"] for entry in hits}) == 1 and len(printed) == 1 and None not in printed:
                    affiliation = str(printed.pop())
                    if affiliation not in UNAFFILIATED:
                        entry = hits[0]
                        party_key = NATIONAL.get(affiliation, f"{state}:{slug(affiliation)}")
                        party = cs.key("us.party", party_key)
                        if party in cs.minted and party_key not in created:
                            created.add(party_key)
                            cs.add("party", id=party, kind="party", polity_id=cs.polity(f"US-{state}"))
                            cs.add(
                                "name", entity_id=party, text=affiliation, lang="en", kind="official", is_preferred=True
                            )
                        cited = nnv
                        locator = {
                            "file": entry["file"],
                            "election_id": entry["election"],
                            "handle": entry["handle"],
                            "name_id": entry["id"],
                            "affiliation": affiliation,
                            **({"page_image": entry["images"]} if entry["images"] else {}),
                        }
            outcome = "winner" if won else "loser"
            if party is None:
                tally[(year, f"{outcome} unlabeled")] += 1
                continue
            tally[(year, f"{outcome} labeled")] += 1
            cs.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind=kind,
                parent_option_id=parent,
                party_id=party,
                alliance_id=alliance,
                ballot_position=position,
                ballot_label=label,
            )
            cs.add("citation", entity_id=option, source_id=cited, locator=locator)
        for (year, what), count in sorted(tally.items()):
            print(year, what, count)
        return cs.write()
