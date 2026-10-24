import type { RequestEvent } from "@sveltejs/kit";
import { call } from "@orpc/server";
import { error, isHttpError, isRedirect, redirect } from "@sveltejs/kit";
import { effective, stand } from "#lib/caucus.js";
import { PARTS, label as reasonLabel, REASONS } from "#lib/coverage.js";
import { count, date, definite, percent, plural, points } from "#lib/format.js";
import { isDerived, VACANCIES } from "#lib/outcomes.js";
import { m } from "#lib/paraglide/messages.js";
import { getLocale } from "#lib/paraglide/runtime.js";
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
  elsewhere: m.person_elected_elsewhere,
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
    return [
      row.isRecorded === false
        ? m.places_unrecorded()
        : row.valid === 0
          ? m.places_no_votes()
          : null,
      null,
    ];
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
  if (found.kind === "election") {
    return {
      ...found,
      byes: await call(router.seats.byElections, {
        id: found.id,
        within: found.scope?.id,
      }),
    };
  }
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
    if (data.kind === "election" && data.scope != null) {
      headers.link = `<${url.origin}${pathname}?in=${data.scope.path}>; rel="canonical"`;
    }
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
      const line = (hit: (typeof data.hits)[number]) =>
        `- ${(hit.office == null ? [link(hit.name, hit.path), hit.context, date(hit.date)] : [link(hit.name, hit.path), hit.office]).filter((part) => part != null && part !== "").join(", ")}`;
      const best = data.hits.filter((hit) => hit.isBest);
      return [
        `# ${m.search_title()}`,
        ...(data.q === ""
          ? [m.search_hint()]
          : data.hits.length === 0
            ? [m.search_empty({ query: data.q })]
            : [
                ...(best.length === 0
                  ? []
                  : [
                      `## ${m.search_best()}`,
                      best.map((hit) => line(hit)).join("\n"),
                    ]),
                ...groups.flatMap(([type, heading]) => {
                  const hits = data.hits.filter(
                    (hit) => hit.type === type && !hit.isBest,
                  );
                  return hits.length === 0
                    ? []
                    : [
                        `## ${heading}`,
                        hits.map((hit) => line(hit)).join("\n"),
                      ];
                }),
              ]),
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
      return electionMarkdown(data.election, data.sections, data.byes, link);
    }
    default: {
      return contestMarkdown(
        data.contest,
        data.cycle,
        data.handover,
        data.additional,
        data.successors,
        data.statutes,
        data.lists,
        link,
      );
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
      ? (person.isGone ? m.person_served_from : m.person_served_now)({
          from: from?.slice(0, 4) ?? "",
        })
      : m.person_served_span({
          from: from?.slice(0, 4) ?? "",
          to: to.slice(0, 4),
        });
  const roles = {
    member: m.person_parties_member,
    office: m.person_parties_office,
    group: m.person_parties_group,
  };
  const endings: Record<string, (() => string) | undefined> = {
    died: m.person_ended_died,
    resigned: m.person_ended_resigned,
    elevated: m.person_ended_elevated,
    appointed: m.person_ended_appointed,
    annulled: m.person_ended_annulled,
    disqualified: m.person_ended_disqualified,
    expelled: m.person_ended_expelled,
    removed: m.person_ended_removed,
    withdrew: m.person_ended_withdrew,
    lost_seat: m.person_ended_lost_seat,
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
  const died =
    person.died == null
      ? null
      : person.diedPrecision === "day"
        ? m.card_died_date({ date: date(person.died) })
        : m.card_died({ year: person.died.slice(0, 4) });
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
      died,
      wikidata,
    ]
      .filter((part) => part != null)
      .join(". "),
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
                `${spell.isBody ? link(spell.name, `id/${spell.id}`) : spell.name}${
                  "seats" in spell && spell.seats.length > 0
                    ? ` (${spell.seats
                        .map((seat) =>
                          [
                            seat.name,
                            years(seat.from, seat.to),
                            endings[seat.ended ?? ""]?.() ?? null,
                          ]
                            .filter((part) => part != null)
                            .join(", "),
                        )
                        .join("; ")})`
                    : ""
                }`,
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
            m.col_majority(),
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
            entry.party?.label ??
              (entry.isIndependent === true ? m.person_parties_none() : null),
            entry.votes == null || entry.total == null || entry.total === 0
              ? null
              : percent(entry.votes / entry.total),
            entry.majority == null ? null : count(entry.majority),
            entry.isUnopposed === true && entry.outcome === "elected"
              ? m.person_elected_unopposed()
              : entry.note == null || isDerived(entry.note)
                ? outcome(entry.outcome)
                : `${outcome(entry.outcome)} (${entry.note})`,
          ]),
        ),
  ].filter((block) => block !== "");
}

