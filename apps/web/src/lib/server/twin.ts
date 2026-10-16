import type { RequestEvent } from "@sveltejs/kit";
import { call } from "@orpc/server";
import { error, isHttpError, isRedirect, redirect } from "@sveltejs/kit";
import { count, date, percent, points } from "#lib/format.js";
import { m } from "#lib/paraglide/messages.js";
import { archive, home, party, person } from "#lib/server/pages.js";
import { router } from "#lib/server/rpc/router.js";

type Data = Awaited<
  ReturnType<typeof home | typeof person | typeof party | typeof archive>
>;
type Link = (name: string, path: string | null) => string;

const TYPES = {
  json: "application/json; charset=utf-8",
  md: "text/markdown; charset=utf-8",
} as const;

const ACCEPTS: Record<string, keyof typeof TYPES | "html"> = {
  "application/json": "json",
  "text/markdown": "md",
  "text/html": "html",
  "text/*": "html",
  "*/*": "html",
};

const KINDS: Record<string, () => string> = {
  party: m.party_kind_party,
  independent_group: m.party_kind_independent_group,
  list_vehicle: m.party_kind_list_vehicle,
  federation: m.party_kind_federation,
  bloc: m.party_kind_bloc,
  nuance: m.party_kind_nuance,
  parliamentary_group: m.party_kind_parliamentary_group,
};

const OUTCOMES: Record<string, () => string> = {
  elected: m.outcome_elected,
  won: m.outcome_won,
  advanced: m.outcome_advanced,
  tied: m.outcome_tied,
};

const outcome = (value: string | null) =>
  value == null ? null : (OUTCOMES[value]?.() ?? value);

const span = (first: number | null, last: number | null) => {
  if (first == null || last == null) {
    return "";
  }
  return first === last
    ? String(first)
    : m.polity_span({ first: String(first), last: String(last) });
};

const table = (
  head: Array<string>,
  rows: Array<Array<string | number | null>>,
) =>
  [
    head,
    head.map(() => "---"),
    ...rows.map((row) =>
      row.map((value) =>
        String(value ?? "")
          .replaceAll("|", String.raw`\|`)
          .replaceAll("\n", " "),
      ),
    ),
  ]
    .map((row) => `| ${row.join(" | ")} |`)
    .join("\n");

const byline = (
  crumbs: Array<{ name: string; path: string | null }>,
  link: Link,
  when: string | null,
) =>
  [crumbs.map((crumb) => link(crumb.name, crumb.path)).join(" / "), date(when)]
    .filter((part) => part !== "")
    .join(", ");

function negotiate(accept: string | null) {
  const weights = { html: 0, json: 0, md: 0 };
  const parts = accept?.split(",") ?? [];
  for (const part of parts) {
    const [type = "", ...parameters] = part.trim().toLowerCase().split(";");
    const key = ACCEPTS[type];
    const q = parameters
      .map((parameter) => parameter.trim())
      .find((parameter) => parameter.startsWith("q="));
    if (key != null) {
      weights[key] = Math.max(weights[key], Number(q?.slice(2) ?? 1));
    }
  }
  if (weights.json > weights.html && weights.json >= weights.md) {
    return "json";
  }
  return weights.md > weights.html ? "md" : null;
}

async function load(segments: Array<string>, search: URLSearchParams) {
  const [head = "", ...rest] = segments;
  if (segments.length === 0) {
    return home();
  }
  if (head === "id" && rest.length === 1) {
    const { path } = await call(router.entities.path, { id: rest[0] ?? "" });
    if (path == null) {
      error(404);
    }
    redirect(307, `/${path}`);
  }
  if (head === "people" && rest.length === 1) {
    return person(rest[0] ?? "");
  }
  if (head === "parties" && rest.length > 0) {
    return party(rest.join("/"));
  }
  return archive(segments.join("/"), search);
}

