# Data pipelines

This guide explains how the data pipelines in `pipelines/` load election results into the archive, and how to add a source. Read it in about fifteen minutes. The commands assume a checkout of this repository, Python 3.14 or later, and `uv`. Run every command from the `pipelines/` folder with `uv run ea`.

For the database schema and vocabulary, read [data-model.md](data-model.md). For the overall system, read [architecture.md](architecture.md).

## What a pipeline is

A pipeline turns one official source into facts the archive can publish. Every pipeline has four steps:

- **Fetch** downloads the source files into the raw cache.
- **Parse** reads the raw files and writes typed Parquet tables under `data/parsed/{source}/`.
- **Emit** builds a changeset, a folder that describes every row the source adds or changes.
- **Apply** loads the changeset, runs checks, and publishes it, or holds it for review.

Each source is a Python module named by its registry name, `{iso}.{publisher}`. The name has two parts: the ISO code of the polity, such as `us`, `tr`, or `ch`, and the publisher, such as `medsl` or `bfs`. The module lives at `pipelines/ea/sources/{iso}/{publisher}.py` and defines `fetch()`, `parse()`, and `emit()`. The CLI imports `ea.sources.{name}` and calls the function named after the step. The Canadian federal elections module, `ca.elections`, is the reference pattern for the others.

Pipelines never write to the core tables directly. Each one writes a changeset, and only `apply` publishes changesets. A changeset is a folder with a `manifest.json` file and one Parquet file per target table. The manifest records the author, the pipeline name and version, the kind of change, the declared scope, and the order in which the tables publish. This design gives every write three properties. Each row has a pipeline and a source, each changeset can be reviewed before it lands, and each one can be reverted later.

`apply` loads a changeset into a staging schema, runs every applicable check, and computes the difference from the current data. It then publishes the changeset in one transaction. A changeset publishes automatically when every check passes, if it comes from an official body and every source is a registered official source. Any other changeset is set to `pending_review`, including every changeset from a person or an agent. A reviewer then publishes it with `ea publish`, or reverts it with `ea revert`.

## The raw cache

The raw cache keeps every downloaded file exactly as the source served it. Files live under `data/raw/{source}/`. Each file is named by its SHA-256 hash and keeps the extension it arrived with, so `data/raw/{source}/{sha256}.{ext}`. Each source folder also has an `index.json` file. The index records, for each file, the URL it came from, its SHA-256 hash, its retrieval time, and its media type.

Three rules govern the cache:

- Downloads are never edited. Parsing reads the cached file and writes its output elsewhere.
- A re-run reuses the cached file when the index already has it. A run therefore reproduces the same parse and the same changeset from the cache alone, even after the source changes or goes offline.
- Raw files are untrusted input. They are read as data, never run as code.

Some sources need a different shape of request. A query API that requires a POST passes a `body=` argument to `raw.fetch` and `raw.cached`. The index keys such an entry by the URL plus a hash of the JSON body, and it stores the body, so each query becomes its own artifact. A source that a person crawls and supplies, rather than a URL the pipeline can fetch, goes into the cache through `raw.store`. Wikipedia pages are pinned to the revision current at retrieval time, so a lost file can be fetched again exactly.

Files that a person downloads by hand live in `data/inbox/manual/MF-###/`, one numbered folder per download request, because the site blocks scripted downloads; the pipeline that needs them reads them from there and adds them to the cache with `raw.store`.

`emit` turns the index entries it used into `source_artifact` rows in the changeset. Those rows let any published fact trace back to the file it came from.

The `ea raw` command maintains the cache:

- `uv run ea raw verify [source] [--quick]` checks each entry's file, size, and hash against the index. It reports unindexed files and exits nonzero when a file is missing or changed. `--quick` checks sizes without hashing.
- `uv run ea raw license [source]` records the licenses of the sources that cite each entry. Those licenses determine whether the raw file may be shared.
- `uv run ea raw pin [source]` pins Wikipedia fetches that were cached before pinning existed.
- `uv run ea raw retire [source]` moves unindexed files older than a day to `data/retired/raw/{source}/`.

