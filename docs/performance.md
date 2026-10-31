# Performance

This page explains how the site stays fast: where results are cached, how the cache is kept warm, which database settings matter, and which query patterns we use and avoid. Read it before you write a new RPC query or change the cache.

## How a page is served

Cloudflare sits in front of the origin. Static assets under `/_app/immutable/` have content-hashed names and are sent with `cache-control: public, max-age=31536000, immutable`, so browsers and the edge keep them for a year. Pages and twins are rendered by the SvelteKit server, which answers each page from a handful of RPC procedures called in-process. A loader starts independent procedure calls together with `Promise.all`, so a contest page waits for its slowest call, not for the sum of them.

Each kind of archive page (contest, election, day, and polity) is its own JavaScript chunk, loaded with a dynamic `import()`, so a contest page doesn't download the code for the other three. That cut the JavaScript a contest page preloads from about 473 kB to 240 kB gzipped.

Two sections of the contest page, the ballot list and the lean chart, sit below the results and can take seconds on a cold cache. They don't hold up the page. The loader starts them once the contest is found. When the main data is ready, it returns each one as a plain value if it has already finished, and as a promise if it hasn't. SvelteKit streams a promise to the browser when it resolves, and the page renders it with `{#await}`. A warm page therefore arrives fully rendered, and a cold one shows the results first and fills in the rest. The Markdown and JSON twins wait for both.

## The procedure cache

Every read procedure goes through `remember()` in `apps/web/src/lib/server/cache.ts`. A result is keyed by the procedure's name and its JSON input, and it lives in two tiers:

- **In-process.** A `Map` used as an LRU, capped at 20,000 entries or 1 GiB of serialized size, whichever comes first.
- **Shared.** Redis, keyed by `c:<build>:<procedure>:<input>`. Values are serialized with `node:v8`, compressed with zstd, and kept for 30 days. Values larger than 64 MiB are not stored.

The build id in the key means a deploy never reads results written by older code, so every deploy starts with an empty shared cache.

Each entry carries the data version that was current when it was computed. The version joins today's date, the number of published and rejected changesets, the time of the latest publish, and the time of the latest coverage build. The server reads it at most every five seconds. When the version moves, a stale entry is still served right away and a background renewal recomputes it. At most four renewals run at once, so the database never gets a burst of heavy queries after a publish. Procedures built with `strict` instead of `os` never serve a stale value: they recompute before answering.

## Warming

Because a deploy empties the shared cache, the first visitor to each page would pay for every query. After each deploy and each database restore, a warm-up script asks the database for the pages people are most likely to open: the home page, every country, supranational body, and federal unit, their recent general elections, and every contest in each one's latest general election. It then requests each page three at a time. That is about 18,000 pages.

## Database settings

- **JIT is off.** Our queries are large and generated, and their plans are expensive to compile. With JIT on, one contest page spent 14 of its 34 seconds compiling.
- `random_page_cost = 1.1` and `effective_io_concurrency = 200`, which suit SSD storage.
- After a restore, `vacuumdb --freeze --analyze` runs with eight jobs. Freezing sets the visibility map, so index-only scans skip the heap, and analyzing gives the planner fresh statistics.
- The web pool holds up to 32 connections and waits up to 30 seconds for one.
- `pg_stat_statements` and `track_io_timing` are on. Rank `pg_stat_statements` by `total_exec_time` to find the next query to fix.

## Query patterns

These patterns came out of plans that read whole tables. Each one turned a query that took seconds into one that takes milliseconds.

