# electionsarchive.com

An election archive for humans and agents: every election in the world, down to the finest reporting unit and back to each polity's first election. One Postgres + PostGIS source of truth serves a SvelteKit app, a REST API, an MCP server, Markdown and JSON versions of each page, and bulk releases.

## Read first

Read the matching file before working in its area:

- `docs/architecture.md`: flows, repository layout, stack, API surfaces, access, and hosting. Read it before adding a package, service, route family, or dependency.
- `docs/data-model.md`: vocabulary, conventions, and tables. Read it before touching the database, changeset formats, or anything about results, geography, parties, people, offices, or provenance.
- `docs/pipelines.md`: the `ea` CLI, the raw cache, apply and checks, digitization, entity resolution, and live ingestion. Read it before writing or running a pipeline.
- `CONTRIBUTING.md`: setup, the checks CI runs, and conventions.

## Invariants

- Every write to core tables is a changeset through the apply path (`uv run ea apply`). Never write to core tables with ad hoc SQL.
- Until launch, a re-emit replaces the facts of a result set its own pipeline last wrote, so a pipeline's own bugs are fixed in place; a disagreeing or finer source publishes a superseding result set. After launch, a published result set is immutable.
- Every number traces to a source and a changeset; digitized numbers also trace to a page and bounding box.
- Official outcomes and seats are stored as declared; engine output is derived and only ever compared against them.
- Locales never occupy a URL path segment.

## Commands

- JavaScript: `pnpm install`, `pnpm dev` (app on http://localhost:5173), `pnpm run check`, `pnpm run test` (engine golden tests), `pnpm run lint`, and `pnpm run format`. The `:fix` variants of lint and format apply fixes.
- Interface text: edit `apps/web/messages/<locale>.json`, then run `pnpm run compile-paraglide`. Never edit `apps/web/src/lib/paraglide/`.
- Database: `docker compose -f infra/compose.yaml up -d`, then `pnpm --filter @ea/db migrate`. The migrations in `packages/db/migrations` are the source of truth for the DDL.
- Python, from `pipelines/`: `uv sync`, `uv run ea --help`, `uv run ruff check`, `uv run ruff format`, and `uv run ty check`.

## Conventions

- Svelte 5 with runes. Strict TypeScript: no `any`; use `unknown` and narrow it.
- Keep `packages/engine`, `packages/analytics`, and `packages/view-spec` free of Node-only APIs, because they also run in the browser.
- Every dependency version lives in the `catalog:` section of `pnpm-workspace.yaml`.
- American English in code, copy, and docs.
- Commit messages start with the area they touch, in lowercase (`web: ...`, `engine: ...`, `ca: ...`). Keep one change per pull request, and say how it was checked.
- Never commit secrets or files under `data/`. `.env.example` lists every variable.