## Commands

The main steps take a registry name, except `apply`, `publish`, and `revert`, which take a changeset. Run `uv run ea <command> --help` for each command's arguments.

- `uv run ea fetch <source>` downloads the source's files into the raw cache.
- `uv run ea parse <source>` writes typed Parquet tables under `data/parsed/<source>/`. It fails when the rows disagree with the totals the source prints.
- `uv run ea emit <source>` writes a changeset folder and prints its path.
- `uv run ea apply <changeset-folder>` loads a changeset, runs its checks, and publishes it or sets it to `pending_review`.
- `uv run ea publish <changeset-id> [--reviewer <user-id>]` publishes a changeset that is in `pending_review`.
- `uv run ea revert <changeset-id> "<reason>" [--reviewer <user-id>]` rejects an unpublished changeset, or undoes a published one that no later changeset builds on.
- `uv run ea raw {verify,license,pin,retire}` maintains the raw cache, as described above.
- `uv run ea fixtures` exports the engine's golden-test fixtures from the database.
- `uv run ea rollup <set>...` recomputes the area rollups of the named result sets. `--affected` rebuilds every set whose rollups are missing at its district.
- `uv run ea coverage` measures a polity's coverage against its official goals and writes the report, with subcommands for the United States (`us`), each supported country, and the coverage tables the app reads (`refresh` and `rebuild`).
- `uv run ea tiles [scheme]` builds one PMTiles archive per boundary set, with geometry only.
- `uv run ea rasters [scheme]` builds a raster archive of area IDs for each boundary set of 1,000 or more areas.
- `uv run ea basemap {br,ca,ch,de,fr,gb,ie,it,tr,us}` builds a polity's planar basemap.
- `uv run ea search` rebuilds the search index from names and slugs. Run it after any changeset that adds or renames entities.
- `uv run ea scans {prepare,run,status,emit,layout,statewide} [name]` drives the batch vision pipeline for scanned tables. See the digitization section below.

## Worked example: Canadian federal elections

This example loads Canada's federal results into a development database. It runs against your own database, not the archive's production data. Set `DATABASE_URL` in a `.env` file at the repository root, and copy `.env.example` as a starting point. The database needs PostgreSQL with PostGIS and the migrations applied. The README describes the migration command.

```sh
cd pipelines
uv run ea fetch ca.elections
uv run ea parse ca.elections
uv run ea apply "$(uv run ea emit ca.elections)"
```

`fetch` downloads the poll-level result files, the riding boundaries, and the by-election results. A second run reuses every cached file that the index already lists.

`parse` reads the cached files and writes Parquet tables under `data/parsed/ca.elections/`. If the parsed rows disagree with the totals the source prints, the parse fails, so a bad parse never reaches the changeset.

`emit` builds the changeset and prints the folder path. The shell passes that path to `apply`, which loads the changeset, checks it, and publishes it. Because Canada's federal results come from an official body, the changeset publishes automatically when every check passes.

## Checks

A changeset must pass its checks before it publishes. Each check has a stable code. A finding that names an entity or a result set becomes a `flag` row, which the public site shows. The changeset's `checks` field keeps the summary: the diff, the counts per code, and sample rows.

These are the checks in the current code:

