---
name: electionsarchive
description: Read electionsarchive.com, the sourced archive of election results, candidates, parties, and chambers. Use when looking up an election, contest, person, party, or chamber.
---

# electionsarchive.com for agents

The archive holds election results down to the smallest reporting unit, from each country's first election to its latest, plus the people, parties, chambers, and boundaries around them. Its values shape every record:

- **Official sources first.** An official canvass, gazette, or election body outranks an aggregator, and an aggregator outranks a news report.
- **Every number traced.** Each fact points to a source, and each change to the evidence behind it. A change without evidence is a guess.
- **Declared as declared.** Outcomes and seats are stored as the authority declared them, even when arithmetic disagrees.

## Connect

- **MCP**: `https://electionsarchive.com/mcp` (streamable HTTP). Read tools work without a key.
- **REST**: `https://electionsarchive.com/api/v1`, described at `https://electionsarchive.com/api/v1/openapi.json`. Every MCP tool has a REST twin.
- **Key**: reading needs none. A person can create one at `https://electionsarchive.com/settings/keys` and give it to you: with it you get higher rate limits, and they see your calls there. Send it as `x-api-key: ea_…` or `Authorization: Bearer ea_…`.

- **Pages**: every page also answers as Markdown or JSON. Add `.md` or `.json` to its URL. `https://electionsarchive.com/llms.txt` is the map.

## The data model in one page

- **Entities** have UUIDs. Every tool takes and returns UUIDs; `get_page_path` turns one into its page URL, and `https://electionsarchive.com/id/<uuid>` redirects to it.
- **Polity**: an institution that holds elections (a country, state, canton, or city). **Election**: an occasion on a date for a polity, grouping contests. **Contest**: one decision, such as an office, a district's seats, or a ballot question. **Option**: a ballot line (candidate, list, answer). **Candidacy**: a person standing as an option.
- **Result set**: one tabulation of an election or contest from one source at one stage (preliminary, official, certified, recount). Facts are keyed by reporting unit, option, measure (votes, registered, valid, …), and channel (election day, postal, …). **Outcome**: what was declared for an option.
- **Names** carry a kind (official, native, english, ballot, short, …), a language, and optional valid dates; one is preferred. **Notes** are Markdown with a kind (context, caveat, methodology, editorial) and a language. **Sources** are documents; **citations** tie a source to a record.
- **Valid time** is when something was true in the world, with a precision (day, month, year, unknown).

The archive launched on November 2, 2026, and is read-only for now: every page, REST endpoint, and MCP read tool works, and contributions open later.

## Reading

- **Find, then fetch.** `search` (pass `q`) finds any election, contest, person, party, chamber, or place by name; the `get_` tools read one record by UUID.
- **Places.** `list_polities` with no arguments lists the countries and the EU, 100 a page. Pass `parent` for the places directly below one, `kinds` to filter, and `cursor` (the last answer's `next`) for the following page.
- **Results.** `get_contest` gives a contest's totals; pass `by` for a breakdown by place and `unit` to look inside one place. For a national election, read one state's or region's own election, or one contest, instead of the whole election. `get_unmapped_units` lists the vote counts a contest's map can't draw (absentee and provisional units, precincts without shapes); past 4,000 such units it returns only the count, so pass `unit`.
- **Slow or large answers.** A first read of a large contest can take up to a minute. If a tool says the archive is still working, call it again a minute later: the answer will be ready. If a tool says an answer is too large, follow its hint to narrow the request.

## Election night

`get_live_results` (`GET /api/v1/live/results`) is the live board for a US general election; it defaults to the nearest one.

- `chambers` holds the Senate, the House, and governorships: seats won and called by each side, seats leading, seats not up, gains, losses, and, once forecasts are shown, the chance each side controls the chamber.
- `races` lists each race with its status (`won`, `called`, `unopposed`, `counting`, `waiting`, or `no_feed`), the share of the expected vote counted, the top candidates with votes and shares, the margin, the winner or projected winner, the favorite's win chance, the party that held the seat, and whether it flipped.
- Filter with `office` (`senate`, `house`, `governor`, `other`), `state` (two letters), and `status`; sort with `sort=closest` for the tightest races or `sort=updated` for the latest counts; page with `limit` and `cursor`. `election.pollsClose` gives each state's poll closing time.

`get_live_race` (`GET /api/v1/live/race`) reads one race in full: pass `race`, the `path` from `get_live_results`. It adds every candidate, the turnout estimate with its range, the source, and, with `places=true`, the count in each county.

Counts and calls come from the live feed and change all night, and win chances are model estimates. Say how much of the vote is counted, and never report a race as won before it is called. A race with status `unopposed` has one candidate per seat: `winner` names who takes the seat, `calledAt` stays empty, and its seats count as `unopposed` in `chambers`, not as `won` or `called`. After certification, the official results are in `get_contest`.
