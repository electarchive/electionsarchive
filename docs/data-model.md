# Data model

The archive is one Postgres 18 database with PostGIS. The schema in `packages/db/src/core.ts` names every table and column in this guide, and the migrations in `packages/db/migrations/` create them. What the tables hold and why they have their shape is explained here, so you can read a pipeline, a changeset, or a query without guessing what a column means. For the code layout, read the [architecture guide](architecture.md). For how pipelines load data, read the [pipelines guide](pipelines.md).

## What the archive stores

The archive stores election results and the facts needed to read them. Its first ten polities are the United States, the United Kingdom, France, Brazil, Germany, Canada, Italy, Turkey, the European Union, and Switzerland. The goal is every election in the world, down to the finest reporting unit each election used. Coverage differs by polity and year, so check the coverage pages before you assume a race is missing or complete.

The data follows a chain. A polity holds elections or offices. An election is an occasion on a date range, and it groups contests. A contest is one decision, such as an office, one body's seats in one district, or a ballot question. Each contest has options, which are its ballot lines. A result set is one tabulation of one contest, from one source, at one stage. It holds values for each reporting unit, where votes were counted and reported. Each value has a measure, an option, a channel, and a count round.

Outcomes record what the authority declared. Tenures record who held an office afterward, which can differ from who won.

### Result sets

A result set has a stage, which says where the count sits in reporting: `live`, `preliminary`, `official`, `certified`, `court_revised`, `recount`, `alternative`, or `notional`. It also has a result kind: `counted` for votes, or `tacit`, `show_of_hands`, or `appointed` for races decided without a count. Those three kinds have outcomes but no vote values.

A result set with no contest holds election-level accounting, such as registered voters shared by every contest on one ballot paper.

Each election, contest, and stage has at most one default result set, which a partial unique index enforces. An official body's set outranks other sources for the default. When one stage has two grains, such as constituency totals and polling-station files, the coarser official set is the default and holds the outcomes. The finer set records a `completeness` value, its valid votes divided by the official valid votes. Partial community tallies use stage `alternative` and never become the default. A notional result is an estimate of an earlier election on later boundaries. It is flagged as estimated and holds no outcomes.

## Vocabulary

Use these terms in the same sense across the code, the API, the documentation, and the interface.

- **Polity**: an institution that holds elections or has offices, such as a country, a state, a county, or the European Union. A polity is not a place, and its territory can change over time.
- **Area**: a spatial unit in a scheme, such as a county, a municipality, or an electoral district.
- **Scheme**: a family of areas or reporting units that one authority defines for one purpose.
- **Boundary set**: a scheme at one vintage, such as the districts drawn in one year.
- **Reporting unit**: the place where votes are tabulated and reported, such as a ballot box, a precinct, or a county total.
- **Channel**: how votes were cast, such as on election day, early, or by post.
- **Contest**: one decision within an election.
- **Option**: a ballot line in a contest, such as a candidate, a ticket, a list, an answer to a question, or a write-in.
- **Candidacy**: one person's place on an option, with a role such as head, running mate, or list member.
- **Result set**: a tabulation of one election or contest, from one source, at one stage.
- **Measure**: what a number counts, such as votes, registered voters, or valid ballots.
- **Flow**: votes that moved from one option to another, such as transfers in a single transferable vote count.
- **Outcome**: what the authority officially declared for an option, such as elected, advanced, or seats won.
- **Changeset**: the only unit of write to the core tables.
- **Release**: a dated snapshot of the archive, defined by a changeset watermark.
- **Valid time**: when something was true in the world. **Recorded time**: when we recorded it, or changed our record of it.
- **Flag**: a failed check or a caveat attached to data. Flags are shown publicly.

## Conventions

### Identifiers and slugs

Every addressable thing has a row in `entity`, the supertype. Subtype tables such as `polity`, `contest`, `result_set`, and `party` share its primary key. Names, notes, labels, flags, external IDs, slugs, and citations refer to `entity.id`, so each works with an ordinary foreign key.

Postgres gives new rows a UUIDv7 through the `uuidv7()` default. When a pipeline has a stable key, it derives a UUIDv5 from the scheme and the key, so a rebuild keeps the same IDs.

Each entity also has a public ID in the form `{prefix}_{crockford-base32(uuid)}`. The prefix names the type: `pl` for polity, `ar` for area, `ru` for reporting unit, `el` for election, `ct` for contest, `op` for option, `rs` for result set, `pt` for party, and `pe` for person, among others. Public IDs are permanent and are served at `/id/{public_id}`. The `ea_public_id` function computes one, and `ea_resolve` reverses it. Public IDs are computed, not stored in a column.

