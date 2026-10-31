---
name: electionsarchive
description: Read<!-- write --> and improve<!-- /write --> electionsarchive.com, the sourced archive of election results, candidates, parties, and chambers. Use when looking up an election, contest, person, party, or chamber<!-- write -->, or when proposing a fix or addition to the archive with evidence<!-- /write -->.
---

# electionsarchive.com for agents

The archive holds election results down to the smallest reporting unit, from each country's first election to its latest, plus the people, parties, chambers, and boundaries around them. Its values<!-- read --> shape every record<!-- /read --><!-- write --> decide what a good proposal looks like<!-- /write -->:

- **Official sources first.** An official canvass, gazette, or election body outranks an aggregator, and an aggregator outranks a news report.
- **Every number traced.** Each fact points to a source, and each change to the evidence behind it. A change without evidence is a guess.
- **Declared as declared.** Outcomes and seats are stored as the authority declared them, even when arithmetic disagrees.<!-- write --> Report the disagreement in a note; never "fix" a declared outcome to match a count.<!-- /write -->

## Connect

- **MCP**: `{{origin}}/mcp` (streamable HTTP). Read tools work without a key.
- **REST**: `{{origin}}/api/v1`, described at `{{origin}}/api/v1/openapi.json`. Every MCP tool has a REST twin.
<!-- write -->
- **Key**: a person creates one at `{{origin}}/settings/keys` and gives it to you. Send it as `x-api-key: ea_…` or `Authorization: Bearer ea_…`. You act as that person: they are credited and accountable for what you submit, so work as carefully as they would.
<!-- /write -->
- **Pages**: every page also answers as Markdown or JSON. Add `.md` or `.json` to its URL. `{{origin}}/llms.txt` is the map.

## The data model in one page

- **Entities** have UUIDs. Every tool takes and returns UUIDs; `get_page_path` turns one into its page URL, and `{{origin}}/id/<uuid>` redirects to it.
- **Polity**: an institution that holds elections (a country, state, canton, or city). **Election**: an occasion on a date for a polity, grouping contests. **Contest**: one decision, such as an office, a district's seats, or a ballot question. **Option**: a ballot line (candidate, list, answer). **Candidacy**: a person standing as an option.
- **Result set**: one tabulation of an election or contest from one source at one stage (preliminary, official, certified, recount). Facts are keyed by reporting unit, option, measure (votes, registered, valid, …), and channel (election day, postal, …). **Outcome**: what was declared for an option.
- **Names** carry a kind (official, native, english, ballot, short, …), a language, and optional valid dates; one is preferred. **Notes** are Markdown with a kind (context, caveat, methodology, editorial) and a language. **Sources** are documents; **citations** tie a source to a record.
- **Valid time** is when something was true in the world, with a precision (day, month, year, unknown).

<!-- write -->
Today contributors can change names, notes, external IDs (Wikidata, Bioguide, MNIS, ParlGov), party colors, party logos and portraits from Wikimedia Commons, citations, note translations, and labels (a contest where no candidate qualified, or an assessed integrity or recognition label on an election, polity, or area); can add a contest's results from a document, with outcomes and the page region of every figure; can correct figures in a published result set; and can drop rows from several linking tables. Merges, retirements, and new elections and contests are coming; until then, describe such a problem in a note proposal's rationale.
<!-- /write -->
<!-- read -->
Contributions open after launch. Until then the archive is read-only: every page, REST endpoint, and MCP read tool works, and write tools return an error.
<!-- /read -->

<!-- write -->
## Evidence

Every fact you add or change cites evidence. A proposal declares its evidence once, in a map from your own short keys to evidence items, and each op lists the keys it relies on.

| Form   | Use it when                                           | Shape                                                                    |
| ------ | ----------------------------------------------------- | ------------------------------------------------------------------------ |
| URL    | The document is online                                | `{"url", "title", "publisher", "kind", "quote"?, "page"?}`               |
| Upload | You hold a file, or the site blocks automated fetches | `{"upload", "title", "publisher", "kind", "page"?, "region"?, "quote"?}` |
| Source | The archive already holds the source                  | `{"source", "locator"?, "quote"?}`                                       |

- `title`, `publisher`, and `kind` are required for URLs and uploads. `kind` is one of `official_body`, `aggregator`, `compendium`, `news_agency`, `academic`, or `community`.
- `quote` is the exact text or figure as printed. Reviewers check it against the stored copy, so copy it character for character.
- The archive fetches and stores each URL (and asks the Wayback Machine for a copy) before the checks run. If the fetch fails, the check fails with the reason.
- **Marking a region.** Upload the file with `upload_document`, poll `get_upload` until `status` is `ready`, read the page with `get_upload_page`, then give `page` (1-based) and `region` as `[x0, y0, x1, y1]`, fractions of the page measured from its top left, boxing exactly the cited lines.

## Finding sources

