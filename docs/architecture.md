# Architecture

The source of truth is one Postgres + PostGIS database. Python pipelines write to it only through packages and changesets, and every write is recorded as a changeset. One SvelteKit app reads the database and serves every surface for people and agents: pages, the REST API, the MCP server, and Markdown and JSON twins. Static artifacts (map tiles, bulk releases, and live snapshots) are files served from object storage behind a CDN.

## Flows

**Writes.** A pipeline fetches raw files, caches them with checksums, and parses them. A new source builds packages: one folder of Parquet tables plus a manifest for each election, its places, and its parties. The packages are checked without a database, then checked again against the database, and loaded in bulk in one transaction that records a published changeset for each. Older pipelines and curated edits emit a changeset instead. The apply step loads it into staging tables and checks it, then either publishes it in one transaction or queues it for review. See [pipelines.md](pipelines.md) and [data-model.md](data-model.md).

**Reads.** Server code in SvelteKit queries Postgres through Drizzle, and pages render on the server. The same oRPC procedures back the app's typed client, the public REST API, and the MCP tools, so all three return the same data.

**Maps.** Pipelines build vector tiles that hold only geometry, one tileset per boundary set, with tippecanoe, and store them as PMTiles. Each tile feature's ID is the area's key. The client fetches results as compact columnar payloads keyed the same way and joins them to the shapes through MapLibre feature state, so switching to another election or year never refetches geometry. Dense boundary sets also get raster tiles that encode area IDs (`ea rasters`). The browser colors them from the same columns, so a national map can draw every precinct without downloading the precinct shapes.

**Live.** On election nights, live workers poll official feeds, or official pages where no feed exists, and arrive through the apply path as preliminary result sets like any other changeset. Each update is a timestamped snapshot, so `/live` pages can follow the count or replay it to any moment.

**Releases.** Bulk releases rebuild the archive as of a release date and write Parquet, CSV, GeoParquet, GeoPackage, and DuckDB files. Each result set ships under its source's license, and sets whose license bars redistribution are left out. SQL access to the archive will run DuckDB over these files, never against the production database.

## Repository layout

```
apps/web/            SvelteKit app: pages, RPC, REST, MCP, twins, feeds, embeds, and Open Graph images
packages/db/         SQL migrations (the source of truth for every table) and the generated Drizzle schema
packages/engine/     Seat allocation and referendum outcomes, with golden tests against real elections
packages/analytics/  Comparisons between elections, change metrics, and indices
packages/view-spec/  The view spec schema, its compiler to renderers, and Vega-Lite export
pipelines/           Python project: one module per source, the apply step, checks, tiles, and live workers
infra/               Docker Compose for local Postgres + PostGIS and Redis
spike/               The schema spike: proven DDL and benchmarks
docs/                These guides
```

- `packages/engine`, `packages/analytics`, and `packages/view-spec` run in the browser and on the server, so keep them free of Node-only APIs.
- Comparisons fetch both sides' totals in SQL, along with crosswalk weights when the two elections use different boundaries, then match options and compute changes in `packages/analytics`. Published result sets never change, so a comparison can be cached by the two result sets and its options.
- Python reads and writes Postgres with plain SQL (psycopg and binary `COPY`). The SQL migrations in `packages/db/migrations` define every table that both sides use, and the TypeScript side gets typed tables by introspecting the database.

## Stack

- **oRPC** for every procedure, with TanStack Query on the client. Procedures live in `apps/web/src/lib/server/rpc/router.ts`. oRPC is pinned to exact versions and upgraded deliberately.
- **Paraglide** for interface text in six locales (English, Turkish, Kurmanji, German, French, and Brazilian Portuguese). Messages live in `apps/web/messages/`. The server renders every locale. The client build ships English only: a Vite plugin (`apps/web/client-messages.ts`) turns each message into a call that returns its English text. So a page carries only the English strings it uses. For any other locale, `hooks.client.ts` loads that locale's whole dictionary (`src/lib/dictionary.ts`) before hydration.
- **Better Auth** with email and password, GitHub and Google (each enabled only when its client ID and secret are set), the admin plugin, and API keys with the `ea_` prefix. Roles beyond Better Auth's own live in `user_role`.
- **Redis** for rate limits and short-lived caches, and **Cloudflare Email Service** for sign-in email, sent through its REST API with a token that can only send.
- **MapLibre** and **PMTiles** for maps.
- Every dependency version lives in the `catalog:` section of `pnpm-workspace.yaml`, and packages reference `catalog:`.