A duplicate entity is merged, never deleted. An `entity_merge` row points the duplicate at its survivor, and the old public ID redirects there. An entity that was never real, and that nothing references, is retired with an `entity_retirement` row that gives the reason. Its public ID then answers with HTTP 410, and its ID is never reused.

Other corrections have their own tables, each with a reason: `entity_subtype_drop`, `external_id_drop`, `name_drop`, `membership_drop`, `unit_area_drop`, `note_drop`, `option_drop`, and `row_drop`. A correction removes a row only when the pipeline that wrote it is the one withdrawing it.

Slugs live in `slug`. Only one slug per entity is current, and older slugs stay on record and redirect to it.

Entities that appear in fact tables also carry a compact integer key `k`: `area`, `reporting_unit`, `contest_option`, and `result_set`. Fact rows and map tiles use `k`, and nothing outside the database exposes it. UUID keys would cost far more space on the largest fact tables.

External IDs link an entity to an identifier in another system, such as Wikidata. The `external_id` table is unique on scheme and value, so an ID is recorded only when it identifies one entity.

### Dates and valid time

Facts that can change in the world carry valid time. Base tables hold only identity that does not change. Names, codes, parent areas, geometry, colors, seat counts, and memberships live in child tables with valid time.

Valid-time columns are `valid_from` and `valid_to`, each with a `date_precision` of `day`, `month`, `year`, or `unknown`. `valid_to` is exclusive, and a null value means the span is open. Each table also has a generated `valid` daterange. Versioned tables use a temporal key written as `valid without overlaps`, so the database rejects overlapping versions.

Event dates, such as elections and tenure events, carry a date and a precision. An event date can also record its original calendar (`gregorian`, `julian`, `rumi`, `hijri`, or `french_republican`) and the text as printed.

### Recorded time and history

Every table except the fact tables has a `changeset_id`, which names the changeset that wrote its current version, and a `recorded_from` timestamp. A trigger stamps both from the session setting `ea.changeset_id`, so a write without a changeset fails.

Each versioned table has a history twin named `{table}_history`. It has the same columns, plus a `recorded` range and a `superseded_by` column that names the changeset that replaced or deleted the row. The archive's state at any release watermark can be rebuilt from the current and history tables.

Fact tables, such as `result_value`, have no history. A published result set does not change. A correction publishes a new result set that supersedes the old one. Before launch, a pipeline can replace the facts of a result set it wrote last. The `live` stage is the one exception: its set changes while counting, and it is frozen when counting ends.

### Votes, seats, and shares

Vote values are exact decimals (`numeric`), because some counts are fractional. Seats appear as declared outcomes and declared body sizes. Engine output is never stored as a value.

Every fact row carries a `flags` bitmask. The flags mark values we derived, values that are estimated, values a source published only as shares, values digitized from a scan, values in dispute, and values reported only above the finest unit.

Channels partition a total. The channel codes are `all` (0), `election_day` (1), `early` (2), `postal` (3), `abroad` (4), `provisional` (5), `mobile` (6), `prison` (7), `customs` (8), and `special_rules` (9). For a given option, unit, measure, and count round, a unit has either one row on `all` or one row per channel, never both.

Measures are coded in the `measure` table. In a count with several rounds, such as an instant-runoff count, each candidate's running total at the end of count _n_ is stored under count round _n_. Option 0 holds unit-level accounting, such as registered voters for a unit.

Maps and tables read rollups, which are sums of values over the areas that a result set's units roll up to. Rollups are derived and rebuildable. The `result_rollup` view combines `result_rollup_sum` and `result_rollup_unit`.

### Declared outcomes and engine output

Official outcomes and seats are stored as declared. The engine in `packages/engine` recomputes results from an electoral system's rules and the votes, then compares its answer with the declared outcome. A mismatch raises a flag. The declared record is what the archive publishes.

## Geography and reporting units

### Schemes, areas, and boundary sets

A `scheme` names a family of areas, with a kind (`administrative`, `electoral_district`, `precinct`, `census`, or `synthetic`) and a rank that orders schemes from coarse to fine. A scheme that belongs to no polity has a null `polity_id`.

An `area` row holds identity only. Official codes live in `area_code`, and the hierarchy lives in `area_parent`, both with valid time. A transfer between parents is a new `area_parent` version and an `area_event` of kind `transferred`.

The shape of an area lives in `area_geometry`, with a multipolygon in SRID 4326, a label point, a quality, a source, and valid time. Quality is `official`, `osm`, `academic`, `digitized`, or `synthetic`. Synthetic geometry is always flagged on the site.

A `boundary_set` is a scheme at one vintage. Its `as_of` date picks the geometry valid on that day, and its tiles are keyed by `area.k`. Lineage events live in `area_event`, with their areas in `area_event_member` under the role `from` or `to`.