- **Accounting** (`accounting.ballots_sum`, `accounting.votes_valid`, `accounting.turnout`) checks that valid, invalid, and blank ballots add up to the ballots cast, that option votes sum to the valid total in single-member contests, and that turnout does not exceed the number registered.
- **Outcomes** (`outcomes.winner`, `outcomes.list_members`) checks that a declared winner has the most votes in single-member contests, and that a list declares no more elected members than its seats. Instant-runoff contests are exempt from the winner check, because the first-round leader can lose.
- **Reference** (`reference.facts`, `reference.stranded`) checks that every unit, area, and option resolves, and that a published result set does not hold facts that point at a deleted row.
- **Immutability** (`facts.immutable`) refuses facts for a published result set that another pipeline last wrote.
- **Rules** (`rules.schema`) checks that each electoral system's rules validate against the engine's JSON schema.
- **Geometry** (`geometry.valid`) checks that polygons are valid.
- **Scope** (`scope.polity`, `scope.owner`) checks that the diff touches only what the manifest declares, and that a pipeline never changes a row that another pipeline or a person last wrote.

Some checks run at apply time, and some run in the engine's golden tests. The engine recomputes seat allocations from the rules and compares them with the official seats. Declared seats are stored as published, and the engine's output is compared against them, never written over them.

## Re-emits and result sets

A re-emit runs the same pipeline again and writes a new changeset. Re-emits must be safe, so the archive holds several rules about IDs and rows.

**IDs are stable.** The emit resolves each entity by its natural key: the polity's ISO code, the area code within a scheme, the unit code within a scheme, or the source's publisher and title. Where a scheme reuses codes, the emit resolves them on a date, as Canada's riding numbers do. Pipeline-specific keys live in `external_id` under a `{source}.{kind}` scheme. An entity that no key resolves gets a UUIDv5 derived from its scheme and key, so a re-emit into a rebuilt database mints the same IDs again.

**Re-emits replace their own rows.** A changeset that stages facts for a result set its own pipeline last wrote replaces that set's facts. A fix to the parse, the mapping, or a key is therefore a re-emit into the same set. Temporal rows that a staged row overlaps are replaced, and keyed child rows that the pipeline no longer stages are deleted. Rows another pipeline wrote, and entities, are never deleted this way.

**Disagreeing or finer sources supersede.** When a second source disagrees with the first, or when a source offers a finer breakdown of the same contest, the emit publishes a superseding result set. The new set becomes the default and records what it supersedes. The old set stays in the archive as superseded and non-default. A superseding set must also stage the contest's outcomes, because `apply` copies outcomes only when it flips a default.

**Published sets become immutable.** Until the archive's public launch, a re-emit can replace the facts of a set that its own pipeline last wrote. After launch, a published result set is immutable. Corrections then arrive as superseding sets.

**Reverting is the fallback.** `ea revert` undoes a changeset. It deletes the result sets the changeset created, restores the rows it changed from their history, and removes the entities it minted. It refuses when a later changeset changed a row it wrote, references an entity it created, or holds facts on its units. It also refuses when the changeset replaced facts in a set it did not create. Those corrections are fixed forward with another re-emit. Use `revert` when a re-emit cannot express the fix, such as an entity that should not exist. Revert newer changesets first, and move the reverted folders to `data/retired`.

## Entity resolution

Matching people, parties, and places across elections and sources runs in three stages. Deterministic rules come first: official IDs, and exact normalized names within a polity and period. Wikidata lookups come second. AI-assisted matching comes last, and it outputs a confidence score with the evidence behind it. High-confidence matches are applied automatically as `match` changesets. Everything else goes to review. A wrong match is undone by reverting its changeset.

### Parties

The `wikidata` pipeline matches each party to a Wikidata item. It queries parties by country, scores each item by how well its labels, aliases, short names, and official names match the party's names, and checks the item's founding and dissolution dates against the party's elections. The top item must score well enough and beat its runner-up. A party that another pipeline has already linked keeps its link. The same pipeline imports each party's colors and political position from Wikidata. The module keeps a party's hand-curated color, or a political position that the source leaves out, as a constant with a note, so reviewers can see where it came from.

### People

Two people who are the same person merge with `cs.merge(duplicate, survivor)`. Publishing the merge re-points every foreign key and every fact that references the duplicate, moves the duplicate's names and slugs to redirects, and records each re-pointed row in `fact_rekey`, so `ea revert` can undo the merge. The merge is refused when the survivor already holds a conflicting row for the same key.