- **Start from a small set of ids.** When a query needs rows for a few contests, select the ids first, then fetch rows with `cross join lateral (select ... where rs.contest_id = ids.id offset 0)`. The `offset 0` keeps the planner from flattening the subquery into a hash join over the whole table.
- **Don't join recursive CTE output back to big tables.** Postgres estimates recursive CTEs poorly, so a join from `grp` or `tree` back to `contest` or `election` often becomes a hash join over all of `contest` (about 950,000 rows) or `election` (about 220,000 rows). Fetch the extra columns with a scalar subquery or a lateral instead.
- **Use `= any(array[x])` when an equality would pick the wrong index.** When a column is indexed by a btree and also appears, not first, in a GiST key, a plain equality can lead the planner to the GiST index. `= any(array[x])` steers it to the btree.
- **Don't wrap indexed columns in `coalesce`.** Write `(c.body_id = x or (c.body_id is null and c.office_id = x))`, not `coalesce(c.body_id, c.office_id) = x`, so each branch can use its index.
- **Filter before joining territories.** Find the elections you need first, then join `polity_territory` for only those.
- **Test existence in a select list with `(select true ... limit 1)`.** The planner can turn an `exists` in a select list into a hashed subplan that reads every matching row in the table. A scalar subquery with `limit 1` always runs per row and stops at the first match.
- **Probe geometry through its spatial index first.** Select candidate shapes with `g.geom && ST_MakeEnvelope(...)` in a subquery that ends in `offset 0`, then join `area` and filter by date. Without the optimization fence, the planner can combine the spatial index with the date index in a bitmap AND that reads hundreds of thousands of rows.
- **Fix the leading column of a GiST key.** The keys of `area_geometry`, `area_parent`, and `reporting_unit_area` are GiST indexes on an id and a `valid` range. A probe that fixes only the date scans the whole index. Start from the ids you already have, and look up each one with a lateral subquery.
- **Compute constant sets once.** Put a filter that doesn't depend on the row, such as the list of ICPSR sources or the dates of elections that have polity-wide results, in `= any(array(select ...))` or a materialized CTE. Then it runs once per query instead of once per row.
- **Summarize a CTE once, not once per row.** A scalar subquery such as `(select id from ahead a where a.root = i.id order by a.depth desc limit 1)` reads the whole CTE for every row. Compute `distinct on (root)` once and join the result.
- **Group before you test.** When thousands of rows collapse to a few hundred keys, as when every past contest of a body maps to its districts, group by the key first and run the existence checks on the grouped rows.
- **Fetch notes by entity id.** Put the union of ids first, then fetch matching rows with a lateral lookup by `entity_id`, instead of testing `exists` against the union for every note.
- **Run cheap geometry tests before expensive ones.** Compare bounding boxes with `&&`, or check `ST_Intersects`, before computing `ST_Intersection`. Fold "has no shape" and "overlaps" into one lookup per row instead of two. Clip a heavy shape to the other shape's bounding box with `ST_ClipByBox2D`, wrapped in `ST_MakeValid`, before intersecting them.
- **Make `is not distinct from` joins indexable.** A join on `c.body_id is not distinct from me.body_id` can't use an index. When one side is known to be non-null, add `(c.body_id = me.body_id or c.office_id = me.office_id)` so the planner can probe both indexes, and keep the null-safe test as a filter. For a small constant set, use `= any(array[...])`.
- **Memoize shared helpers.** A contest page runs several procedures, and each one resolves the URL path through `children()`. `children()` and the lean atlases also go through `remember()`, so a page load resolves each path once, and concurrent calls share one query.
- **Rule out candidates before resolving them.** The ballot list checks every contest held in the same place on the same day. A cheap check drops the contests that can't list this place before the expensive path resolution runs: it checks whether the two shapes' bounding boxes overlap, whether the place sits under the contest's area, and whether reporting units link them. On a Cleveland precinct page, the test dropped 112 of 133 candidates, and the ballot went from 20 s to 5 s cold.

## Indexes

Every column that a page looks rows up by needs a btree index. The migration `20261031200000_lookup_indexes` added the ones that the plans showed were missing:

- `source_icpsr_idx`, a partial index of the sources whose license mentions ICPSR. The withdrawn-license filter reads it in every result-set query.
- `body_polity_idx`, `office_polity_idx`, `office_body_idx`, `tenure_body_idx`, and `polity_parent_parent_idx`, which polity and body pages use.
- `contest_option_alliance_idx`, a partial index on `(alliance_id, party_id)` for the 1% of options that belong to an alliance. Counting an alliance's wins had read every option of each member party, which took up to 400 ms. It now takes under a millisecond. It is added in migration `20261031230000_alliance_option_index`.

To find another missing index, run the benchmark and then look at `seq_scan` and `seq_tup_read` in `pg_stat_user_tables`.

## Heavy geometry

Some boundaries have more than a million points, Nunavut's among them. Intersecting two such shapes takes seconds. `ea coarse` keeps a simplified copy of every area geometry with more than 20,000 points in `display.coarse`. The tolerance is 0.0002 times the square root of the shape's area. A copy is kept only when it has fewer than half the points and its area is within 0.5% of the original; otherwise the row holds no geometry, and queries fall back to the full shape. `ea tiles` refreshes the table, and rows are dropped when their source geometry changes.