### Reporting units

A `reporting_unit` is a place where votes are counted and reported. Its kind is `standard`, `abroad_mission`, `customs_gate`, `prison`, `mobile`, `postal_district`, or `aggregate`. Its `area_relation` is `equals` (a precinct with its own shape, or a county total), `within` (a ballot box inside a neighborhood), or `unlocated` (an embassy, which has no area).

An `aggregate` unit holds votes reported only above the finest level, such as county-wide absentee ballots. Those votes stay at the level the source reports them and are never forced down to finer units. A unit's `parent_id` follows the source's reporting hierarchy.

A unit's code is unique within its scheme over valid time. For United States precincts, the code takes the form `{year}:{ST}:{county FIPS}::{normalized precinct id}`.

The `reporting_unit_area` table adds area memberships when the reporting hierarchy does not nest inside the area hierarchy. A German polling district, for example, lies within its municipality and also belongs to a constituency. Two flags qualify a membership. `approximate` marks a membership that stands in for a missing shape. Maps may draw it, labeled as an approximation, but it never feeds a rollup. `partial` marks a unit that reports only part of the area it lies within, so its rollups are incomplete. The `method` column records how a membership is known. Memberships are never apportioned.

The `area_universe` table records how many reporting units a place should have on an election day, and `rollup_coverage` compares the units that reported with that expectation. A rollup is complete only when the two counts match.

### Crosswalks

A crosswalk re-aggregates results from one set of boundaries onto another. Its `method` is `exact`, `registered_voters`, `population`, or `area`, in order of preference. Its weights live in `crosswalk_weight`. Every comparison a reader sees is labeled `exact` or `estimated`.

## Parties, people, and offices

### Parties and alliances

A party's kind is `party`, `independent_group`, `list_vehicle`, `federation`, `bloc`, `nuance`, `parliamentary_group`, or `caucus`. A rename keeps the entity and adds a name version. A legal refounding or merger creates a new entity, linked to its predecessor through `party_relation`.

The `party_affiliation` table records membership in a larger body, such as a European Parliament group. `party_color` holds dated, sourced colors. `party_position` places a party on a scale from `-3` to `3`. `party_founding` holds the founding date.

Alliances live in `alliance`, with kinds `electoral_alliance`, `joint_list`, `federation`, and `apparentement`. An option joins an alliance through `contest_option.alliance_id`.

Party logos live in `party_image`. Each logo comes from a dated source statement, never from an upload date, and it carries its license and author. Only public domain, CC0, CC BY, and CC BY-SA files qualify.

### People and candidacies

The `person` table holds only what an official source publishes about a candidate or officeholder, plus Wikidata links. It never stores identity numbers, addresses, contact details, or residences. A gender is recorded only when an official source publishes one. Portraits live in `person_image`.

A `candidacy` links a person to an option, with a role of `head`, `running_mate`, `list_member`, or `substitute`. The `candidacy_nomination` table records how a party is connected to a candidate. The kind is `nominated` when the party formally applied for the candidate, `endorsed` when a party backs a candidate who qualified another way, and `supported` when a party backs another party's candidate. A party label on a ballot without a nomination is a preference, not a nomination.

The `person_party_membership` table is keyed by person, party, and valid time, so memberships can overlap.

### Bodies, offices, and tenures

A `body` is an assembly, such as a lower house, an upper house, or a council. Its declared size lives in `body_size`. Where a law fixes categories of seats, `body_size_part` breaks the total down by `seat_category`. `body_apportionment` records how many seats an area was entitled to.

An `office` is a post in a polity or body. A `seat` is a place a contest fills. A seat's `voting` flag is false when its holder has no vote on the floor.

A `tenure` is one person's term in an office or seat. It records how the person acquired the office (`acquired_via`, such as `elected` or `appointed`) and how the term ended (`ended_via`, such as `term_end` or `resigned`). The `tenure_event` table records dated events within a term, and `tenure_affiliation` records the party or parliamentary group the member sat with. A `government` is a cabinet, with a `caretaker` flag. `government_party` records each party's role in it.

Being elected is an outcome, and being seated is a tenure. A winner who never takes up a seat keeps the declared outcome and gains no tenure.

## Provenance

Every published number traces to a source through its result set, or through a `citation` when the fact is not a result. Every number also traces to the changeset that wrote it.

The `source` table records each source's kind (`official_body`, `aggregator`, `compendium`, `news_agency`, `academic`, `community`, or `wikidata`), its publisher, its URL, its license, and its attribution. Raw files are cached in `source_artifact` with a SHA-256 checksum and a retrieval time. Scanned files keep page images in `source_page`.

