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

- **Pages**: every page also answers as Markdown or JSON. Add `.md` or `.json` to its URL. `https://electionsarchive.com/llms.txt` is the map.

## The data model in one page

- **Entities** have UUIDs. Every tool takes and returns UUIDs; `get_page_path` turns one into its page URL, and `https://electionsarchive.com/id/<uuid>` redirects to it.
- **Polity**: an institution that holds elections (a country, state, canton, or city). **Election**: an occasion on a date for a polity, grouping contests. **Contest**: one decision, such as an office, a district's seats, or a ballot question. **Option**: a ballot line (candidate, list, answer). **Candidacy**: a person standing as an option.
- **Result set**: one tabulation of an election or contest from one source at one stage (preliminary, official, certified, recount). Facts are keyed by reporting unit, option, measure (votes, registered, valid, …), and channel (election day, postal, …). **Outcome**: what was declared for an option.
- **Names** carry a kind (official, native, english, ballot, short, …), a language, and optional valid dates; one is preferred. **Notes** are Markdown with a kind (context, caveat, methodology, editorial) and a language. **Sources** are documents; **citations** tie a source to a record.
- **Valid time** is when something was true in the world, with a precision (day, month, year, unknown).

Contributions open after launch. Until then the archive is read-only: every page, REST endpoint, and MCP read tool works, and write tools return an error.