export async function twin({ request, url }: RequestEvent) {
  if (
    !["GET", "HEAD"].includes(request.method) ||
    /^\/(?:api|rpc|lab|_app|llms\.txt|sitemap\.xml|sitemaps|og)(?:\/|\.|$)/.test(
      url.pathname,
    )
  ) {
    return null;
  }
  const suffix = /\.(json|md)$/.exec(url.pathname)?.[1] as
    | keyof typeof TYPES
    | undefined;
  const format = suffix ?? negotiate(request.headers.get("accept"));
  if (format == null) {
    return null;
  }
  const pathname =
    suffix == null
      ? url.pathname
      : url.pathname.slice(0, -suffix.length - 1).replace(/^\/index$/, "") ||
        "/";
  const headers = {
    "content-type": TYPES[format],
    vary: "Accept",
    link: `<${url.origin}${pathname}>; rel="canonical"`,
  };
  try {
    const data = await load(
      pathname.split("/").filter((segment) => segment !== ""),
      url.searchParams,
    );
    if (format === "json") {
      return Response.json(data, { headers });
    }
    const base = `${url.origin}${pathname === "/" ? "/index" : pathname}`;
    const link: Link = (name, path) =>
      path == null ? name : `[${name}](${url.origin}/${path}.md)`;
    return new Response(
      `${markdown(data, link).join("\n\n")}\n\n${m.twin_alternates({
        html: `${url.origin}${pathname}`,
        json: `${base}.json`,
      })}\n`,
      { headers },
    );
  } catch (error_) {
    return failure(error_, suffix, format, headers);
  }
}

function failure(
  error_: unknown,
  suffix: string | undefined,
  format: keyof typeof TYPES,
  headers: Record<string, string>,
) {
  if (isRedirect(error_)) {
    const [path = "", query] = error_.location.split("?", 2);
    const location =
      suffix == null
        ? error_.location
        : `${path === "/" ? "/index" : path}.${suffix}${query == null ? "" : `?${query}`}`;
    return new Response(null, {
      status: error_.status,
      headers: { ...headers, location },
    });
  }
  if (!isHttpError(error_)) {
    throw error_;
  }
  const { status } = error_;
  const message = status === 404 ? m.twin_not_found() : String(status);
  return format === "json"
    ? Response.json({ error: message }, { status, headers })
    : new Response(`# ${message}\n`, { status, headers });
}

function markdown(data: Data, link: Link) {
  switch (data.kind) {
    case "home": {
      return [
        `# ${m.wordmark()}`,
        m.home_intro(),
        `## ${m.home_polities()}`,
        table(
          [
            m.col_polity(),
            m.polity_elections(),
            m.person_years(),
            m.home_latest(),
          ],
          data.polities.map((polity) => [
            link(polity.name, polity.path),
            count(polity.elections + polity.within),
            span(polity.first, polity.last),
            polity.latest == null
              ? null
              : link(polity.latest.name, polity.latest.path),
          ]),
        ),
      ];
    }
    case "person": {
      return personMarkdown(data.person, link);
    }
    case "party": {
      return partyMarkdown(data, link);
    }
    case "polity": {
      return polityMarkdown(data.polity, link);
    }
    case "election": {
      return electionMarkdown(data.election, link);
    }
    default: {
      return contestMarkdown(data.contest, link);
    }
  }
}

function personMarkdown(
  person: Extract<Data, { kind: "person" }>["person"],
  link: Link,
) {
  const born =
    person.born == null
      ? null
      : person.precision === "day"
        ? m.card_born_date({ date: date(person.born) })
        : m.card_born({ year: person.born.slice(0, 4) });
  const wikidata =
    person.wikidata == null
      ? null
      : `[${m.card_wikidata()}](https://www.wikidata.org/wiki/${person.wikidata})`;
  return [
    `# ${person.name}`,
    [person.party?.label, born, wikidata]
      .filter((part) => part != null)
      .join(", "),
    `## ${m.person_record()}`,
    person.record.length === 0
      ? m.person_empty()
      : table(
          [
            m.col_year(),
            m.col_contest(),
            m.col_party(),
            m.col_share(),
            m.col_result(),
          ],
          person.record.map((entry) => [
            entry.year,
            link(entry.name, entry.path),
            entry.party?.label ?? null,
            entry.votes == null || entry.total == null || entry.total === 0
              ? null
              : percent(entry.votes / entry.total),
            outcome(entry.outcome),
          ]),
        ),
  ].filter((block) => block !== "");
}