## API surfaces

- **RPC** at `/rpc`, for the app's own typed client.
- **REST** at `/api/v1`, generated by oRPC's OpenAPI handler, with the spec at `/api/v1/openapi.json`. The version is in the path, and breaking changes go to `/api/v2`.
- **MCP** at `/mcp`, over streamable HTTP. The tools wrap the same oRPC procedures and reuse their Valibot schemas as JSON Schema. Every tool only reads and needs no key. Examples are `search`, `get_election`, `get_contest`, `get_party`, and `get_person`.
- **Twins**: every page is also served as Markdown or JSON, through the `Accept` header or a `.md` or `.json` suffix. `/llms.txt` is an index of the site for agents. Crawlers that collect text for AI training (the list is `apps/web/src/lib/server/crawlers.json`) get the Markdown twin at the page's own URL; search engines, assistants fetching a page for a user, and browsers get HTML. Twins are paged by `apps/web/src/lib/server/paging.ts`. A Markdown twin longer than about 128 KB is split at block and line boundaries into pages (`?page=2`); a split table repeats its header row, and each page ends with a link to the next one. A JSON twin larger than 256 KB keeps its shape but shortens its longest lists until it fits, and lists each shortened array under `paging`, keyed by JSON pointer, with its full length, the number of items kept, and a `next` URL. `?list=<pointer>&offset=<n>` returns the next items of that array, paged the same way.
- **Live**: `/live/{election path}` follows or replays an election night, with Markdown and JSON twins that take the same `?at=` timestamp, and REST at `/api/v1/live`.
- **Feeds**: Atom feeds for every polity, election, and contest, and for the corrections log.
- **Embeds**: any view spec renders as a responsive iframe under `/embed/`, with PNG and SVG fallbacks rendered on the server and dynamic Open Graph images.
- Responses carry an ETag derived from the latest published changeset that affects them, so clients and the CDN can revalidate cheaply.

## Access

- **Anyone**: every page, the API, MCP read tools, downloads, embeds, and feeds, all rate-limited per IP in Redis.
- **Returning visitors**: once a page has loaded, the browser sends one `POST /visitor`, which sets `ea_v`, a first-party HttpOnly cookie that lasts a year and is signed with `VISITOR_SECRET`. The request guard (`apps/web/src/lib/server/guard.ts`) checks the signature and rate-limits a visitor with a valid cookie by that cookie instead of by IP, with the same limits. Under load, a process turns away new visitors without the cookie once 36 requests are in flight, and new visitors with one only at 44. During overload, Cloudflare challenges page requests that carry no `ea_v`, so a page request whose `ea_v` fails the check is an attempt to forge a way past the challenge: it gets a 403 response that deletes the cookie. Embeds and signed-in visitors never ask for the cookie. When `VISITOR_SECRET` is unset, no cookie is issued and nobody gets priority.
- **Signed in**: saved views, settings sync, and API keys for higher limits.
- **Roles**: `admin` is used for the API usage dashboard and the election-night controls.

## Search

Search runs in Postgres alone: one denormalized `search_document` row per entity, with `pg_trgm`, `unaccent`, full-text search, and custom folding for Turkish, German, and French characters. The same index serves the command palette, the API, and MCP.

## Hosting

Production is one dedicated server running Postgres, Redis, the app, the API, MCP, and the live workers in Docker. Postgres and Redis listen only on private interfaces. Cloudflare sits in front as the only public way in. For visitors who are not signed in, it caches pages, twins, and the API for five minutes, keeping one copy for each format and each value of the locale cookie, and it caches static assets and map tiles for as long as their headers allow. `/live` pages, their twins, and the live API stay at the edge for only five seconds, so a new count reaches visitors within about 15 seconds. [performance.md](performance.md#the-edge-cache) explains which responses are cached and how the cache is refreshed after a deploy or a publish. Bulk releases are static files on object storage behind `data.electionsarchive.com`.

To learn how pages stay fast, from the procedure cache and warm-up to the database settings and query patterns, read [performance.md](performance.md).