function partyMarkdown(
  { party, record, members, notes }: Extract<Data, { kind: "party" }>,
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
    ...notes.map((note) => `> ${note.body}`),
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
          [
            m.col_year(),
            m.col_election(),
            m.party_contests(),
            m.col_seats(),
            m.person_won(),
          ],
          record.elections.map((election) => [
            election.year,
            link(election.name, election.path),
            count(election.races),
            election.seats > 0 ? count(election.seats) : null,
            election.won > 0 ? count(election.won) : null,
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
            link(
              latest.name,
              latest.path == null || latest.scope == null
                ? latest.path
                : `${latest.path}?in=${latest.scope}`,
            ),
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
              label:
                labels.get(row.party ?? "") ??
                (row.isIndependent === true
                  ? m.person_parties_none()
                  : series.isOffice
                    ? null
                    : m.record_none()),
              seats: row.seats,
            }));
          return [
            point.year,
            link(
              point.name,
              point.path == null ||
                record.scope == null ||
                point.path.startsWith(`${record.scope}/`)
                ? point.path
                : `${point.path}?in=${record.scope}`,
            ),
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
                race.source == null
                  ? link(race.name, race.path)
                  : `${link(race.name, race.path)} (${m.polity_results_source({ publisher: race.source })})`,
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

function councilsMarkdown(
  councils: Extract<Data, { kind: "election" }>["election"]["councils"],
  link: Link,
) {
  if (councils.length === 0) {
    return [];
  }
  const none = councils.filter(
    (council) => council.control != null && council.control.key == null,
  ).length;
  return [
    `## ${m.councils_title()}`,
    m.councils_lede({ count: count(councils.length) }),
    `### ${m.councils_tally()}`,
    table(
      [m.col_party(), m.col_councillors(), m.col_councils_controlled()],
      [
        ...Map.groupBy(
          councils.flatMap((council) =>
            council.parties.map((party) => ({
              party,
              isControl: council.control?.key === party.key,
            })),
          ),
          (entry) => entry.party.key,
        )
          .values()
          .map((entries) => {
            let seats = 0;
            let controlled = 0;
            for (const entry of entries) {
              seats += entry.party.seats;
              controlled += Number(entry.isControl);
            }
            return { label: entries[0]?.party.label ?? "", seats, controlled };
          })
          .toArray()
          .toSorted((a, b) => b.seats - a.seats)
          .map((entry) => [
            entry.label,
            count(entry.seats),
            entry.controlled === 0 ? null : count(entry.controlled),
          ]),
        ...(none > 0 ? [[m.control_none_short(), null, count(none)]] : []),
      ],
    ),
    table(
      [
        m.col_council(),
        m.col_seats_up(),
        m.col_seats_by_party(),
        m.col_control(),
      ],
      councils.map((council) => [
        link(council.name, council.path),
        council.size != null && council.size > council.up
          ? m.polity_seats({
              seats: count(council.up),
              size: count(council.size),
            })
          : count(council.up),
        council.parties
          .map((party) => `${party.short ?? party.label} ${count(party.seats)}`)
          .join(", "),
        council.control == null
          ? null
          : council.control.label == null
            ? m.control_none_short()
            : (council.parties.find(
                (party) => party.key === council.control?.key,
              )?.short ?? council.control.label),
      ]),
    ),
  ];
}

function electionMarkdown(
  election: Extract<Data, { kind: "election" }>["election"],
  { governments, regions }: Extract<Data, { kind: "election" }>["sections"],
  byes: Extract<Data, { kind: "election" }>["byes"],
  link: Link,
) {
  const signed = new Intl.NumberFormat("en", { signDisplay: "exceptZero" });
  const parties = new Map(byes?.parties.map((party) => [party.key, party]));
  const label = (key: string | null) =>
    key == null || key.startsWith("person:")
      ? m.seats_independents()
      : (parties.get(key)?.short ?? parties.get(key)?.label ?? "");
  const reasons: Record<string, (input: { name: string }) => string> = {
    died: m.byes_reason_died,
    resigned: m.byes_reason_resigned,
    elevated: m.byes_reason_elevated,
    appointed: m.byes_reason_appointed,
    expelled: m.byes_reason_expelled,
    disqualified: m.byes_reason_disqualified,
    annulled: m.byes_reason_annulled,
    removed: m.byes_reason_removed,
    withdrew: m.byes_reason_withdrew,
  };
  const byeRows = (byes?.rows ?? []).map((row) => ({
    ...row,
    isGain:
      row.to != null && row.from != null && label(row.to) !== label(row.from),
  }));
  const gained = byeRows.filter((row) => row.isGain).length;
  const listed =
    byeRows.length > 20
      ? byeRows.filter((row) => row.isGain || row.isHere || row.to == null)
      : byeRows;
  const tiered =
    Map.groupBy(
      election.races.filter(
        (race) =>
          race.body != null &&
          (race.round ?? 1) === 1 &&
          (race.kind === "body_seats" || race.kind === "list_allocation"),
      ),
      (race) => race.body,
    )
      .values()
      .find((group) => {
        let seats = 0;
        for (const race of group) {
          for (const row of race.results) {
            seats += row.seats ?? 0;
          }
        }
        return (
          new Set(group.map((race) => race.kind)).size === 2 &&
          (group[0]?.size == null || seats <= group[0].size * 1.02)
        );
      }) ?? [];
  const tiers = new Map<
    string,
    { label: string; constituencies: number; lists: number }
  >();
  for (const race of tiered) {
    for (const row of race.results) {
      const entry = tiers.get(row.key) ?? {
        label: row.label,
        constituencies: 0,
        lists: 0,
      };
      if (race.kind === "list_allocation") {
        entry.lists += row.seats ?? 0;
      } else {
        entry.constituencies += row.seats ?? 0;
      }
      tiers.set(row.key, entry);
    }
  }
  const lists = tiered.filter((race) => race.kind === "list_allocation");
  const chamber = election.races.find(
    (race) =>
      race.kind === "body_seats" &&
      (race.round ?? 1) === 1 &&
      race.results.some((row) => (row.seats ?? 0) > 0),
  );
  const rest = election.races.filter((race) => !lists.includes(race));
  const at = rest.findLastIndex((race) => tiered.includes(race)) + 1;
  return [
    `# ${election.name}`,
    byline(election.breadcrumbs, link, election.date),
    ...election.notes.map((note) => `> ${note.body}`),
    ...(election.turnout == null
      ? []
      : [
          `${m.election_turnout({
            voters: count(election.turnout.voters),
            registered: count(election.turnout.registered),
            share: percent(
              election.turnout.voters / election.turnout.registered,
            ),
          })}${
            election.turnout.earlier == null
              ? ""
              : ` ${m.turnout_since({
                  points: points(
                    election.turnout.voters / election.turnout.registered -
                      election.turnout.earlier.voters /
                        election.turnout.earlier.registered,
                  ),
                  year: String(election.turnout.earlier.year),
                })}.`
          }${election.turnout.basis === "rejected" ? ` ${m.turnout_basis_rejected()}` : election.turnout.basis === "valid" ? ` ${m.turnout_basis_valid()}` : ""}`,
        ]),
    ...(election.control == null
      ? []
      : [
          `${
            election.control.label == null
              ? m.control_none({ total: count(election.control.total) })
              : m.control_party({
                  party: election.control.label,
                  seats: count(election.control.seats),
                  total: count(election.control.total),
                })
          }. ${m.control_derived()}`,
        ]),
    ...councilsMarkdown(election.councils, link),
    ...(tiered[0] == null
      ? []
      : [
          `## ${m.race_tiers_title({ name: tiered[0].name })}`,
          table(
            [
              m.col_party(),
              m.col_constituency_seats(),
              m.col_list_seats(),
              m.col_total_seats(),
            ],
            tiers
              .values()
              .filter((entry) => entry.constituencies + entry.lists > 0)
              .toArray()
              .toSorted(
                (a, b) =>
                  b.constituencies + b.lists - (a.constituencies + a.lists),
              )
              .map((entry) => [
                entry.label,
                count(entry.constituencies),
                count(entry.lists),
                count(entry.constituencies + entry.lists),
              ]),
          ),
        ]),
    ...[...rest.slice(0, at), ...lists, ...rest.slice(at)].flatMap((entry) => {
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
          : tiered.includes(entry) && entry.kind === "list_allocation"
            ? { ...entry, name: m.race_list({ name: entry.name }) }
            : tiered.includes(entry) && entry.places?.levels[0] != null
              ? {
                  ...entry,
                  name: m.race_split({
                    name: entry.name,
                    noun: entry.places.levels[0].noun,
                  }),
                }
              : entry;
      const seats = race.electors == null ? m.col_seats() : m.col_electors();
      let filled = 0;
      for (const row of race.results) {
        filled += row.seats ?? 0;
      }
      const hasSeats = filled > 1;
      const hasParty = race.results.some(
        (row) =>
          row.person != null &&
          (row.short != null || row.isIndependent === true),
      );
      const hasResult =
        !hasSeats &&
        race.results.some((row) => row.isWon === true && row.person != null);
      const before = new Map(
        (race.before ?? []).map((entry) => [entry.key, entry.seats]),
      );
      const isChanged =
        hasSeats &&
        race.before != null &&
        race.results.every((row) => !row.key.startsWith("alliance:")) &&
        race.results.some((row) => (row.seats ?? 0) > 0 && before.has(row.key));
      const units = Map.groupBy(
        race.results.filter((row) => (row.seats ?? 0) > 0),
        (row) =>
          race.partners.find((pair) => pair.includes(row.key))?.[0] ?? row.key,
      )
        .values()
        .map((group) => {
          let won = 0;
          for (const row of group) {
            won += row.seats ?? 0;
          }
          return { ...group[0], won };
        })
        .toArray()
        .toSorted((a, b) => b.won - a.won);
      const leader = units[0];
      const lede =
        !hasSeats ||
        leader?.label == null ||
        race.isOffice ||
        race.isPrimaries ||
        race.electors != null ||
        race.kind !== "body_seats" ||
        race.body == null ||
        tiered.includes(entry) ||
        leader.key?.startsWith("person:") === true
          ? null
          : ((race.unopposed?.seats ?? 0) * 10 >= filled ||
              (race.multiple ?? 0) > 0
              ? m.race_lede_seats_only
              : race !== entry && race.round === 1
                ? m.race_lede_seats_first
                : m.race_lede_seats)({
              party: definite(
                leader.label.length > 28
                  ? (leader.short ?? leader.label)
                  : leader.label,
                true,
              ),
              seats: count(leader.won),
              total: count(
                race.size != null && race.size <= filled + 2
                  ? race.size
                  : filled,
              ),
              share: percent(leader.share ?? 0),
            });
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
        ...(lede == null ? [] : [lede]),
        ...(race.results.length === 0
          ? []
          : [
              table(
                [
                  race.results.some((row) => row.key.startsWith("answer:"))
                    ? m.col_answer()
                    : m.col_option(),
                  ...(hasParty ? [m.col_party()] : []),
                  m.col_votes(),
                  m.col_share(),
                  ...(hasSeats ? [seats] : []),
                  ...(isChanged ? [m.col_change()] : []),
                  ...(hasResult ? [m.col_result()] : []),
                ],
                race.results.map((row) => [
                  row.label,
                  ...(hasParty
                    ? [
                        row.person == null
                          ? null
                          : (row.short ??
                            (row.isIndependent === true
                              ? m.seats_independents()
                              : null)),
                      ]
                    : []),
                  count(row.votes),
                  percent(row.share),
                  ...(hasSeats
                    ? [row.seats == null ? null : count(row.seats)]
                    : []),
                  ...(isChanged
                    ? [
                        before.has(row.key)
                          ? signed
                              .format(
                                (row.seats ?? 0) - (before.get(row.key) ?? 0),
                              )
                              .replace("-", "−")
                          : null,
                      ]
                    : []),
                  ...(hasResult
                    ? [
                        row.isWon === true && row.person != null
                          ? m.outcome_elected()
                          : null,
                      ]
                    : []),
                ]),
              ),
            ]),
        ...((race.places?.sources.length ?? 0) === 0
          ? []
          : [
              m.places_sources({
                publisher: race.places?.sources.join(", ") ?? "",
              }),
            ]),
        ...(race.unopposed == null
          ? []
          : [
              m.race_unopposed({
                seats: race.unopposed.seats,
                parties: new Intl.ListFormat("en", {
                  type: "conjunction",
                }).format(
                  race.unopposed.parties.map((party) =>
                    m.race_unopposed_party({
                      party: party.label === "" ? m.record_none() : party.label,
                      seats: count(party.seats),
                    }),
                  ),
                ),
              }),
            ]),
        ...(race.isStaggered === true && race.size != null && filled < race.size
          ? [
              m.race_staggered({
                count: count(filled),
                total: count(race.size),
              }),
            ]
          : []),
        ...((race.multiple ?? 0) > 0
          ? [
              m.race_multiple({
                count: race.multiple ?? 0,
                noun: race.places?.levels[0]?.noun ?? "constituency",
                nouns: plural(race.places?.levels[0]?.noun ?? "constituency"),
              }),
            ]
          : []),
      ];
    }),
    ...(governments.every((government) => government.isBefore)
      ? []
      : [
          `## ${m.governments_title()}`,
          m.governments_lede(),
          table(
            [
              m.col_name(),
              m.col_status(),
              m.col_led_by(),
              m.col_from(),
              m.col_to(),
              m.col_parties(),
            ],
            governments.map((government) => {
              const parties = government.parties.map((party) => ({
                ...party,
                seats:
                  party.seats ??
                  (government.isBefore
                    ? null
                    : (chamber?.results.find(
                        (row) => row.key === `party:${party.id}`,
                      )?.seats ?? null)),
              }));
              let held = 0;
              for (const party of parties) {
                held += party.seats ?? 0;
              }
              return [
                government.name,
                [
                  government.isBefore
                    ? m.governments_before()
                    : m.governments_formed(),
                  ...(government.isCaretaker
                    ? [m.governments_caretaker()]
                    : []),
                ].join(", "),
                government.head == null
                  ? null
                  : link(government.head.name, `id/${government.head.id}`),
                date(government.from),
                government.to == null
                  ? m.governments_present()
                  : date(government.to),
                [
                  parties
                    .map((party) =>
                      [
                        party.short ?? party.label,
                        party.role === "support"
                          ? ` (${m.governments_support()})`
                          : "",
                        party.seats == null ? "" : ` ${count(party.seats)}`,
                      ].join(""),
                    )
                    .join(", "),
                  ...(held > 0 && government.size != null
                    ? [
                        `(${m.polity_seats({ seats: count(held), size: count(government.size) })})`,
                      ]
                    : []),
                ].join(" "),
              ];
            }),
          ),
        ]),
    ...regions.flatMap((chamber) => {
      const info = new Map(chamber.parties.map((party) => [party.id, party]));
      const totals = Map.groupBy(
        chamber.regions.flatMap((region) => region.parties),
        (entry) => entry.party ?? (entry.isIndependent ? "independent" : ""),
      )
        .entries()
        .map(([key, entries]) => {
          let seats = 0;
          for (const entry of entries) {
            seats += entry.seats;
          }
          const party = info.get(entries[0]?.party ?? "");
          return {
            key,
            label:
              party == null
                ? key === "independent"
                  ? m.seats_independents()
                  : m.record_none()
                : (party.short ?? party.label),
            seats,
          };
        })
        .toArray()
        .toSorted((a, b) => b.seats - a.seats);
      const shown = totals.slice(0, totals.length > 9 ? 8 : 9);
      const hasOthers = totals.length > shown.length;
      const cells = (
        parties: Array<{
          party: string | null;
          isIndependent: boolean;
          seats: number;
        }>,
        seats: number,
      ) => {
        const row = shown.map(
          (column) =>
            parties.find(
              (entry) =>
                (entry.party ?? (entry.isIndependent ? "independent" : "")) ===
                column.key,
            )?.seats ?? 0,
        );
        let listed = 0;
        for (const cell of row) {
          listed += cell;
        }
        return [
          ...row.map((cell) => count(cell)),
          ...(hasOthers ? [count(seats - listed)] : []),
          count(seats),
        ];
      };
      let all = 0;
      for (const entry of totals) {
        all += entry.seats;
      }
      return [
        `## ${m.regions_title({ name: chamber.name, noun: chamber.noun ?? "" })}`,
        table(
          [
            chamber.noun == null
              ? m.regions_place()
              : `${chamber.noun.charAt(0).toUpperCase()}${chamber.noun.slice(1)}`,
            ...shown.map((column) => column.label),
            ...(hasOthers ? [m.regions_others()] : []),
            m.regions_total(),
          ],
          [
            ...chamber.regions.map((region) => [
              region.name,
              ...cells(region.parties, region.seats),
            ]),
            [
              m.regions_total(),
              ...cells(
                totals.map((entry) => ({
                  party:
                    entry.key === "independent" || entry.key === ""
                      ? null
                      : entry.key,
                  isIndependent: entry.key === "independent",
                  seats: entry.seats,
                })),
                all,
              ),
            ],
          ],
        ),
      ];
    }),
    ...(byeRows.length === 0
      ? []
      : [
          `## ${m.byes_title()}`,
          ...(byes?.general == null
            ? []
            : [
                link(
                  m.byes_parliament({ date: date(byes.general.date) }),
                  byes.general.path,
                ),
              ]),
          `${
            byes?.until == null
              ? m.byes_lede_open({ count: byeRows.length })
              : m.byes_lede({ count: byeRows.length, year: String(byes.until) })
          } ${gained === 0 ? m.byes_changed_none() : m.byes_changed({ count: gained })}`,
          table(
            [
              m.col_date(),
              m.col_seat(),
              m.col_result(),
              m.col_majority(),
              m.col_previous_member(),
            ],
            listed.map((row) => [
              date(row.date),
              row.isHere
                ? `${link(row.name, row.path)} (${m.byes_here()})`
                : link(row.name, row.path),
              row.to == null
                ? null
                : row.isGain
                  ? m.gains_seat({ to: label(row.to), from: label(row.from) })
                  : m.byes_hold({ party: label(row.to) }),
              row.majority == null ? null : count(row.majority),
              row.member == null
                ? null
                : (reasons[row.reason ?? ""]?.({
                    name: link(row.member.name, row.member.path),
                  }) ?? link(row.member.name, row.member.path)),
            ]),
          ),
          ...(listed.length < byeRows.length
            ? [
                m.byes_holds_hidden({
                  count: count(
                    byeRows.filter((row) => !listed.includes(row)).length,
                  ),
                }),
              ]
            : []),
        ]),
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
  handover: Extract<Data, { kind: "contest" }>["handover"],
  additional: Extract<Data, { kind: "contest" }>["additional"],
  successors: Extract<Data, { kind: "contest" }>["successors"],
  statutes: Extract<Data, { kind: "contest" }>["statutes"],
  lists: Extract<Data, { kind: "contest" }>["lists"],
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
  const [top, next] = contest.rows
    .filter((row) => row.current != null && !row.key.startsWith("kind:"))
    .toSorted((a, b) => (b.current?.votes ?? 0) - (a.current?.votes ?? 0));
  const label =
    handover?.party?.short ?? handover?.party?.label ?? m.seats_independents();
  const party = handover?.alliance ?? label;
  const from =
    handover?.from?.short ?? handover?.from?.label ?? m.seats_independents();
  const majority =
    top?.current != null && next?.current != null && top.current.votes > 0
      ? count(top.current.votes - next.current.votes)
      : null;
  const [gain, hold, allied] =
    handover?.lead === "margin"
      ? [m.change_gain_margin, m.change_hold_margin, m.change_allied_margin]
      : [m.change_gain, m.change_hold, m.change_allied];
  const won = contest.rows.filter((row) => (row.current?.seats ?? 0) > 0);
  const held =
    handover == null || contest.current == null
      ? contest.kind === "list_allocation" && won.length > 1
        ? [
            m.change_seats_list({
              changes: new Intl.ListFormat(getLocale(), {
                style: "long",
                type: "conjunction",
              }).format(
                won.map((row) =>
                  m.change_seats_count({
                    party: row.short ?? row.label,
                    count: count(row.current?.seats ?? 0),
                  }),
                ),
              ),
            }),
          ]
        : []
      : [
          `${
            handover.seats != null
              ? (handover.seats.some((seat) => seat.change != null)
                  ? m.change_seats_list
                  : m.change_seats)({
                  changes: new Intl.ListFormat(getLocale(), {
                    style: "long",
                    type: "conjunction",
                  }).format(
                    handover.seats.map((seat) => {
                      const to =
                        seat.party?.short ??
                        seat.party?.label ??
                        m.seats_independents();
                      if (seat.change != null) {
                        return m.change_seats_net({
                          party: to,
                          count: count(seat.count),
                          change:
                            seat.change === 0
                              ? "±0"
                              : `${seat.change > 0 ? "+" : "−"}${count(Math.abs(seat.change))}`,
                        });
                      }
                      const before =
                        seat.from?.short ??
                        seat.from?.label ??
                        m.seats_independents();
                      return seat.isGain
                        ? seat.count > 1
                          ? m.change_seats_gain({
                              to,
                              from: before,
                              count: count(seat.count),
                            })
                          : m.gains_seat({ to, from: before })
                        : seat.count > 1
                          ? m.change_seats_hold({
                              party: to,
                              count: count(seat.count),
                            })
                          : m.byes_hold({ party: to });
                    }),
                  ),
                })
              : handover.alliance != null
                ? majority == null
                  ? m.change_allied_bare({ party, label })
                  : allied({ party, label, majority })
                : majority == null
                  ? handover.isGain
                    ? m.change_gain_bare({ party, from })
                    : m.change_hold_bare({ party })
                  : handover.isGain
                    ? gain({ party, from, majority })
                    : hold({ party, majority })
          }${handover.basis === "same" ? "" : "*"}`,
          ...(handover.basis === "same"
            ? []
            : [
                `${String.raw`\*`} ${(handover.basis === "area" ? m.change_note_area : m.change_note_name)({ year: String(handover.since) })} ${link(handover.prior.name, handover.prior.path)}.`,
              ]),
        ];
  return [
    `# ${contest.name}`,
    byline(contest.breadcrumbs, link, contest.election.date),
    contest.scope.trail.length > 1
      ? contest.scope.trail
          .map((step) => link(step.name, step.path))
          .join(" / ")
      : "",
    ...contest.wording.map((row) => {
      const source = m.wording_source({ publisher: row.publisher });
      return row.kind === "link"
        ? `[${m.wording_link()}](${row.url ?? ""})`
        : `${row.kind === "text" ? m.wording_text() : m.wording_summary()}: ${row.body ?? ""} (${row.url == null ? source : `[${source}](${row.url})`})`;
    }),
    ...(contest.kind === "ballot_question" &&
    top?.current != null &&
    next?.current != null
      ? [
          (["won", "elected"].includes(top.current.outcome ?? "")
            ? m.race_lede_won
            : m.race_lede_votes)({
            party: top.label,
            share: percent(top.current.share),
            margin: points(top.current.share - next.current.share).replace(
              /^\+/u,
              "",
            ),
            runner: next.label,
          }),
        ]
      : []),
    ...contest.notes.map((note) => `> ${note.body}`),
    ...compared,
    ...held,
    ...(handover?.vacated == null
      ? []
      : [
          (VACANCIES[handover.vacated.via ?? ""] ?? m.byes_reason_none)({
            name: handover.vacated.name,
          }),
        ]),
    resultsMarkdown(contest, then),
    ...(lists == null ||
    lists.isRanked ||
    lists.seats === 0 ||
    additional != null
      ? []
      : [
          `## ${m.lists_unranked_title()}`,
          m.lists_unranked_summary({ seats: lists.seats }),
          table(
            [m.col_party(), m.col_seats(), m.col_members()],
            lists.lists
              .filter((list) => list.seats > 0)
              .map((list) => [
                list.label,
                count(list.seats),
                list.members
                  .filter((member) => member.fate === "elected")
                  .map((member) => member.name)
                  .join(", "),
              ]),
          ),
        ]),
    ...(additional == null
      ? []
      : [
          `## ${m.ams_title()}`,
          `${m.ams_lede({ seats: count(additional.seats) })} ${
            additional.isCounted
              ? additional.isReproduced
                ? m.ams_reproduced()
                : m.ams_differs({
                    parties: new Intl.ListFormat(getLocale(), {
                      style: "long",
                      type: "conjunction",
                    }).format(
                      additional.parties
                        .filter((party) => party.computed !== party.declared)
                        .map((party) =>
                          m.ams_differs_party({
                            party: party.short ?? party.label,
                            computed: count(party.computed ?? 0),
                            declared: count(party.declared),
                          }),
                        ),
                    ),
                  })
              : m.ams_uncounted()
          }`,
          table(
            [
              m.col_party(),
              ...(additional.isCounted ? [m.col_list_votes()] : []),
              m.col_constituency_seats(),
              ...additional.picks.map((_, seat) =>
                m.ams_seat({ count: seat + 1 }),
              ),
              m.col_list_seats(),
              m.col_total_seats(),
            ],
            additional.parties
              .filter(
                (party) =>
                  party.constituencies +
                    party.declared +
                    (party.computed ?? 0) >
                    0 || party.key === additional.next?.key,
              )
              .map((party) => [
                party.label,
                ...(additional.isCounted
                  ? [party.votes == null ? null : count(party.votes)]
                  : []),
                count(party.constituencies),
                ...additional.picks.map((pick, seat) => {
                  const quotient = party.quotients[seat];
                  return quotient == null
                    ? null
                    : pick === party.key
                      ? `**${count(Math.round(quotient))}**`
                      : count(Math.round(quotient));
                }),
                count(party.declared),
                count(party.constituencies + party.declared),
              ]),
          ),
          additional.next == null
            ? ""
            : m.ams_next({
                party:
                  additional.parties.find(
                    (party) => party.key === additional.next?.key,
                  )?.label ?? "",
                quotient: count(Math.round(additional.next.quotient)),
              }),
          `### ${m.ams_constituency_members()}`,
          additional.constituencies
            .map(
              (seat) =>
                `- ${link(seat.name, seat.path)}${seat.member == null ? "" : `: ${link(seat.member, seat.memberPath)}`}`,
            )
            .join("\n"),
          ...(additional.members.length === 0
            ? []
            : [
                `### ${m.ams_list_members()}`,
                Map.groupBy(additional.members, (member) => member.key)
                  .entries()
                  .map(
                    ([key, elected]) =>
                      `- ${additional.parties.find((party) => party.key === key)?.label ?? ""}: ${elected.map((member) => link(member.name, member.path)).join(", ")}`,
                  )
                  .toArray()
                  .join("\n"),
              ]),
        ]),
    ...countMarkdown(contest),
    ...placesMarkdown(contest.places, link, then),
    ...(contest.children.length === 0
      ? []
      : [
          `## ${m.children_title()}`,
          contest.children
            .map((child) => `- ${link(child.name, child.path)}`)
            .join("\n"),
        ]),
    ...(contest.origins == null ||
    contest.origins.length === 0 ||
    contest.series.find((entry) => entry.current)?.year == null ||
    contest.origins.every((origin) =>
      statutes.some((entry) => !entry.isNext && entry.name === origin.name),
    )
      ? []
      : [
          contest.origins.length === 1 &&
          (contest.origins[0]?.share ?? 0) >= 0.9 &&
          contest.origins[0]?.first != null &&
          contest.origins[0].year != null &&
          contest.origins[0].first < contest.origins[0].year
            ? m.origins_one({
                year: String(
                  contest.series.find((entry) => entry.current)?.year,
                ),
                seat: link(contest.origins[0].name, contest.origins[0].path),
                from: String(contest.origins[0].first),
                to: String(contest.origins[0].year),
              })
            : m.origins_many({
                year: String(
                  contest.series.find((entry) => entry.current)?.year,
                ),
                seats: new Intl.ListFormat(getLocale(), {
                  type: "conjunction",
                }).format(
                  contest.origins.map((origin) =>
                    m.origins_item({
                      name: link(origin.name, origin.path),
                      share: percent(origin.share, 0),
                    }),
                  ),
                ),
              }),
        ]),
    ...(successors == null ||
    successors.places.every((place) =>
      statutes.some((entry) => entry.isNext && entry.name === place.name),
    )
      ? []
      : [
          successors.places.length === 1 &&
          (successors.places[0]?.share ?? 0) >= 0.9
            ? (successors.method === "population"
                ? m.successors_one_population
                : m.successors_one)({
                year: successors.year,
                seat: link(
                  successors.places[0]?.name ?? "",
                  successors.places[0]?.path ?? null,
                ),
              })
            : (successors.method === "population"
                ? m.successors_many_population
                : m.successors_many)({
                year: successors.year,
                seats: new Intl.ListFormat(getLocale(), {
                  type: "conjunction",
                }).format(
                  successors.places.map((place) =>
                    m.origins_item({
                      name: link(place.name, place.path),
                      share: percent(place.share, 0),
                    }),
                  ),
                ),
              }),
        ]),
    ...statutes.map((entry) =>
      [
        entry.isNext
          ? m.statutes_into({
              year: entry.year,
              seat: link(entry.name, entry.path),
            })
          : m.statutes_within({
              year: entry.year,
              seat: link(entry.name, entry.path),
              from: entry.from ?? "",
              to: entry.to ?? "",
            }),
        ...(entry.basis == null
          ? []
          : [
              m.statutes_basis({
                basis:
                  entry.url == null
                    ? entry.basis
                    : `[${entry.basis}](${entry.url})`,
              }),
            ]),
      ].join(" "),
    ),
    ...(contest.series.length < 2
      ? []
      : [
          `## ${m.series_label()}`,
          contest.series
            .map(
              (entry) =>
                `- ${entry.current ? entry.name : link(entry.name, entry.path)}${entry.winner == null ? "" : entry.winner.seats != null && entry.winner.seats.length > 1 ? `: ${entry.winner.seats.map((seat) => seat.label).join(", ")}` : `: ${entry.winner.label}${entry.winner.total > 0 ? `, ${percent(entry.winner.share)}` : ""}`}`,
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

function countMarkdown(contest: Extract<Data, { kind: "contest" }>["contest"]) {
  const { count: tally } = contest;
  if (tally == null || tally.last < 2) {
    return [];
  }
  const rounds = Array.from({ length: tally.last }, (_, step) => step);
  const { stv } = tally;
  if (stv != null) {
    const list = new Intl.ListFormat(getLocale(), {
      style: "long",
      type: "conjunction",
    });
    const names = (keys: Array<string>) =>
      list.format(
        keys.map(
          (key) => tally.rows.find((row) => row.key === key)?.label ?? "",
        ),
      );
    const signed = (value: number) =>
      Math.abs(value) < 0.5
        ? ""
        : ` (${value > 0 ? "+" : "−"}${count(Math.abs(value))})`;
    return [
      `## ${m.stv_title()}`,
      m.stv_lede({ seats: count(stv.seats), quota: count(stv.quota) }),
      (stv.isDerived ? m.stv_quota_computed : m.stv_quota_declared)({
        quota: count(stv.quota),
      }),
      table(
        [
          m.col_candidate(),
          m.col_result(),
          ...rounds.map((round) => m.stv_count({ count: round + 1 })),
        ],
        [
          [
            "",
            "",
            ...stv.steps.map((step) =>
              step.kind === "first"
                ? m.stv_first()
                : step.kind === "surplus"
                  ? m.stv_surplus({ names: names(step.from) })
                  : step.kind === "exclusion"
                    ? m.stv_exclusion({ names: names(step.from) })
                    : m.stv_none(),
            ),
          ],
          ...tally.rows.map((row) => {
            const elected = stv.steps.findIndex((step) =>
              step.elected.includes(row.key),
            );
            const out = stv.steps.findIndex(
              (step) =>
                step.kind === "exclusion" && step.from.includes(row.key),
            );
            return [
              row.label,
              elected !== -1
                ? row.votes.every((value) => (value ?? 0) < stv.quota)
                  ? m.stv_elected_short()
                  : m.stv_elected({ count: elected + 1 })
                : out === -1
                  ? null
                  : m.stv_excluded({ count: out + 1 }),
              ...rounds.map((round) => {
                const votes = row.votes[round];
                const before = round === 0 ? null : row.votes[round - 1];
                return votes == null
                  ? before == null
                    ? null
                    : `−${count(before)}`
                  : `${count(votes)}${before == null ? "" : signed(votes - before)}`;
              }),
            ];
          }),
          [
            m.count_exhausted(),
            "",
            ...tally.exhausted.map((spent, index) => {
              const prior = index === 0 ? null : tally.exhausted[index - 1];
              return spent == null
                ? null
                : `${count(spent)}${prior == null ? "" : signed(spent - prior)}`;
            }),
          ],
        ],
      ),
    ];
  }
  const totals = rounds.map((round) => {
    let total = 0;
    for (const row of tally.rows) {
      total += row.votes[round] ?? 0;
    }
    return total;
  });
  return [
    `## ${m.count_table()}`,
    table(
      [
        m.col_candidate(),
        ...rounds.map((round) =>
          round === tally.last - 1
            ? m.count_final()
            : m.count_round({ round: round + 1 }),
        ),
      ],
      [
        ...tally.rows.map((row) => [
          row.label,
          ...rounds.map((round) => {
            const votes = row.votes[round];
            return votes == null || totals[round] === 0
              ? null
              : `${count(votes)} (${percent(votes / (totals[round] ?? 1))})`;
          }),
        ]),
        [
          m.count_exhausted(),
          ...tally.exhausted.map((spent) =>
            spent == null ? null : count(spent),
          ),
        ],
      ],
    ),
  ];
}

function resultsMarkdown(
  contest: Extract<Data, { kind: "contest" }>["contest"],
  then: string | null,
) {
  let won = 0;
  for (const row of contest.rows) {
    won += row.current?.seats ?? 0;
  }
  const hasSeats = won > 1;
  const hasParty = contest.rows.some(
    (row) => row.current?.person != null && row.short != null,
  );
  const parties = new Map(
    contest.rows.map((row) => [
      row.key,
      row.short ??
        ((row.current ?? row.earlier)?.isIndependent === true
          ? m.seats_independents()
          : null),
    ]),
  );
  const results = contest.rows
    .filter(
      (row) => row.current != null || (then != null && row.earlier != null),
    )
    .map(({ key, label, current, earlier, delta }) => [
      current?.isIncumbent === true && current.person != null
        ? m.results_incumbent_twin({ name: label })
        : label,
      ...(hasParty
        ? [
            (current ?? earlier)?.person == null
              ? null
              : (parties.get(key) ?? null),
          ]
        : []),
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
  if (contest.current == null && contest.declared != null) {
    const said = (
      {
        tacit: m.declared_tacit,
        show_of_hands: m.declared_show_of_hands,
        appointed: m.declared_appointed,
        counted: m.declared_counted,
      } as Record<string, () => string>
    )[contest.declared.kind]?.();
    return [
      ...(said == null ? [] : [said, ""]),
      ...contest.declared.rows.map(
        (row) =>
          `- ${row.label}${row.short == null || row.short === row.label ? "" : ` (${row.short})`}: ${outcome(row.outcome)}`,
      ),
    ].join("\n");
  }
  return contest.current == null || results.length === 0
    ? m.no_results()
    : `${table(
        [
          contest.rows.some((row) => row.key.startsWith("answer:"))
            ? m.col_answer()
            : m.col_option(),
          ...(hasParty ? [m.col_party()] : []),
          contest.count?.stv != null && contest.count.round === 1
            ? m.stv_first()
            : m.col_votes(),
          m.col_share(),
          ...(hasSeats ? [m.col_seats()] : []),
          ...(then == null
            ? []
            : [m.col_share_in({ year: then }), m.col_change()]),
          m.col_result(),
        ],
        results,
      )}\n\n${contest.ballot == null ? m.stat_valid() : m.stat_votes_cast()}: ${count(contest.current.valid)}${contest.ballot == null ? "" : ` ${m.term_votes_cast({ count: String(contest.ballot) })}`}${
        contest.turnout == null || contest.turnout.registered === 0
          ? ""
          : `\n\n${m.stat_turnout()}: ${percent(contest.turnout.voters / contest.turnout.registered)} (${m.stat_turnout_count({ voters: count(contest.turnout.voters), registered: count(contest.turnout.registered) })})${contest.turnout.basis === "rejected" ? ` ${m.turnout_basis_rejected()}` : contest.turnout.basis === "valid" ? ` ${m.turnout_basis_valid()}` : ""}`
      }${contest.partial == null ? "" : `\n\n${m.race_partial_counties(contest.partial)}`}${contest.rows
        .flatMap((row) =>
          row.current?.outcome == null ||
          row.current.note == null ||
          isDerived(row.current.note)
            ? []
            : [
                `\n\n${row.key.startsWith("answer:") ? row.current.note : m.outcome_note({ names: row.label, note: row.current.note })}`,
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
          row.margin == null
            ? null
            : places.legend.length === 1
              ? m.map_unopposed()
              : points(row.margin),
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
    ...sets.map(({ source, isDefault, status, stage }) => {
      const marks =
        sets.length < 2
          ? []
          : [
              ...((
                contest.current == null
                  ? isDefault
                  : contest.current.sets.some(
                      (other) =>
                        other.stage === stage &&
                        other.source.publisher === source.publisher &&
                        other.source.title === source.title,
                    )
              )
                ? [m.source_shown()]
                : []),
              ...(status === "active" ? [] : [statuses[status]?.() ?? status]),
            ];
      return `- ${marks.length === 0 ? "" : `${marks.join(", ")}: `}${source.url == null ? source.title : `[${source.title}](${source.url})`}, ${source.publisher}${source.license == null ? "" : `. ${m.source_license({ license: source.license })}`}`;
    }),
    ...contest.citations
      .filter(
        (citation) =>
          !sets.some(
            ({ source }) =>
              source.title === citation.title &&
              source.publisher === citation.publisher,
          ),
      )
      .map(
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
    const questions = data.places.series
      .filter((series) => series.isQuestion)
      .flatMap((series) => series.points)
      .toSorted((x, y) => x.date.localeCompare(y.date));
    return [
      ...heading,
      ...(data.places.series.length === 0
        ? [m.versus_places_none()]
        : [
            ...data.places.series
              .filter((series) => !series.isQuestion)
              .map((series) => ({
                ...series,
                name: series.name ?? m.versus_governor(),
              })),
            ...[
              { name: m.versus_questions(), kind: "shared", points: questions },
            ].filter((series) => series.points.length > 0),
          ].flatMap((series) => [
            `## ${series.name}`,
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
