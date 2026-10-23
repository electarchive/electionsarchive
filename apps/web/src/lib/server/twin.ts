import type { RequestEvent } from "@sveltejs/kit";
import { call } from "@orpc/server";
import { error, isHttpError, isRedirect, redirect } from "@sveltejs/kit";
import { effective, stand } from "#lib/caucus.js";
import { PARTS, label as reasonLabel, REASONS } from "#lib/coverage.js";
import { count, date, percent, points } from "#lib/format.js";
import { m } from "#lib/paraglide/messages.js";
import { AFFILIATIONS, RELATIONS } from "#lib/relations.js";
import {
  archive,
  chamber,
  comparison,
  coverage,
  home,
  party,
  person,
  results,
} from "#lib/server/pages.js";
import { router } from "#lib/server/rpc/router.js";

type Data = Awaited<ReturnType<typeof load>>;
type Link = (name: string, path: string | null) => string;

const TITLES = {
  county: m.coverage_goal_county,
  history: m.coverage_goal_history,
  turnout: m.coverage_goal_turnout,
  precinct: m.coverage_goal_precinct,
  shapes: m.coverage_goal_shapes,
  shapes_history: m.coverage_goal_shapes_history,
  local: m.coverage_goal_local,
};

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
  caucus: m.party_kind_caucus,
};

const OUTCOMES: Record<string, () => string> = {
  elected: m.outcome_elected,
  won: m.outcome_won,
  advanced: m.outcome_advanced,
  tied: m.outcome_tied,
  lost: m.person_lost_mark,
  withdrew: m.outcome_withdrew,
  disqualified: m.outcome_disqualified,
};

const outcome = (value: string | null) =>
  value == null ? null : (OUTCOMES[value]?.() ?? value);

const leading = (
  row: {
    leader: number | null;
    shares: Array<number>;
    valid: number;
    isRecorded?: boolean;
    declared?: { kind: string; isTacit: boolean; label: string } | null;
  },
  legend: Array<{ label: string }>,
) => {
  if (row.valid === 0 && row.declared != null) {
    return [
      row.leader == null
        ? row.declared.label
        : (legend[row.leader]?.label ?? null),
      row.declared.isTacit ? m.map_unopposed() : outcome(row.declared.kind),
    ];
  }
  if (row.leader == null) {
    return [row.isRecorded === false ? m.places_unrecorded() : null, null];
  }
  return [
    legend[row.leader]?.label ?? null,
    percent(row.shares[row.leader] ?? 0),
  ];
};

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
    return party(rest.join("/"), search.get("in"));
  }
  if (head === "chambers" && rest.length > 0) {
    return chamber(rest.join("/"));
  }
  switch (rest.length === 0 ? head : "") {
    case "coverage": {
      return coverage(search);
    }
    case "compare": {
      return comparison(
        [search.get("a"), search.get("b")].filter((id) => id != null),
      );
    }
    case "search": {
      return results(search);
    }
  }
  const found = await archive(segments.join("/"), search);
  return found.kind === "polity"
    ? { ...found, record: await call(router.polities.record, { id: found.id }) }
    : found;
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
      return Response.json(
        data.kind === "home" ? { ...data, atlas: undefined } : data,
        { headers },
      );
    }
    const base = `${url.origin}${pathname === "/" ? "/index" : pathname}`;
    const link: Link = (name, path) => {
      if (path == null) {
        return name;
      }
      const [route = "", query] = path.split("?", 2);
      return `[${name}](${url.origin}/${route}.md${query == null ? "" : `?${query}`})`;
    };
    return new Response(
      `${markdown(data, link).join("\n\n")}\n\n${m.twin_alternates({
        html: `${url.origin}${pathname}${url.search}`,
        json: `${base}.json${url.search}`,
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
    case "search": {
      const groups = [
        ["polity", m.search_group_polity()],
        ["body", m.search_group_body()],
        ["election", m.search_group_election()],
        ["contest", m.search_group_contest()],
        ["party", m.search_group_party()],
        ["person", m.search_group_person()],
      ] as const;
      return [
        `# ${m.search_title()}`,
        ...(data.q === ""
          ? [m.search_hint()]
          : data.hits.length === 0
            ? [m.search_empty({ query: data.q })]
            : groups.flatMap(([type, heading]) => {
                const hits = data.hits.filter((hit) => hit.type === type);
                return hits.length === 0
                  ? []
                  : [
                      `## ${heading}`,
                      hits
                        .map(
                          (hit) =>
                            `- ${[link(hit.name, hit.path), hit.context, date(hit.date)].filter((part) => part != null && part !== "").join(", ")}`,
                        )
                        .join("\n"),
                    ];
              })),
      ];
    }
    case "home": {
      return homeMarkdown(data, link);
    }
    case "coverage": {
      return coverageMarkdown(data, link);
    }
    case "person": {
      return personMarkdown(data.person, data.parties, link);
    }
    case "party": {
      return partyMarkdown(data, link);
    }
    case "chamber": {
      return chamberMarkdown(data, link);
    }
    case "comparison": {
      return comparisonMarkdown(data, link);
    }
    case "polity": {
      return polityMarkdown(data.polity, data.record, link);
    }
    case "election": {
      return electionMarkdown(data.election, link);
    }
    default: {
      return contestMarkdown(data.contest, data.cycle, link);
    }
  }
}

