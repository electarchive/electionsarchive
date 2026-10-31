# Contributing

Thanks for helping. There are three ways in, and each has its own path:

- **A wrong number, name, or shape on the site.** Open the page with the mistake, choose Edit, and cite your source. Your proposal goes to review and, once accepted, is published as a changeset like any other. You don't need this repository for that. If you can't fix it yourself, open a [data error issue](https://github.com/electarchive/electionsarchive/issues/new?template=data-error.yml).
- **A source we don't have yet.** Open a [new source issue](https://github.com/electarchive/electionsarchive/issues/new?template=new-source.yml) before writing a pipeline, so we can tell you whether it is already planned or partly loaded. Then read [docs/pipelines.md](docs/pipelines.md).
- **Code.** Bug fixes and small improvements can go straight to a pull request. For anything larger, such as a new page, a schema change, or a new API surface, open an issue first and describe the change.

## Set up

You need Node.js 24, pnpm 12, [uv](https://docs.astral.sh/uv/), and Docker (or Postgres 18 with PostGIS 3.6 and Redis 8).

```bash
pnpm install
cp .env.example .env
docker compose -f infra/compose.yaml up -d
pnpm --filter @ea/db migrate
pnpm dev
```

Set `BETTER_AUTH_SECRET` in `.env` to any long random string. `RESEND_API_KEY` is only needed to send sign-in emails, and the GitHub and Google keys are only needed for those sign-in buttons.

The app runs at http://localhost:5173 and starts empty. To load real data, run a pipeline. Canada's federal elections since 1867 take a few minutes:

```bash
cd pipelines
uv sync
uv run ea fetch ca.elections
uv run ea parse ca.elections
uv run ea apply "$(uv run ea emit ca.elections)"
```

A small seed dump with one US state and one country will be linked here, so you can skip the pipeline step.

## Before you open a pull request

Run the same checks as CI. For the app and packages:

```bash
pnpm run compile-paraglide
pnpm run check
pnpm run test
pnpm run lint
pnpm run format
```

`pnpm run format:fix` and `pnpm run lint:fix` fix what they can. For the pipelines:

```bash
cd pipelines
uv run ruff check
uv run ruff format --check
uv run ty check
```

The modules listed in `pipelines/ty.toml` were written before the type check, so ty skips some rules for them. New modules get no such exemption. When you fix the types in a listed module, delete it from the list in the same pull request.

Keep each pull request to one change, and say how you checked it: the page you looked at, the command you ran, or the election whose numbers you compared.

## Conventions

- **Every write goes through a changeset.** Nothing writes to the core tables directly: pipelines emit changesets, people submit proposals that become changesets, and `ea apply` publishes them after the checks pass. See [docs/data-model.md](docs/data-model.md).
- **Every number has a source.** A result set names its source, and numbers read from scans also record the page and the bounding box they came from.
- **Declared results stay as declared.** Official outcomes and seat counts are stored as the authority published them. The engine in `packages/engine` recomputes seats only to compare against them.
- **Svelte 5 with runes, strict TypeScript.** No `any`; use `unknown` and narrow it.
- **Interface text lives in `apps/web/messages/<locale>.json`.** Never edit `src/lib/paraglide/` by hand; run `pnpm run compile-paraglide` after changing messages.
- **American English** in code, copy, and docs.
- **Commit messages** start with the area they touch, in lowercase: `web: /coverage local-election wording`, `license: AGPL-3.0`.

## License

By contributing, you agree that your code is licensed under the [AGPL-3.0](LICENSE) and your data contributions under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), the same terms as the rest of the project.

Report security issues privately, as described in [SECURITY.md](SECURITY.md), and not in a public issue.