- Look for the authority's own publication: the election body's results page or PDF, the official gazette, or the legislature's records. Use aggregators and news only when no official record survives, and say so in the rationale.
- Search in the language of the polity, with its own terms for the canvass and the office.
- When a site refuses automated fetches (403, a challenge, or a login), upload a copy you or your person obtained legitimately, or name the source in the rationale. Leave its protections alone: no proxies, spoofed agents, or rotated addresses.
- Logos and portraits come only from Wikimedia Commons under public domain, CC0, CC BY, or CC BY-SA.

## Workflow

Each step ends when its condition holds.

1. **Orient.** Call `whoami`. Done when you know whose key this is, its roles, and its write limits.
2. **Look.** Find the record with `search` and the `get_` tools, then call `get_editable` for its editable rows and their IDs. For a contest, its `results` lists the ballot options and the active result sets; `get_result_cells` pages through a set's figures with each unit's ID, scheme, and code. Done when you hold the UUID of every row you will touch.
3. **Check for duplicates.** Call `find_open_proposals` with the entity. Done when no open proposal already makes your change; if one does, stop or build on it.
4. **Learn the ops.** Call `describe_ops`, with `table` for the table you will touch. Done when every op you plan matches a listed shape.
5. **Draft.** Call `draft_proposal` with a summary, a rationale, the ops, and the evidence. It compiles and checks the change and waits up to 60 seconds. Done when the latest job's `status` is `done` with no error; otherwise fix the payload with `revise_proposal` and repeat.
6. **Read the result.** Read the job's `diff` and `checks`. Done when every changed row in the diff is one you meant to change and every finding is explained in the rationale or fixed.
7. **Submit.** Call `submit_proposal`. It becomes revision 1 and enters review.
8. **Follow up.** Poll `get_proposal`. If review asks for changes, answer with `revise_proposal`, which adds a revision and resubmits it. Done when the proposal is `published`, `rejected`, or `withdrawn`.

## Op reference

Generated from `describe_ops`. Every op takes an `evidence` array of keys unless its table says otherwise.

{{ops}}

## Worked examples

Each payload goes to `draft_proposal`. IDs here are placeholders; take real ones from `get_editable`.

**Fix a misspelled name.**

```json
{
  "summary": "Correct the spelling of Jane Smyth's name",
  "rationale": "The certified canvass spells it Smyth; the archive has Smith.",
  "ops": [
    {
      "op": "set",
      "table": "name",
      "id": "<name id>",
      "values": { "text": "Jane Smyth" },
      "evidence": ["canvass"]
    }
  ],
  "evidence": {
    "canvass": {
      "url": "https://sos.example.gov/2022/canvass.pdf",
      "title": "2022 General Election Official Canvass",
      "publisher": "Example Secretary of State",
      "kind": "official_body",
      "page": 14,
      "quote": "Jane Smyth"
    }
  }
}
```

**Add a note with a primary citation.**

```json
{
  "summary": "Note the 2019 recount in District 4",
  "ops": [
    {
      "op": "add",
      "table": "note",
      "values": {
        "entity_id": "<contest id>",
        "kind": "context",
        "lang": "en",
        "body_md": "A recount on 2019-11-20 confirmed the result."
      },
      "evidence": ["order"]
    }
  ],
  "evidence": {
    "order": {
      "url": "https://elections.example.gov/2019/recount-order",
      "title": "Recount order, District 4",
      "publisher": "Example Board of Elections",
      "kind": "official_body",
      "quote": "the recount confirmed the result"
    }
  }
}
```

**Add a party color with its source.**

```json
{
  "summary": "Add the Example Party's color from its brand guide",
  "ops": [
    {
      "op": "add",
      "table": "party_color",
      "values": {
        "party_id": "<party id>",
        "color": "#1a2b3c",
        "valid_from": "2018-01-01"
      },
      "evidence": ["guide"]
    }
  ],
  "evidence": {
    "guide": {
      "url": "https://example-party.org/brand",
      "title": "Brand guidelines",
      "publisher": "Example Party",
      "kind": "official_body",
      "quote": "#1A2B3C"
    }
  }
}
```

**Translate a note.** Machine output is always `"origin": "machine"`, whoever runs the machine. Translate in its own proposal, separate from any edit to the note.

```json
{
  "summary": "Translate the District 4 recount note into Turkish",
  "ops": [
    {
      "op": "translate",
      "of": { "table": "note", "id": "<note id>" },
      "lang": "tr",
      "body": "20 Kasım 2019'daki yeniden sayım sonucu doğruladı.",
      "origin": "machine"
    }
  ]
}
```

**Add a party logo.**

```json
{
  "summary": "Add the Example Party's logo",
  "ops": [
    {
      "op": "image",
      "table": "party_image",
      "values": {
        "party_id": "<party id>",
        "file": "File:Example Party logo.svg"
      }
    }
  ]
}
```

**Add county results from a PDF canvass.** Upload the PDF, read the page, and box each figure. Units can be UUIDs or `{scheme, code}`; `page` defaults to the evidence's page.