function partyMarkdown(
  { party, record }: Extract<Data, { kind: "party" }>,
  link: Link,
) {
  const related = [
    ...party.relations.map(
      (relation) =>
        `- ${relation.kind.replaceAll("_", " ")}: ${link(relation.party.label, `id/${relation.party.id}`)}${relation.date == null ? "" : ` (${relation.date.slice(0, 4)})`}`,
    ),
    ...party.parents.map(
      (parent) =>
        `- ${parent.kind.replaceAll("_", " ")}: ${link(parent.party.label, `id/${parent.party.id}`)}`,
    ),
  ];
  return [
    `# ${party.name}${party.short != null && party.short !== party.name ? ` (${party.short})` : ""}`,
    ...(party.polity == null
      ? []
      : [
          m.party_in({
            kind: KINDS[party.kind]?.() ?? party.kind,
            polity: party.polity.name,
          }),
        ]),
    ...(related.length === 0
      ? []
      : [`## ${m.party_relations()}`, related.join("\n")]),
    `## ${m.party_elections()}`,
    record.elections.length === 0
      ? m.party_none()
      : table(
          [m.col_year(), m.col_election(), m.party_contests(), m.person_won()],
          record.elections.map((election) => [
            election.year,
            link(election.name, election.path),
            count(election.races),
            count(election.won),
          ]),
        ),
    ...(record.count > record.elections.length
      ? [
          m.party_record_more({
            shown: count(record.elections.length),
            count: count(record.count),
          }),
        ]
      : []),
  ];
}

function polityMarkdown(
  polity: Extract<Data, { kind: "polity" }>["polity"],
  link: Link,
) {
  return [
    `# ${polity.name}`,
    byline(polity.breadcrumbs, link, null),
    `## ${m.polity_elections()}`,
    polity.elections.length === 0
      ? m.polity_no_elections()
      : table(
          [m.col_date(), m.col_election()],
          polity.elections.map((election) => [
            election.date ?? election.year,
            link(election.name, election.path),
          ]),
        ),
    ...(polity.results.length === 0
      ? []
      : [
          `## ${m.polity_results_title({ place: polity.name })}`,
          table(
            [m.col_date(), m.col_contest(), m.col_leader(), m.col_share()],
            polity.results.flatMap((group) =>
              group.races.map((race) => [
                group.date,
                link(race.name, race.path),
                race.leaders[0]?.label ?? null,
                race.leaders[0] == null ? null : percent(race.leaders[0].share),
              ]),
            ),
          ),
        ]),
    ...(polity.polities.length === 0
      ? []
      : [
          `## ${m.polity_subdivisions()}`,
          table(
            [m.col_name(), m.polity_elections(), m.person_years()],
            polity.polities.map((child) => [
              link(child.name, child.path),
              count(child.elections),
              span(child.first, child.last),
            ]),
          ),
        ]),
  ].filter((block) => block !== "");
}

function electionMarkdown(
  election: Extract<Data, { kind: "election" }>["election"],
  link: Link,
) {
  return [
    `# ${election.name}`,
    byline(election.breadcrumbs, link, election.date),
    ...election.notes.map((note) => `> ${note.body}`),
    ...election.races.flatMap((race) => {
      const hasSeats = race.results.some((row) => row.seats != null);
      const seats = race.electors == null ? m.col_seats() : m.col_electors();
      return [
        `## ${link(race.name, race.path)}`,
        table(
          [
            m.col_option(),
            m.col_votes(),
            m.col_share(),
            ...(hasSeats ? [seats] : []),
          ],
          race.results.map((row) => [
            row.label,
            count(row.votes),
            percent(row.share),
            ...(hasSeats ? [row.seats == null ? null : count(row.seats)] : []),
          ]),
        ),
      ];
    }),
    ...(election.contests.length === 0
      ? []
      : [
          `## ${m.election_contests()}`,
          election.contests
            .map((contest) => `- ${link(contest.name, contest.path)}`)
            .join("\n"),
        ]),
    ...(election.elections.length === 0
      ? []
      : [
          `## ${m.election_children()}`,
          election.elections
            .map(
              (child) =>
                `- ${link(child.name, child.path)}${child.date == null ? "" : `, ${date(child.date)}`}`,
            )
            .join("\n"),
        ]),
  ].filter((block) => block !== "");
}