function coverageMarkdown(
  data: Extract<Data, { kind: "coverage" }>,
  link: Link,
) {
  const year = String(new Date().getFullYear());
  const share = (row: { complete: number; total: number }) =>
    m.coverage_percent({
      percent: percent(row.complete / Math.max(1, row.total), 1),
    });
  return [
    `# ${m.coverage_title()}`,
    m.coverage_lede(),
    (data.current ? m.twin_coverage_current_on : m.twin_coverage_current)({
      year,
    }),
    ...data.goals.flatMap((row) => [
      `## ${TITLES[row.goal]()}`,
      [
        `${(row.goal === "local" ? m.coverage_progress_local : row.goal === "turnout" ? m.coverage_progress_turnout : m.coverage_progress)({ complete: count(row.complete), total: count(row.total) })}, ${share(row)}`,
        ...(row.icpsr > 0
          ? [m.coverage_icpsr_of({ count: count(row.icpsr) })]
          : []),
      ].join(", "),
      ...(row.goal === "history" || row.goal === "turnout"
        ? [
            data.parts
              .filter((part) => part.goal === row.goal)
              .map(
                (part) =>
                  `- ${PARTS[part.part]({ complete: count(part.complete), total: count(part.total), percent: percent(part.complete / Math.max(1, part.total), 1) })}`,
              )
              .join("\n"),
            (row.goal === "history"
              ? m.coverage_goal_history_note
              : m.coverage_goal_turnout_note)(),
          ]
        : []),
      REASONS[row.goal]
        .flatMap((reason) => [
          `- ${reasonLabel(row.goal, reason)}: ${count(row[reason])}`,
          ...(reason === "complete" && row.icpsr > 0
            ? [`  - ${m.coverage_icpsr()}: ${count(row.icpsr)}`]
            : []),
        ])
        .join("\n"),
    ]),
    `## ${m.coverage_where()}`,
    TITLES.county(),
    table(
      [
        m.coverage_column_name(),
        m.coverage_column_short(),
        m.coverage_column_done(),
      ],
      data.gaps.map((row) => [
        link(row.label ?? row.key, null),
        count(row.total - row.complete),
        share(row),
      ]),
    ),
  ];
}