The `coarse()` helper in `archive.ts` returns the simplified copy when one exists. Use it only where a share is measured against the heavy shape itself or against the larger of two shapes, such as overlap with a predecessor district. Don't use it where a share is measured against the smaller shape: simplifying a large neighbor can move a small district's share by much more than 0.5%. Filters such as `&&` and `ST_Intersects` keep using the full geometry.

Across the 70 predecessor pairs of the 12 heaviest districts of Canada's 2025 election, the largest difference between coarse and exact shares was 0.00047. None changed at two decimal places, and none crossed the 1% cutoff for listing a predecessor.

## Rendering and payloads

The server's CPU time matters as much as the database's once the queries are fast. These patterns came out of CPU profiles of cold page loads.

- **Reuse collators and formatters.** `localeCompare` with an options object builds a new ICU collator on every call, which made sorting France's 35,000 communes take 3 seconds. Sort with the shared `natural` comparator in `apps/web/src/lib/format.ts`, which wraps one `Intl.Collator` with numeric ordering. Build number and date formatters with `numbers()` and `dates()` from the same file, which keep one formatter per locale and set of options. Building an `Intl.NumberFormat` takes about 16 µs and an `Intl.DateTimeFormat` about 60 µs, while reusing one takes half a microsecond.
- **Group rows once instead of filtering per row.** When a loop over areas needs each area's result sets, group them with `Map.groupBy` before the loop instead of calling `filter` inside it. Key nested sums by result set, so each set reads only its own entries. On the 2024 UK general election page, the per-area filters took 480 ms of CPU.
- **Send only what the page shows.** A polity page sends the first 20 places of each kind and the 10 largest municipalities, plus the count of each kind. When someone filters the list or asks for every place, the page loads the full list from `polities.get`. The page for France went from 4 MB of HTML to 92 KB.
- **Pack sparse arrays.** A results map is a matrix with one row per place and one column per party or independent candidate, so most cells are zero or empty. The `transport` hook in `apps/web/src/hooks.ts` encodes every array of 64 or more items where at most a quarter of the items differ from 0 or `null` as its length and the items that differ. It encodes ascending runs of integers, such as the list of columns a place lacks, as start and end pairs. The browser decodes both back to the same arrays, so components never see the encoding. The 2024 UK general election page went from 5.4 MB of HTML to 1.6 MB, or 209 kB compressed, and the 2024 US general election page went from 1.6 MB to 1.1 MB.

## Measuring

The benchmark we track requests 45 representative pages one at a time against the origin, first with every cache empty and then with both tiers warm.

| Change                                                                                      | All 45 pages, cold | Slowest contest page, cold |
| ------------------------------------------------------------------------------------------- | ------------------ | -------------------------- |
| Before                                                                                      | 98.7 s             | 34 s                       |
| JIT off, lateral result-set lookups, lookup indexes, and the turnout rewrite                | 91.4 s             | 13.7 s                     |
| zstd, the owner predicate, and the recursive-CTE rewrites                                   | 80.8 s             | 13.2 s                     |
| Coarse geometry for overlaps                                                                | 69.0 s             | 3.0 s                      |
| Geometry-first lookups, lookups for lateral rollups, and the six lookup indexes             | 61.6 s             | 2.85 s                     |
| Intersects guards, grouped district lookups, and decided-race shortcuts                     | 57.4 s             | 2.83 s                     |
| Memoized path resolution, indexable peer joins, the ballot prefilter, and streamed sections | 54.7 s             | 2.72 s                     |

With both cache tiers warm, all 45 pages take 1.9 s together, and the slowest takes 0.34 s.

Pages below the county level gain the most from memoization and streaming. A Cleveland precinct page in the 2016 Ohio Senate race took 25.1 s cold. It now sends its first byte after 2.2 s and finishes streaming after about 6 s. The next precinct in the same district sends its first byte after 0.8 s. A Canadian riding opened from the national map takes 1.8 s cold, where it took more than 20 s before.

Polity and election pages with thousands of places gained the most from the rendering changes. France's polity page took 4.2 s cold and now takes 2.1 s. The 2024 UK general election page took 3.7 s and now takes 3.3 s. With the cache warm, both take under 0.15 s.

Most of the remaining cold time goes to reading from disk, because the database is about as large as the server's memory. The two slowest queries left are geometric. One draws the home page's world map, and its output is the same for every request, so the warm-up run caches it. The other query measures how much of a heavy district its predecessor covers, which means intersecting two shapes of about 90,000 points each. Pre-simplifying the world map changed its output, and btree indexes on `area_parent (area_id)` and `election (date_from, kind)` saved less than 10% on warm queries, so neither change was made.