Pipelines match people across elections with their own matchers. `us.match` joins book persons, FEC candidate IDs, and Electoral College people. `tr.match` joins members of the national assembly across consecutive elections when they share a printed name and electoral district. `ca.match` joins candidates across consecutive federal elections when they share a printed name and overlapping ridings. Each matcher keeps the person with the most evidence, such as an official ID, a Wikidata item, or the most candidacies, and merges the rest into it. A person who was never real, such as a parse artifact, is retired with an `entity_retirement` row rather than merged, and the retirement refuses if anything still references the entity.

Names are handled in the same way. A pipeline stages a preferred name only when it is the best name it has for that entity and language. A name that should not appear on its entity is removed with `name_drop`, and a caveat that no longer applies is removed with `note_drop`. These rows have IDs, so a re-emit alone cannot delete them.

### Places

A place resolves by its scheme and code, with a date when the scheme reuses codes. Precinct units resolve through `ea.precinct_code`, which builds the same code from the same state, county, and precinct, so two sources for one precinct and year share one unit. Town and municipal areas match to boundary shapes by name within their county. A place's own contest on election day sets its universe, and other sources only fill in what that contest does not hold. Shared display names are settled by a curated list, `identity_names.tsv`, which names places that share a name within a state by their legal type or county.

## Digitization

Scanned compendia, such as nineteenth-century returns, national statistical books, and printed county tables, need their numbers read from page images. Every digitized number stores its source page and a bounding box in `value_locator`. A reviewer can therefore check any number against the printed page.

Two independent passes read each page, either a multimodal model and OCR, or two different models. The passes are reconciled cell by cell and then checked against the totals printed in the source. A disagreement goes to review, with the page crop attached.

PDFs with a text layer need no OCR. `pdftotext -bbox` gives each word with its box, and columns come from clustering the right edges of the words, because blank cells break the token order.

For ruled tables in scans, the OCR pass is `pipelines/ea/scan.py`. It renders each page at 300 dpi, detects the grid from long dark pixel runs, OCRs each cell on its own with Tesseract, and reconciles the results against the printed row and column sums. Full-page OCR fails on the same tables, because it cannot see the cell boundaries.

The batch vision pipeline, `ea scans`, reads scanned county tables with a Claude model through the Message Batches API. Each volume has a manifest in `pipelines/scans/{name}.json`. The manifest lists the source file, the pages, and for each page the crop, the number of bands, the orientation, and one entry per printed column with its contest key and its printed candidate label. `ea scans layout` can write a manifest from the model's own reading of a volume's pages, and `ea scans prepare` fills in the official totals the archive already holds for each column.

The steps run in order, and each step is a single call that advances the batch once:

- `ea scans prepare <name>` renders the crops, fills the official totals, and prints the number of requests.
- `ea scans run <name>` advances the reading. It reads the first pass, checks the tables, sends a second read for pages that fail, and writes `tables.parquet`, `review.json`, and `report.md`.
- `ea scans status` lists every manifest and its open jobs.
- `ea scans emit <name>` writes the tables that passed through the normal changeset path.

A column passes when its county rows sum to its printed total, or, if no total is printed, to the official total within the greater of 50 votes or one percent. A page passes when every column passes. A page that still fails after the second read goes to `review.json`, and nothing on that list is published. A person settles it by writing the corrected cells and a note into `overrides.json` in the same folder.

Responses are cached in the raw cache, keyed by the model, the prompt version, the effort level, and a hash of the image. Rerunning a volume with the same inputs costs nothing. Set `EA_SCANS_CACHED` to make every step read only what the cache holds and send no requests. Set `EA_SCANS_RESTAGE` to restage the sets a volume has already published. Batch requests go through `pipelines/ea/batch.py`, which stops before it sends anything when the environment it runs in is not supported. A contributor without that setup can still run any step that reads from the cache.