A digitized number keeps its page and a normalized bounding box in `value_locator`, and the site shows that crop beside the number. A locator can name its own `source_id`, which overrides the result set's source for one value. A locator must have a page or a source, and a page goes with its bounding box.

Prose is written once, in English. The `translation` table holds translations of each text, keyed by the MD5 hash of that text, each with an origin of `official`, `human`, or `machine`.

## Changesets and the apply path

A changeset is the only unit of write to the core tables. Each row records its author (`author_kind` is `pipeline`, `user`, `agent`, or `system`), its pipeline and version, its `kind` (`import`, `edit`, `correction`, `match`, `digitization`, or `live`), and its `status` (`draft`, `pending_review`, `published`, `rejected`, or `superseded`). It also records a summary, a rationale, the scope it may touch, its payload, its checks, and its review times.

A payload takes one of two forms. The `ops` form holds small edits as JSON operations that people and agents submit through the API. The `artifacts` form points to Parquet files in object storage, which pipelines use for imports.

The apply path loads the payload into staging, runs the checks, and then publishes the changeset in one transaction or queues it for review. A pipeline changeset from a registered official source publishes automatically when every check passes and the diff stays inside its declared scope. Everything else waits in `pending_review`.

A release is every changeset published on or before its watermark. The `release` table stores the calendar-version label, the watermark changeset, an optional DOI, and a manifest.

## Main tables

The schema in `packages/db/src/core.ts` is the complete reference. This table lists the main tables by purpose.

| Table                                                                                                            | What one row holds                                                                     |
| ---------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `entity`                                                                                                         | The identity of every addressable thing, with its type                                 |
| `polity`, `polity_parent`, `polity_territory`, `polity_relation`, `polity_period`                                | An institution that holds elections, and its parents, territory, links, and periods    |
| `scheme`, `area`, `area_code`, `area_parent`, `area_geometry`, `area_event`                                      | A family of areas, the areas, their codes, hierarchy, shapes, and lineage events       |
| `boundary_set`, `crosswalk`, `crosswalk_weight`                                                                  | A scheme at one vintage, and re-aggregations between boundary sets                     |
| `reporting_unit`, `reporting_unit_area`, `area_universe`                                                         | A place where votes are counted, its area memberships, and its expected count          |
| `election`, `contest`, `ballot_question`, `ballot_question_text`                                                 | An occasion, a decision within it, and a ballot question with its wording              |
| `contest_option`, `candidacy`, `candidacy_nomination`                                                            | A ballot line, a person's place on it, and a party's nomination of that person         |
| `result_set`, `result_value`, `result_flow`, `value_locator`                                                     | A tabulation at one stage, its values and flows, and their page locators               |
| `result_rollup_sum`, `result_rollup_unit`, `rollup_coverage`                                                     | Derived sums over areas, and how many reporting units each place has reported          |
| `outcome`                                                                                                        | What the authority declared for an option or a candidacy                               |
| `measure`, `channel`, `electoral_system`                                                                         | The codes for what a number counts and how votes were cast, and the rules for a system |
| `party`, `party_color`, `party_founding`, `party_position`, `party_relation`, `party_affiliation`, `party_image` | A party and its colors, founding, position, lineage, memberships, and logo             |
| `alliance`, `alliance_member`                                                                                    | An alliance and its members                                                            |
| `person`, `person_party_membership`, `person_image`                                                              | A person with published data only, party memberships, and a portrait                   |
| `body`, `body_size`, `body_size_part`, `body_apportionment`, `representation_gap`                                | An assembly, its declared size, its apportionment, and gaps in representation          |
| `office`, `seat`, `tenure`, `tenure_event`, `tenure_affiliation`                                                 | An office, a seat, a term, events within it, and the group the member sat with         |
| `government`, `government_party`                                                                                 | A cabinet and the parties in it                                                        |
| `name`, `external_id`, `slug`                                                                                    | A name, an identifier in another system, and a URL path                                |
| `note`, `translation`, `label`, `flag`                                                                           | Prose, translations, outside assessments, and failed checks or caveats                 |
| `source`, `source_artifact`, `source_page`, `citation`                                                           | A source, its cached files and page images, and its link to an entity                  |
| `changeset`, `release`                                                                                           | The unit of write, and a dated snapshot defined by a watermark                         |
| `entity_merge`, `entity_retirement`, `row_drop`, `fact_rekey`                                                    | Recorded corrections, each with a reason, and a record of changed compact keys         |
| `user_role`, `saved_view`, `proposal`, `proposal_revision`, `proposal_job`, `activity`                           | Application tables for roles, saved views, proposed changesets, and activity           |