function homeMarkdown(data: Extract<Data, { kind: "home" }>, link: Link) {
  const listed = data.polities.filter((polity) => polity.first != null);
  let elections = 0;
  const years: Array<number> = [];
  for (const polity of listed) {
    elections += polity.elections + polity.within;
    years.push(...[polity.first, polity.last].filter((year) => year != null));
  }
  return [
    `# ${m.wordmark()}`,
    years.length === 0
      ? m.home_intro()
      : m.home_title({
          elections: count(elections),
          first: String(Math.min(...years)),
          last: String(Math.max(...years)),
        }),
    ...(data.recent.length === 0 ? [] : [`## ${m.home_recent()}`]),
    ...data.recent.flatMap((election) => {
      const race = election.lead;
      if (race == null) {
        return [
          `### ${link(election.name, election.path)}, ${date(election.date)}`,
        ];
      }
      const isSeated = race.contested > 1;
      const rows = race.results.map((row) => [
        row.label,
        isSeated
          ? `${count(row.seats ?? 0)} / ${count(race.contested)}`
          : percent(row.share),
        row.isLost === true
          ? race.quorum != null && row.share > 0.5
            ? m.outcome_quorum()
            : m.outcome_lost()
          : null,
      ]);
      const isDecided = rows.some((row) => row[2] != null);
      return [
        `### ${link(election.name, election.path)}, ${date(election.date)}`,
        ...(race.question == null ? [] : [race.question]),
        table(
          [
            m.col_option(),
            isSeated
              ? race.results[0]?.person == null
                ? m.col_seats()
                : m.col_electors()
              : m.col_share(),
            ...(isDecided ? [m.col_result()] : []),
          ],
          rows.map((row) => (isDecided ? row : row.slice(0, 2))),
        ),
      ];
    }),
    ...(data.upcoming.length === 0
      ? []
      : [
          `## ${m.home_upcoming()}`,
          table(
            [m.col_date(), m.col_election()],
            data.upcoming.map((election) => [
              date(election.date),
              link(election.name, election.path),
            ]),
          ),
        ]),
    ...(data.today.length === 0
      ? []
      : [
          `## ${m.home_today()}`,
          m.home_today_lede(),
          table(
            [m.col_date(), m.col_election(), m.col_polity()],
            data.today.map((election) => [
              date(election.date),
              link(election.name, election.path),
              election.polity,
            ]),
          ),
        ]),
    `## ${m.home_polities()}`,
    table(
      [m.col_polity(), m.polity_elections(), m.person_years(), m.home_latest()],
      listed.map((polity) => [
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

function personMarkdown(
  person: Extract<Data, { kind: "person" }>["person"],
  lanes: Extract<Data, { kind: "person" }>["parties"],
  link: Link,
) {
  const years = (from: string | null, to: string | null) =>
    to == null
      ? m.person_served_now({ from: from?.slice(0, 4) ?? "" })
      : m.person_served_span({
          from: from?.slice(0, 4) ?? "",
          to: to.slice(0, 4),
        });
  const roles = {
    member: m.person_parties_member,
    office: m.person_parties_office,
    group: m.person_parties_group,
  };
  const ways = {
    nominated: m.person_parties_nominated,
    line: m.person_parties_line,
    endorsed: m.person_parties_endorsed,
    supported: m.person_parties_supported,
    member: m.person_parties_member_ran,
    independent: m.person_parties_independent,
  };
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
    [
      person.party?.label ??
        (person.isIndependent ? m.person_parties_none() : null),
      person.caucus == null
        ? null
        : m.person_caucus({
            caucus: person.caucus.short ?? person.caucus.label,
          }),
      born,
      wikidata,
    ]
      .filter((part) => part != null)
      .join(", "),
    ...[
      { title: m.person_served(), spells: person.served },
      { title: m.person_elected(), spells: person.elected },
    ].flatMap(({ title, spells }) =>
      spells.length === 0
        ? []
        : [
            `## ${title}`,
            table(
              [m.col_name(), m.person_years()],
              spells.map((spell) => [
                spell.isBody ? link(spell.name, `id/${spell.id}`) : spell.name,
                years(spell.from, spell.to),
              ]),
            ),
          ],
    ),
    ...(lanes.length === 0
      ? []
      : [
          `## ${m.person_parties()}`,
          table(
            [m.col_party(), m.col_role(), m.person_years()],
            lanes.flatMap((lane) => {
              const party = lane.party?.label ?? m.person_parties_none();
              return [
                ...lane.spans.map((spell) => [
                  party,
                  roles[spell.kind](),
                  spell.from == null && spell.to == null
                    ? null
                    : years(spell.from, spell.to),
                ]),
                ...Map.groupBy(lane.ballots, (ballot) => ballot.how)
                  .entries()
                  .map(([how, ballots]) => {
                    const held = ballots.map((ballot) =>
                      Number(ballot.date.slice(0, 4)),
                    );
                    return [
                      party,
                      ways[how](),
                      span(Math.min(...held), Math.max(...held)),
                    ];
                  }),
              ];
            }),
          ),
        ]),
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
            link(
              entry.kind !== "seated"
                ? entry.name
                : entry.name === ""
                  ? m.person_record_by_election_unplaced()
                  : m.person_record_by_election({ place: entry.name }),
              entry.path,
            ),
            entry.party?.label ?? null,
            entry.votes == null || entry.total == null || entry.total === 0
              ? null
              : percent(entry.votes / entry.total),
            entry.note == null
              ? outcome(entry.outcome)
              : `${outcome(entry.outcome)} (${entry.note})`,
          ]),
        ),
  ].filter((block) => block !== "");
}

function partyMarkdown(
  { party, record, members }: Extract<Data, { kind: "party" }>,
  link: Link,
) {
  const related = [
    ...party.relations.map(
      (relation) =>
        `- ${RELATIONS[relation.kind]?.[relation.outgoing ? 0 : 1]({ name: link(relation.party.label, `id/${relation.party.id}`) }) ?? link(relation.party.label, `id/${relation.party.id}`)}${relation.date == null ? "" : ` (${relation.date.slice(0, 4)})`}`,
    ),
    ...party.parents.map(
      (parent) =>
        `- ${AFFILIATIONS[parent.kind]?.({ name: link(parent.party.label, `id/${parent.party.id}`) }) ?? link(parent.party.label, `id/${parent.party.id}`)}`,
    ),
  ];
  return [
    party.scope == null
      ? `# ${party.name}${party.short != null && !party.name.includes(party.short) ? ` (${party.short})` : ""}`
      : `# ${m.party_scope_title({ party: party.name, place: party.scope.polity.name })}`,
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
    ...(members.length === 0
      ? []
      : [
          `## ${m.party_members_title()}`,
          members
            .map(
              (member) =>
                `- ${link(member.name, `id/${member.person}`)} (${member.from ?? ""}–${member.to ?? ""})`,
            )
            .join("\n"),
        ]),
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
  record: Extract<Data, { kind: "polity" }>["record"],
  link: Link,
) {
  const latest = polity.latest;
  const labels = new Map(
    record.parties.map((party) => [party.id, party.short ?? party.label]),
  );
  return [
    `# ${polity.name}`,
    byline(polity.breadcrumbs, link, null),
    ...(latest == null || latest.races.length === 0
      ? []
      : [
          `## ${latest.isLocal ? m.polity_latest_local() : m.polity_latest()}`,
          [
            link(latest.name, latest.path),
            latest.date == null ? null : date(latest.date),
          ]
            .filter((part) => part != null)
            .join(", "),
          ...latest.races.flatMap((race) => {
            const isSeated = race.results.some((row) => row.seats != null);
            return [
              `### ${link(race.name, race.path)}`,
              table(
                [
                  m.col_option(),
                  m.col_votes(),
                  m.col_share(),
                  ...(isSeated ? [m.col_seats()] : []),
                ],
                race.results
                  .slice(0, 8)
                  .map((row) => [
                    row.label,
                    count(row.votes),
                    percent(row.share),
                    ...(isSeated
                      ? [row.seats == null ? null : count(row.seats)]
                      : []),
                  ]),
              ),
            ];
          }),
        ]),
    ...(record.series.length === 0
      ? []
      : [`## ${m.scrubber_title({ place: polity.name })}`]),
    ...record.series.flatMap((series) => [
      `### ${series.name}`,
      table(
        [m.col_year(), m.col_election(), m.col_result()],
        series.points.toReversed().map((point) => {
          const parties = point.parties
            .toSorted((a, b) => b.seats - a.seats)
            .map((row) => ({
              label: labels.get(row.party ?? "") ?? m.person_parties_none(),
              seats: row.seats,
            }));
          return [
            point.year,
            link(point.name, point.path),
            series.isOffice
              ? [
                  parties[0]?.label,
                  point.share == null ? null : percent(point.share),
                ]
                  .filter((part) => part != null)
                  .join(", ")
              : parties
                  .slice(0, 4)
                  .map((row) => `${row.label} ${count(row.seats)}`)
                  .join(", "),
          ];
        }),
      ),
    ]),
    `## ${m.polity_elections()}`,
    polity.elections.length === 0
      ? m.polity_no_elections()
      : table(
          [m.col_date(), m.col_election()],
          polity.elections.map((election) => [
            election.date == null ? election.year : date(election.date),
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
                date(group.date),
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
    ...election.races.flatMap((entry) => {
      const race =
        entry.round != null &&
        election.races.some(
          (other) =>
            other !== entry &&
            other.name === entry.name &&
            other.round !== entry.round,
        )
          ? {
              ...entry,
              name: m.race_round({ name: entry.name, round: entry.round }),
            }
          : entry;
      const hasSeats = race.results.some((row) => row.seats != null);
      const seats = race.electors == null ? m.col_seats() : m.col_electors();
      const districts = race.isPrimaries ? race.places : null;
      if (districts != null) {
        return [
          `## ${link(race.name, race.path)}`,
          m.race_lede_primaries({ count: count(race.count) }),
          table(
            [m.col_name(), m.col_leader(), m.col_share()],
            districts.rows.map((row) => [
              link(row.name, row.path),
              ...leading(row, districts.legend),
            ]),
          ),
        ];
      }
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
  cycle: Extract<Data, { kind: "contest" }>["cycle"],
  link: Link,
) {
  const groups: Record<string, () => string> = {
    owner: () => "",
    governor: m.cycle_governor,
    legislature: m.cycle_legislature,
    regional_office: m.cycle_regional_office,
    national_measure: m.cycle_national_measure,
    regional_measure: m.cycle_regional_measure,
    local: m.cycle_local,
    local_measure: m.cycle_local_measure,
  };
  const steps = [
    ...cycle.primaries.map((step) => ({
      ...step,
      name:
        step.party == null
          ? m.cycle_general()
          : (step.until == null ? m.cycle_primary : m.cycle_primaries)({
              party: step.party.label,
            }),
    })),
    ...(cycle.general == null || cycle.primaries.length === 0
      ? []
      : [{ ...cycle.general, name: m.cycle_general() }]),
  ];
  const day = cycle.day.groups.filter((group) => group.path != null);
  const { target } = contest;
  const then =
    target == null
      ? null
      : String(target.year ?? target.date?.slice(0, 4) ?? target.name);
  const compared = comparedMarkdown(contest, link, then);
  return [
    `# ${contest.name}`,
    byline(contest.breadcrumbs, link, contest.election.date),
    contest.scope.trail.length > 1
      ? contest.scope.trail
          .map((step) => link(step.name, step.path))
          .join(" / ")
      : "",
    ...contest.notes.map((note) => `> ${note.body}`),
    ...compared,
    resultsMarkdown(contest, then),
    ...placesMarkdown(contest.places, link, then),
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
                `- ${entry.current ? entry.name : link(entry.name, entry.path)}${entry.winner == null ? "" : `: ${entry.winner.label}${entry.winner.total > 0 ? `, ${percent(entry.winner.share)}` : ""}`}`,
            )
            .join("\n"),
        ]),
    ...(steps.length === 0
      ? []
      : [
          `## ${m.cycle_road()}`,
          table(
            [m.col_date(), m.col_contest(), m.cycle_winner()],
            steps.map((step) => [
              step.date == null ? null : date(step.date),
              step.isCurrent ? step.name : link(step.name, step.path),
              step.winners.map((winner) => winner.label).join(", "),
            ]),
          ),
        ]),
    ...(day.length === 0 || cycle.day.date == null
      ? []
      : [
          `## ${m.cycle_day({ date: date(cycle.day.date) })}`,
          day
            .map(
              (group) =>
                `- ${link(group.label ?? groups[group.kind]?.() ?? "", group.path)}${group.count > 1 ? ` (${count(group.count)})` : ""}`,
            )
            .join("\n"),
        ]),
    ...sourcesMarkdown(contest),
  ].filter((block) => block !== "");
}

function resultsMarkdown(
  contest: Extract<Data, { kind: "contest" }>["contest"],
  then: string | null,
) {
  const hasSeats = contest.rows.some((row) => row.current?.seats != null);
  const results = contest.rows
    .filter(
      (row) => row.current != null || (then != null && row.earlier != null),
    )
    .map(({ label, current, earlier, delta }) => [
      label,
      current && count(current.votes),
      current && percent(current.share),
      ...(hasSeats
        ? [current?.seats == null ? null : count(current.seats)]
        : []),
      ...(then == null
        ? []
        : [
            earlier && percent(earlier.share),
            delta == null ? null : points(delta),
          ]),
      current?.outcome === "lost"
        ? m.outcome_lost()
        : outcome(current?.outcome ?? null),
    ]);
  return contest.current == null || results.length === 0
    ? m.no_results()
    : `${table(
        [
          m.col_option(),
          m.col_votes(),
          m.col_share(),
          ...(hasSeats ? [m.col_seats()] : []),
          ...(then == null
            ? []
            : [m.col_share_in({ year: then }), m.col_change()]),
          m.col_result(),
        ],
        results,
      )}\n\n${m.stat_valid()}: ${count(contest.current.valid)}${contest.rows
        .flatMap((row) =>
          row.current?.outcome == null || row.current.note == null
            ? []
            : [
                `\n\n${m.outcome_note({ names: row.label, note: row.current.note })}`,
              ],
        )
        .join("")}`;
}

function comparedMarkdown(
  contest: Extract<Data, { kind: "contest" }>["contest"],
  link: Link,
  then: string | null,
) {
  const { target } = contest;
  if (target == null || then == null) {
    return [];
  }
  const years = [Number(then), Number(contest.election.date?.slice(0, 4))];
  const from = String(Math.min(...years));
  const to = String(Math.max(...years));
  return [
    [
      `${m.compare_label()}: ${link(target.name, target.path)}. ${contest.comparison.label === "exact" ? m.quality_exact_detail() : m.quality_estimated_detail()}`,
      ...contest.comparison.notes.map((note) => {
        if (note.code === "partial") {
          const value = percent(Number(note.value));
          return note.side === "target"
            ? m.note_partial_target({ percent: value })
            : m.note_partial_current({ percent: value });
        }
        if (note.code === "redrawn") {
          return m.note_redrawn({
            from,
            to,
            percent: percent(Number(note.value) / 100),
          });
        }
        if (note.code === "alternative") {
          const publisher = note.value ?? "";
          return note.side === "target"
            ? m.note_alternative_target({ publisher })
            : m.note_alternative_current({ publisher });
        }
        return {
          no_target: m.note_no_target,
          not_covered: m.note_not_covered,
          different_race: m.note_different_race,
          annulled: m.note_annulled,
        }[note.code]();
      }),
    ].join("\n\n"),
  ];
}

function placesMarkdown(
  places: Extract<Data, { kind: "contest" }>["contest"]["places"],
  link: Link,
  then: string | null,
) {
  if (places == null || places.rows.length === 0) {
    return [];
  }
  const isCompared =
    then != null && places.rows.some((row) => row.earlier != null);
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
        ...(isCompared ? [m.col_share_in({ year: then }), m.col_change()] : []),
        m.stat_valid(),
      ],
      places.rows.map((row) => {
        const before =
          row.leader == null ? null : (row.earlier?.[row.leader] ?? null);
        return [
          link(row.name, row.path),
          ...leading(row, places.legend),
          row.margin == null ? null : points(row.margin),
          ...(isCompared
            ? [
                before == null ? null : percent(before),
                before == null || row.leader == null
                  ? null
                  : points((row.shares[row.leader] ?? 0) - before),
              ]
            : []),
          row.valid === 0 && row.declared != null ? null : count(row.valid),
        ];
      }),
    ),
    ...(isCompared && places.estimated === true
      ? [m.atlas_estimated_note({ year: then })]
      : []),
    ...(places.unreconciled ?? []).map((flag) =>
      flag.official == null
        ? `> ${m.places_unreconciled_missing({ publisher: flag.publisher })}`
        : `> ${[
            m.places_unreconciled_lead({ publisher: flag.publisher }),
            ...(flag.reason === "overcounted" || flag.drifted.length === 0
              ? [
                  m.places_unreconciled_overcounted({
                    counted: count(flag.counted),
                    official: count(flag.official),
                  }),
                ]
              : flag.drifted.map((drift) =>
                  m.places_unreconciled_drifted({
                    party: drift.party,
                    counted: count(drift.counted),
                    official: count(drift.official),
                  }),
                )),
            m.places_unreconciled_official(),
          ].join(" ")}`,
    ),
  ];
}

function sourcesMarkdown(
  contest: Extract<Data, { kind: "contest" }>["contest"],
) {
  const sets = contest.sets.filter(
    (set, index) =>
      contest.sets.findIndex(
        (other) =>
          other.stage === set.stage &&
          other.status === set.status &&
          other.isDefault === set.isDefault &&
          other.source.publisher === set.source.publisher &&
          other.source.title === set.source.title,
      ) === index,
  );
  const statuses: Record<string, () => string> = {
    superseded: m.status_superseded,
    annulled: m.status_annulled,
    withdrawn: m.status_withdrawn,
  };
  const sources = [
    ...sets.map(({ source, isDefault, status }) => {
      const marks =
        sets.length < 2
          ? []
          : [
              ...(isDefault ? [m.source_shown()] : []),
              ...(status === "active" ? [] : [statuses[status]?.() ?? status]),
            ];
      return `- ${marks.length === 0 ? "" : `${marks.join(", ")}: `}${source.url == null ? source.title : `[${source.title}](${source.url})`}, ${source.publisher}${source.license == null ? "" : `. ${m.source_license({ license: source.license })}`}`;
    }),
    ...contest.citations.map(
      (citation) =>
        `- ${citation.url == null ? citation.title : `[${citation.title}](${citation.url})`}, ${citation.publisher}`,
    ),
  ];
  return sources.length === 0
    ? []
    : [`## ${m.sources_title()}`, sources.join("\n")];
}

function chamberMarkdown(
  { body, terms, parties, members }: Extract<Data, { kind: "chamber" }>,
  link: Link,
) {
  const KINDS: Record<string, () => string> = {
    lower_house: m.chamber_kind_lower_house,
    upper_house: m.chamber_kind_upper_house,
    unicameral: m.chamber_kind_unicameral,
  };
  const everyone = [
    ...parties,
    ...stand(
      (members?.members ?? []).map((member) => effective(member)),
      members?.parties ?? [],
    ),
  ];
  const names = new Map(everyone.map((entry) => [entry.id, entry.label]));
  const caucuses = new Map(everyone.map((entry) => [entry.id, entry.caucus]));
  const label = (id: string | null) =>
    id == null ? m.record_none() : (names.get(id) ?? "");
  const last = terms.at(-1);
  const first = last?.start.slice(0, 4) ?? "";
  const final = last?.end?.slice(0, 4) ?? m.governments_present();
  const sitting = (members?.members ?? []).filter((member) => member.isVoting);
  return [
    `# ${body.name}`,
    byline(body.crumbs, link, null),
    m.chamber_of({
      kind: (KINDS[body.kind] ?? m.chamber_kind_other)(),
      polity: link(body.polity.name, body.polity.path),
    }),
    `## ${m.chamber_makeup_title()}`,
    terms.length === 0
      ? m.chamber_no_election()
      : table(
          [
            m.col_date(),
            m.chamber_elected(),
            m.chamber_seats(),
            m.chamber_largest(),
          ],
          terms.toReversed().map((term) => {
            const seats = new Map<string | null, number>();
            for (const entry of term.parties) {
              const key = caucuses.get(entry.party ?? "") ?? entry.party;
              seats.set(key, (seats.get(key) ?? 0) + entry.seats);
            }
            const largest = [...seats]
              .map(([party, held]) => ({ party, seats: held }))
              .toSorted((a, b) => b.seats - a.seats)[0];
            return [
              date(term.start),
              term.election == null
                ? null
                : link(term.election.name, term.election.path),
              count(term.size ?? term.total),
              largest == null
                ? null
                : `${label(largest.party)} (${count(largest.seats)})`,
            ];
          }),
        ),
    ...(sitting.length === 0
      ? []
      : [
          `## ${(members?.isSeated === true ? m.chamber_members_title : m.chamber_members_returned)({ span: first === final ? first : m.polity_span({ first, last: final }) })}`,
          table(
            [m.col_name(), m.col_party(), m.chamber_joined(), m.chamber_left()],
            sitting.map((member) => [
              `${link(member.name, `id/${member.person}`)}${member.place == null ? "" : ` (${link(member.place, member.path)})`}`,
              label(effective(member)),
              date(member.from),
              date(member.to),
            ]),
          ),
        ]),
  ].filter((block) => block !== "");
}

const pair = (one: string, two: string) => `compare?a=${one}&b=${two}`;

function comparisonMarkdown(
  data: Extract<Data, { kind: "comparison" }>,
  link: Link,
) {
  const [a, b] = data.sides;
  const heading = [`# ${m.versus_title()}`];
  if (a == null || b == null) {
    return [
      ...heading,
      m.versus_lede(),
      ...(data.examples.length === 0
        ? []
        : [
            `## ${m.versus_examples()}`,
            data.examples
              .map(
                (example) =>
                  `- ${link(example.sides.map((side) => side.name).join(" / "), pair(example.a, example.b))}${
                    example.year == null
                      ? ""
                      : `: ${(example.kind === "person"
                          ? m.versus_example_person
                          : example.kind === "party"
                            ? m.versus_example_party
                            : m.versus_example_polity)({
                          year: String(example.year),
                        })}`
                  }`,
              )
              .join("\n"),
          ]),
      ...(a == null || data.rivals.length === 0
        ? []
        : [
            `## ${link(a.name, `id/${a.id}`)}: ${m.versus_rivals()}`,
            data.rivals
              .map(
                (rival) =>
                  `- ${link(`${a.name} / ${rival.name}`, pair(a.id, rival.id))}`,
              )
              .join("\n"),
          ]),
    ];
  }
  heading.push(`${link(a.name, `id/${a.id}`)} / ${link(b.name, `id/${b.id}`)}`);
  if (data.people != null) {
    const [one, two] = data.people;
    const theirs = new Map(
      (two?.record ?? []).flatMap((entry) =>
        entry.path == null ? [] : [[entry.path, entry]],
      ),
    );
    const cell = (entry: NonNullable<typeof one>["record"][number]) =>
      [
        entry.votes == null || entry.total == null || entry.total === 0
          ? null
          : percent(entry.votes / entry.total),
        outcome(entry.outcome),
      ]
        .filter((part) => part != null)
        .join(", ");
    const shared = (one?.record ?? []).flatMap((entry) => {
      const other = entry.path == null ? null : theirs.get(entry.path);
      return other == null
        ? []
        : [
            [
              entry.year,
              link(entry.name, entry.path),
              cell(entry),
              cell(other),
            ],
          ];
    });
    const facts = [one, two].map((person) => {
      const years = (person?.record ?? []).map((entry) => entry.year);
      return {
        party: person?.party?.label ?? null,
        ran: count(person?.record.length ?? 0),
        won: count(
          (person?.record ?? []).filter((entry) =>
            ["elected", "won", "advanced"].includes(entry.outcome ?? ""),
          ).length,
        ),
        years:
          years.length === 0
            ? null
            : span(Math.min(...years), Math.max(...years)),
      };
    });
    return [
      ...heading,
      table(
        ["", a.name, b.name],
        [
          [m.col_party(), facts[0]?.party ?? null, facts[1]?.party ?? null],
          [m.person_ran(), facts[0]?.ran ?? null, facts[1]?.ran ?? null],
          [m.person_won(), facts[0]?.won ?? null, facts[1]?.won ?? null],
          [m.person_years(), facts[0]?.years ?? null, facts[1]?.years ?? null],
        ],
      ),
      `## ${m.versus_head_title()}`,
      shared.length === 0
        ? m.versus_head_none()
        : table([m.col_year(), m.col_contest(), a.name, b.name], shared),
    ];
  }
  if (data.places != null) {
    const top = (
      options: Array<{ label: string; share: number }> | undefined,
    ) => {
      const best = options?.toSorted((x, y) => y.share - x.share)[0];
      return best == null ? null : `${best.label}, ${percent(best.share)}`;
    };
    return [
      ...heading,
      ...(data.places.series.length === 0
        ? [m.versus_places_none()]
        : data.places.series.flatMap((series) => [
            `## ${series.name ?? (series.kind === "shared" ? m.versus_overall() : m.versus_governor())}`,
            ...(series.kind === "combined" ? [m.versus_combined_key()] : []),
            table(
              [
                m.col_date(),
                m.col_contest(),
                m.versus_overall(),
                a.name,
                b.name,
              ],
              series.points
                .toReversed()
                .map((point) => [
                  date(point.date),
                  link(point.name, point.path),
                  top(point.sides[0]),
                  top(point.sides[1]),
                  top(point.sides[2]),
                ]),
            ),
          ])),
    ];
  }
  if (data.parties != null) {
    return [
      ...heading,
      ...(data.parties.series.length === 0
        ? [m.versus_parties_none()]
        : data.parties.series.flatMap((series) => [
            `## ${series.name}`,
            table(
              [m.col_date(), m.col_election(), a.name, b.name],
              series.points
                .toReversed()
                .map((point) => [
                  date(point.date),
                  link(point.name, point.path),
                  ...point.sides.map((side) =>
                    side == null ? null : percent(side.share),
                  ),
                ]),
            ),
          ])),
    ];
  }
  return [...heading, m.versus_mismatch()];
}