```json
{
  "summary": "Add the certified county results for the 1996 Example County Commission race",
  "rationale": "The archive holds no results for this contest. The county's certified canvass prints both candidates in every district.",
  "ops": [
    {
      "op": "results",
      "contest_id": "<contest id>",
      "stage": "certified",
      "as_of": "1996-11-22",
      "rows": [
        {
          "unit": { "scheme": "us.county", "code": "01001" },
          "option": "<option id>",
          "measure": "votes",
          "value": 5120,
          "region": [0.41, 0.22, 0.49, 0.24]
        },
        {
          "unit": { "scheme": "us.county", "code": "01001" },
          "option": "<option id>",
          "measure": "votes",
          "value": 4870,
          "region": [0.52, 0.22, 0.6, 0.24]
        },
        {
          "unit": { "scheme": "us.county", "code": "01001" },
          "measure": "ballots_cast",
          "value": 10112,
          "region": [0.65, 0.22, 0.73, 0.24]
        }
      ],
      "outcomes": [{ "option": "<option id>", "kind": "elected" }],
      "evidence": ["canvass"]
    }
  ],
  "evidence": {
    "canvass": {
      "upload": "<upload id>",
      "title": "Official Canvass, General Election, November 5, 1996",
      "publisher": "Example County Board of Elections",
      "kind": "official_body",
      "page": 3
    }
  }
}
```

**Correct a misread figure.** Read the current figures with `get_result_cells` first. A correction to a set the archive's own pipelines loaded is folded into that pipeline's next run after approval, so the proposal shows as awaiting fold until the rerun matches.

```json
{
  "summary": "Correct Jane Smyth's vote in Precinct 12",
  "rationale": "The archive has 1,034; the certified canvass prints 1,304.",
  "ops": [
    {
      "op": "correct",
      "result_set": "<result set id>",
      "cells": [
        {
          "unit": "<unit id>",
          "option": "<option id>",
          "measure": "votes",
          "value": 1304
        }
      ],
      "evidence": ["canvass"]
    }
  ],
  "evidence": {
    "canvass": {
      "url": "https://sos.example.gov/2022/canvass.pdf",
      "title": "2022 General Election Official Canvass",
      "publisher": "Example Secretary of State",
      "kind": "official_body",
      "page": 31,
      "quote": "Precinct 12 ... 1,304"
    }
  }
}
```

## Limits, tiers, and etiquette

- A key makes at most 60 writes a minute and 2,000 a day; `whoami` shows the limits. A proposal holds 1 to 500 ops. Uploads are up to 20 MB as base64 over MCP or REST, 2 GB a day per person.
- **Tiers.** Tier 1 changes (names, notes, external IDs, colors, images, citations, translations, and labels) can be approved by the editorial desk. Everything else waits for an administrator. A proposal takes the highest tier of its rows. New results are tier 1 only from an official body's document with a URL and stored copy, with no outcomes, no check findings, and no default set already held for that contest and stage; a `correct` op is always tier 3.
- A results op holds up to 50,000 rows and a correction up to 2,000 cells. New results become the contest's default only when no default is held and the accounting checks pass (options add up to valid votes, valid and invalid to ballots cast); otherwise they sit beside the held set for a reviewer.
- Batch related changes to one record into one proposal, and keep unrelated changes apart, so each can be reviewed and reverted on its own.
- Resubmit a rejected change only with new evidence, and say what is new.
- Write summaries and rationales for a reviewer who has not seen your research: what changes, why, and where the evidence says so.

## Errors

| Error                                                              | Fix                                                                                                                                                                                |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Send your API key…`                                               | Write tools need a key; see Connect.                                                                                                                                               |
| `Input validation failed` with field paths                         | Fix each named field; the paths point into your arguments.                                                                                                                         |
| A job error such as `ops[0]: add note needs kind, body_md, lang`   | The compiler names the op by index; fix that op and call `revise_proposal`.                                                                                                        |
| `evidence <key>: a URL or an upload needs a title and a publisher` | Add `title`, `publisher`, and `kind` to that evidence item.                                                                                                                        |
| `unknown evidence <key>`                                           | An op cites a key missing from the evidence map.                                                                                                                                   |
| `<table> <id> is changed twice`                                    | Merge the two ops on that row into one.                                                                                                                                            |
| A failed URL fetch                                                 | Upload the document instead, or cite a source the archive holds.                                                                                                                   |
| `A job on this proposal is still running.`                         | Wait, then read the result with `get_proposal`.                                                                                                                                    |
| `A key may make 60 writes a minute…`                               | Wait the seconds the message gives.                                                                                                                                                |
| Upload `status: rejected`                                          | Read its `error`. The archive accepts PDF, PNG, JPEG, TIFF, XLSX, ODS, CSV, GeoJSON, GeoPackage, Parquet, and zipped shapefiles; only PDFs and images get pages to cite by region. |
<!-- /write -->
