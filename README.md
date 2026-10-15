# electionsarchive.com

This is an election archive for people and agents, meant ultimately to cover every election in the world. It starts with ten polities at full depth: the United States, the United Kingdom, France, Brazil, Germany, Canada, Italy, Turkey, the European Union, and Switzerland. For each, the data goes down to the smallest reporting unit and back to the first election. One Postgres and PostGIS database is the source of truth. It feeds the website, a REST API, an MCP server, Markdown and JSON versions of every page, and bulk data releases.

## Repository

| Path                 | Contents                                                                     |
| -------------------- | ---------------------------------------------------------------------------- |
| `apps/web`           | SvelteKit app: pages, the RPC and REST APIs, and sign-in                     |
| `packages/db`        | SQL migrations and the Drizzle schema                                        |
| `packages/engine`    | Seat allocation and referendum outcomes, tested against real elections       |
| `packages/view-spec` | The JSON view spec schema                                                    |
| `pipelines`          | Python pipelines that fetch, parse, and publish results as changesets        |
| `infra`              | Docker Compose for local Postgres and Redis                                  |
| `spike`              | The schema spike: proven DDL, benchmarks, and the design lab's data exporter |

## Requirements

- Node.js 24 and pnpm 12
- uv (it installs Python 3.14 for the pipelines)
- Postgres 18 with PostGIS 3.6, and Redis 8 (or Docker to run both)

## Getting started

```bash
pnpm install
cp .env.example .env
docker compose -f infra/compose.yaml up -d
pnpm --filter @ea/db migrate
pnpm dev
```

Before starting the app, fill in `BETTER_AUTH_SECRET` and `RESEND_API_KEY` in `.env`. GitHub and Google sign-in turn on once their client ID and secret are set. The app runs at http://localhost:5173.

## Loading data

Every write to the archive goes through a changeset. To load Canada's federal elections from 1867 to 2025 and join candidates who ran more than once from 2004 on, run the commands below in order. The Library of Parliament changeset for 1867 to 2004 also cites Wikidata, so it waits for review.

```bash
cd pipelines
uv run ea fetch ca.elections
uv run ea parse ca.elections
uv run ea apply "$(uv run ea emit ca.elections)"
uv run ea apply "$(uv run ea emit ca.match)"
uv run ea fetch ca.lop
uv run ea parse ca.lop
uv run ea apply "$(uv run ea emit ca.lop)"
```

Changesets from official sources publish automatically when every check passes. Anything else waits for review and is published with `uv run ea publish <changeset-id>`.

## Checks

```bash
pnpm check
pnpm test
pnpm lint
pnpm format
```

`pnpm run ci` runs all four. For the pipelines, run `uv run ruff check` and `uv run ty check` inside `pipelines`.

## License

The data is published under CC BY 4.0. The code stays private until launch.
