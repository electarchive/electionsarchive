<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/banner-dark.webp">
  <img src="docs/images/banner-light.webp" alt="Elections Archive: every election in the world, down to the ballot box. 964,460 contests, 224,962 elections, 67,416 places that hold elections, and the earliest election in 1787." width="100%">
</picture>

[![CI](https://github.com/electarchive/electionsarchive/actions/workflows/ci.yml/badge.svg)](https://github.com/electarchive/electionsarchive/actions/workflows/ci.yml)
[![Code: AGPL-3.0](https://img.shields.io/badge/code-AGPL--3.0-15171a)](LICENSE)
[![Data: CC BY 4.0](https://img.shields.io/badge/data-CC%20BY%204.0-15171a)](https://creativecommons.org/licenses/by/4.0/)
[![MCP server](https://img.shields.io/badge/MCP-ready-ffe03a)](https://electionsarchive.com/agents)

[Website](https://electionsarchive.com) · [API](https://electionsarchive.com/api/v1/openapi.json) · [Connect your agent](https://electionsarchive.com/agents) · [How the data gets in](docs/pipelines.md) · [Contributing](CONTRIBUTING.md)

</div>

Elections Archive holds results for more than 960,000 contests in 224,000 elections, from the 1787 elections in the United States to this year's, by precinct, polling station, and ballot box where the official count goes that deep. Every number links to the source that published it.

<p align="center">
  <img src="docs/images/map.webp" alt="County map of the 2024 United States presidential election on electionsarchive.com, shaded by margin, with a color key and a histogram of county margins" width="100%">
</p>

- **Every number cites its source.** Each figure traces to a source and to the changeset that wrote it. Figures read from scanned books keep the page and the box on that page they came from.
- **Down to the finest unit.** Precincts in the US, ballot boxes in Türkiye, polling stations in France, sections in Brazil, and polling districts in Germany.
- **Declared, then checked.** Official winners and seats are stored as declared. A seat engine, tested against real elections, recomputes them for comparison and never overwrites them.
- **Live on election night.** Returns update as counties report, and every race has a share card that redraws as the count moves.
- **Built for agents.** Every page is also Markdown and JSON, the REST API is described by OpenAPI, and an MCP server and an agent skill expose the same data.
- **Open to corrections.** Anyone can propose a fix with a citation. Proposals, evidence, and decisions are public.

## What's inside

| Country           | Since | Finest unit       |
| ----------------- | ----- | ----------------- |
| 🇺🇸 United States  | 1787  | precinct          |
| 🇬🇧 United Kingdom | 1832  | ward              |
| 🇨🇭 Switzerland    | 1848  | commune           |
| 🇨🇦 Canada         | 1867  | polling division  |
| 🇹🇷 Türkiye        | 1923  | ballot box        |
| 🇮🇹 Italy          | 1948  | polling section   |
| 🇩🇪 Germany        | 1949  | polling district  |
| 🇫🇷 France         | 1958  | polling station   |
| 🇪🇺 European Union | 1979  | member state      |
| 🇮🇪 Ireland        | 1979  | Dáil constituency |
| 🇧🇷 Brazil         | 1994  | section           |

Depth varies by country and year. The [coverage page](https://electionsarchive.com/coverage) measures each country against an outside count of its elections and lists what is still missing.

## Every race, ready to share

<p align="center">
  <img src="docs/images/share-card.webp" alt="Share card for the 2024 Pennsylvania US Senate race: both candidates with vote shares and totals, a winner tag, a share bar, and a county map shaded by the leading party" width="100%">
</p>

Each contest page has a card like this one, drawn from the same numbers as the page. Add `.png` to a page's path under `/og/live/` to get it; during a count it redraws every minute.

## Use it

Add `.md` or `.json` to any page:

```bash
curl https://electionsarchive.com/us/2024-11-05-general/president.md
```

Search and query through REST:

```bash
curl "https://electionsarchive.com/api/v1/search?q=istanbul"
```

### Connect your agent

Install the skill, which teaches an agent the site's URLs, twins, and API:

```bash
npx skills add electarchive/electionsarchive
```

Or add the MCP server. In Claude Code:

```bash
claude mcp add --transport http electionsarchive https://electionsarchive.com/mcp
```

In any other client:

```json
{
  "mcpServers": {
    "electionsarchive": {
      "type": "http",
      "url": "https://electionsarchive.com/mcp"
    }
  }
}
```

Reading needs no key. Proposing changes needs one from [your settings](https://electionsarchive.com/settings/keys). The [agents page](https://electionsarchive.com/agents) has setup for other clients and example questions.

## How the data gets in

```mermaid
flowchart LR
  S["Official results, books, and scans"] --> P["Pipelines<br>fetch, parse, emit"]
  U["People and agents"] --> X["Proposals"]
  P --> C["Changesets"]
  X --> C
  C --> A{"Checks and review"}
  A --> D[("Postgres + PostGIS")]
  D --> W["Website"]
  D --> R["REST and MCP"]
  D --> T["Markdown and JSON twins"]
  D --> B["Bulk releases"]
```

One database is the source of truth, and the only way to write to it is a changeset. Each source has a pipeline that fetches the official files into a raw cache, parses them, and emits a changeset; people and agents propose changesets the same way. Every changeset passes the same checks before it is published. Scanned books are digitized page by page, and each figure keeps its page and position. [docs/pipelines.md](docs/pipelines.md) walks through the whole workflow with Canada's federal elections as the example, and [docs/architecture.md](docs/architecture.md) and [docs/data-model.md](docs/data-model.md) cover the rest.

## Run it locally

You need Node.js 24, pnpm 12, [uv](https://docs.astral.sh/uv/), and Docker (or Postgres 18 with PostGIS 3.6 and Redis 8).

```bash
pnpm install
cp .env.example .env
docker compose -f infra/compose.yaml up -d
pnpm --filter @ea/db migrate
pnpm dev
```

The app runs at http://localhost:5173. Fill in `BETTER_AUTH_SECRET` in `.env` first, and `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_EMAIL_TOKEN` if you want sign-in emails. Then load real data, for example Canada's federal elections since 1867:

```bash
cd pipelines
uv run ea fetch ca.elections
uv run ea parse ca.elections
uv run ea apply "$(uv run ea emit ca.elections)"
```

[CONTRIBUTING.md](CONTRIBUTING.md) covers the seed dump, the checks, and the rest of the setup.

## Repository

| Path                 | Contents                                                               |
| -------------------- | ---------------------------------------------------------------------- |
| `apps/web`           | SvelteKit app: pages, RPC, REST, MCP, twins, and sign-in               |
| `packages/db`        | SQL migrations and the Drizzle schema                                  |
| `packages/engine`    | Seat allocation and referendum outcomes, tested against real elections |
| `packages/analytics` | Comparisons between elections, change metrics, and indices             |
| `packages/view-spec` | The JSON view spec schema                                              |
| `pipelines`          | Python pipelines that fetch, parse, and publish results as changesets  |
| `infra`              | Docker Compose for local Postgres and Redis                            |
| `spike`              | The schema spike: proven DDL and benchmarks                            |
| `docs`               | Guides for contributors                                                |

## Contributing

Code comes in through pull requests, and data corrections come in through the site: open the page with the mistake, choose Edit, and cite your source. See [CONTRIBUTING.md](CONTRIBUTING.md), and report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## License

The code is licensed under the [GNU Affero General Public License v3.0](LICENSE). The data is published under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), except where a source's own license differs; each result set records its source's license.