function contestMarkdown(
  contest: Extract<Data, { kind: "contest" }>["contest"],
  link: Link,
) {
  const hasSeats = contest.rows.some((row) => row.current?.seats != null);
  const results = contest.rows.flatMap((row) =>
    row.current == null
      ? []
      : [
          [
            row.label,
            count(row.current.votes),
            percent(row.current.share),
            ...(hasSeats
              ? [row.current.seats == null ? null : count(row.current.seats)]
              : []),
            outcome(row.current.outcome),
          ],
        ],
  );
  return [
    `# ${contest.name}`,
    byline(contest.breadcrumbs, link, contest.election.date),
    contest.scope.trail.length > 1
      ? contest.scope.trail
          .map((step) => link(step.name, step.path))
          .join(" / ")
      : "",
    ...contest.notes.map((note) => `> ${note.body}`),
    contest.current == null || results.length === 0
      ? m.no_results()
      : `${table(
          [
            m.col_option(),
            m.col_votes(),
            m.col_share(),
            ...(hasSeats ? [m.col_seats()] : []),
            m.col_result(),
          ],
          results,
        )}\n\n${m.stat_valid()}: ${count(contest.current.valid)}`,
    ...placesMarkdown(contest.places, link),
    ...(contest.children.length === 0
      ? []
      : [
          `## ${m.children_title()}`,
          contest.children
            .map((child) => `- ${link(child.name, child.path)}`)
            .join("\n"),
        ]),
    ...(contest.series.length < 2
      ? []
      : [
          `## ${m.series_label()}`,
          contest.series
            .map(
              (entry) =>
                `- ${entry.current ? entry.name : link(entry.name, entry.path)}${entry.winner == null ? "" : `: ${entry.winner.label}, ${percent(entry.winner.share)}`}`,
            )
            .join("\n"),
        ]),
    ...sourcesMarkdown(contest),
  ].filter((block) => block !== "");
}

function placesMarkdown(
  places: Extract<Data, { kind: "contest" }>["contest"]["places"],
  link: Link,
) {
  if (places == null || places.rows.length === 0) {
    return [];
  }
  const noun =
    places.levels.find((level) => level.code === places.level)?.noun ??
    places.level;
  return [
    `## ${m.places_title({ noun })}`,
    table(
      [
        m.col_name(),
        m.col_leader(),
        m.col_share(),
        m.col_margin(),
        m.stat_valid(),
      ],
      places.rows.map((row) => [
        link(row.name, row.path),
        row.leader == null ? null : (places.legend[row.leader]?.label ?? null),
        row.leader == null ? null : percent(row.shares[row.leader] ?? 0),
        row.margin == null ? null : points(row.margin),
        count(row.valid),
      ]),
    ),
  ];
}

function sourcesMarkdown(
  contest: Extract<Data, { kind: "contest" }>["contest"],
) {
  const sources = [
    ...contest.sets.map(
      ({ source }) =>
        `- ${source.url == null ? source.title : `[${source.title}](${source.url})`}, ${source.publisher}${source.license == null ? "" : `. ${m.source_license({ license: source.license })}`}`,
    ),
    ...contest.citations.map(
      (citation) =>
        `- ${citation.url == null ? citation.title : `[${citation.title}](${citation.url})`}, ${citation.publisher}`,
    ),
  ];
  return sources.length === 0
    ? []
    : [`## ${m.sources_title()}`, sources.join("\n")];
}