## Live ingestion

Live workers poll official machine-readable feeds where they exist, and official results pages, such as HTML, PDF, or results platforms that vendors host for election offices, where they do not. They poll at a polite interval, send conditional requests, back off when a server pushes back, and identify themselves with a User-Agent header. Each update writes a live result set and publishes a snapshot with a manifest. Live values never become certified values. Certified results come from post-count imports, which replace the live set through the usual changeset path. The architecture document covers how live results are served.

## Adding a source

Follow these steps to add a source. The example uses a hypothetical source, `xx.example`.

1. **Check the archive first.** Search the existing modules in `pipelines/ea/sources/{iso}/` and `docs/data-model.md`, so that you do not duplicate a publisher or a naming scheme. Note the source's license or its absence. The `licenses` correction pipeline records licenses for sources that publish none.

2. **Create the module.** Add `pipelines/ea/sources/xx/example.py`. Define `SOURCE = "xx.example"`, a `VERSION` string, and `PARSED = DATA / "parsed" / SOURCE`. The version is written into every manifest, so bump it when the output of the module changes.

3. **Write `fetch()`.** Call `raw.fetch(SOURCE, url, suffix=".csv")` for each file the source publishes. Use the source's stable URLs. If the source needs a POST, pass `body=`. If the source is a crawl you ran yourself, store it with `raw.store`.

4. **Write `parse()`.** Read each cached file with `raw.cached(SOURCE, url)`, and write typed Parquet tables under `PARSED`. Compare the parsed rows with the totals the source prints, and raise an error on any mismatch. Corrections to the source's own cells are curated constants in the module, each proven by the printed totals and disclosed in a `note` on the result set.

5. **Write `emit()`.** Open a connection with `connect()`, create a `Changeset` with the pipeline name, version, a one-line summary, and the polity's ISO code, and stage the rows. Use `cs.source(publisher, title)` for each source, and `cs.artifact(raw.cached(SOURCE, url), source)` to cite each cached file. Resolve existing entities with `cs.key`, `cs.unit`, and `cs.area`, and end with `return cs.write()`. Canada's `ca.elections` module shows the pattern at each step.

   A sketch of `emit()`, where `PUBLISHER`, `TITLE`, and `URL` are module constants:

```python
def emit() -> Path:
    with connect() as conn:
        cs = Changeset(conn, pipeline=SOURCE, version=VERSION, summary="One sentence on the scope.", polity="XX")
        source = cs.source(PUBLISHER, TITLE)
        cs.artifact(raw.cached(SOURCE, URL), source)
        return cs.write()
```

6. **Run the steps in order.** Run `uv run ea fetch xx.example`, then `uv run ea parse xx.example`, then `uv run ea apply "$(uv run ea emit xx.example)"`. Start with a single contest or a single year if the source is large. A re-run reuses the cache, so a later step does not repeat the earlier downloads.

7. **Read the changeset before publishing.** A changeset from a person, or from a source that is not an official body, waits in `pending_review`. Read its `checks` summary and its flags. Publish it with `uv run ea publish <changeset-id>`, or reject it with `uv run ea revert`. Check the new entities in the changeset for anything unexpected, because a pipeline that publishes automatically can mint an entity that should not exist.

8. **Record the source and its citations.** Every fact must trace to a source and a changeset. Cite each cached file through `cs.artifact`. If the source publishes a Wikipedia page, fetch the page with its revision pinned.

9. **Check the cache.** Run `uv run ea raw verify xx.example` to confirm that every indexed file matches its hash, and `uv run ea raw license xx.example` to stamp the license on each entry.

10. **Rebuild the search index after entities change.** Run `uv run ea search` after any changeset that adds or renames entities.

Keep each pipeline small. A source that needs entity matching, such as a people or party matcher, belongs in its own module, which runs after the source publishes.
