import type { RequestEvent } from "@sveltejs/kit";
import { call } from "@orpc/server";
import { error, isHttpError, isRedirect, redirect } from "@sveltejs/kit";
import { effective, stand } from "#lib/caucus.js";
import {
  COUNTRY_GOALS,
  isCountryGoal,
  isGoal,
  levelLabel,
  PARTS,
  label as reasonLabel,
  REASONS,
  spans,
} from "#lib/coverage.js";
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
  coverageOverview,
  home,
  party,
  person,
  results,
} from "#lib/server/pages.js";
import { router } from "#lib/server/rpc/router.js";
import { comeback, verdict } from "#lib/verdict.js";

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

const PLACE_KINDS: Record<string, () => string> = {
  county: m.polity_kind_county,
  municipality: m.polity_kind_municipality,
  school_district: m.polity_kind_school_district,
  special_district: m.polity_kind_special_district,
  judicial_district: m.polity_kind_judicial_district,
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
    redirect(307, `/${path}${search.size > 0 ? `?${search}` : ""}`);
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
  if (head === "coverage" && rest.length === 1) {
    return coverage(rest[0] ?? "", search);
  }
  switch (rest.length === 0 ? head : "") {
    case "coverage": {
      return coverageOverview(search);
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
  if (found.kind !== "polity") {
    return found;
  }
  const [record, holders] = await Promise.all([
    call(router.polities.record, { id: found.id }),
    ["sovereign_state", "supranational", "federal_unit"].includes(
      found.polity.kind,
    )
      ? null
      : call(router.polities.holders, { id: found.id }),
  ]);
  return { ...found, record, holders };
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
      return data.polity === "us"
        ? coverageMarkdown(data, link)
        : countryMarkdown(data, link);
    }
    case "coverage_overview": {
      return overviewMarkdown(data, link);
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
      return polityMarkdown(data.polity, data.record, data.holders, link);
    }
    case "election": {
      return electionMarkdown(data.election, data.sections, data.byes, link);
    }
    case "day": {
      const { local } = data;
      const governments = local.tiers.flatMap((tier) => tier.governments);
      return [
        `# ${m.local_title({ year: data.date.slice(0, 4), place: local.name })}`,
        `${local.breadcrumbs.map((crumb) => link(crumb.name, crumb.path)).join(" / ")}, ${date(data.date)}`,
        m.local_lede({
          governments: m.local_governments({ count: governments.length }),
          contests: m.race_contests({
            count: governments.flatMap((government) => government.contests)
              .length,
          }),
        }),
        ...local.tiers.flatMap((tier) => [
          `## ${{ county: m.local_tier_county, municipal: m.local_tier_municipal, school: m.local_tier_school, special: m.local_tier_special }[tier.kind]()}`,
          ...tier.governments.flatMap((government) => [
            `### ${link(government.name, government.path)}`,
            table(
              [m.col_name(), m.col_leader(), m.col_share(), m.col_result()],
              government.contests.map((contest) => {
                const final =
                  contest.runoff?.isWon === true ? contest.runoff : null;
                const shown = final ?? contest;
                const round =
                  contest.runoff == null
                    ? ""
                    : (contest.runoff.isGeneral
                        ? m.wards_round_general
                        : m.wards_round_runoff)({
                        date: new Intl.DateTimeFormat(getLocale(), {
                          month: "long",
                          day: "numeric",
                          timeZone: "UTC",
                        }).format(new Date(`${contest.runoff.date}T00:00:00Z`)),
                      });
                return [
                  link(contest.name, contest.path),
                  shown.leader,
                  shown.share == null ? null : percent(shown.share),
                  contest.runoff == null || contest.share == null
                    ? null
                    : final == null
                      ? m.wards_pending({
                          share: percent(contest.share),
                          round,
                        })
                      : final.person != null && final.person === contest.person
                        ? m.wards_kept({
                            share: percent(contest.share),
                            round,
                            final: percent(final.share),
                          })
                        : m.wards_took({
                            round,
                            final: percent(final.share),
                            name: contest.leader ?? "",
                            share: percent(contest.share),
                          }),
                ];
              }),
            ),
          ]),
        ]),
      ];
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
        data.rounds,
        link,
      );
    }
  }
}

const share = (row: { complete: number; total: number }) =>
  m.coverage_percent({
    percent: percent(row.complete / Math.max(1, row.total), 1),
  });

function coverageMarkdown(
  data: Extract<Data, { kind: "coverage" }>,
  link: Link,
) {
  const year = String(new Date().getFullYear());
  return [
    `# ${m.coverage_title()}`,
    m.coverage_lede(),
    (data.current ? m.twin_coverage_current_on : m.twin_coverage_current)({
      year,
    }),
    ...data.goals
      .flatMap((goal) =>
        isGoal(goal.goal) ? [{ ...goal, goal: goal.goal }] : [],
      )
      .flatMap((row) => [
        `## ${TITLES[row.goal]()}`,
        [
          ...(row.goal === "local" && row.populationTotal > 0
            ? [
                m.coverage_progress_local_population({
                  percent: percent(
                    row.populationComplete / row.populationTotal,
                    1,
                  ),
                }),
              ]
            : []),
          `${(row.goal === "local" ? m.coverage_progress_local : row.goal === "turnout" ? m.coverage_progress_turnout : m.coverage_progress)({ complete: count(row.complete), total: count(row.total) })}, ${share(row)}`,
          ...(row.icpsr > 0
            ? [m.coverage_icpsr_of({ count: count(row.icpsr) })]
            : []),
        ].join(", "),
        ...(row.goal === "local" && row.populationTotal > 0
          ? [
              `${m.coverage_local_weights()} ${m.coverage_local_estimated({ percent: percent(row.populationEstimated / row.populationTotal, 1) })}`,
            ]
          : []),
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

function countryMarkdown(
  data: Extract<Data, { kind: "coverage" }>,
  link: Link,
) {
  const year = String(new Date().getFullYear());
  const { summary } = data;
  const titles = {
    national: m.coverage_country_goal_national,
    fine: m.coverage_country_goal_fine,
    shapes: m.coverage_country_goal_shapes,
    subnational: m.coverage_country_goal_subnational,
    local: m.coverage_country_goal_local,
    referendums: m.coverage_country_goal_referendums,
    ep: m.coverage_country_goal_ep,
  };
  const rows = data.goals.flatMap((row) =>
    isCountryGoal(row.goal) ? [{ ...row, goal: row.goal }] : [],
  );
  return [
    `# ${m.coverage_country_title({ name: summary.name })}`,
    m.coverage_country_lede(),
    ...(summary.first == null || summary.last == null
      ? []
      : [
          [
            m.coverage_country_facts({
              elections: count(summary.elections),
              contests: count(summary.contests),
              first: String(summary.first),
              last: String(summary.last),
            }),
            ...(summary.finestNoun == null
              ? []
              : [m.coverage_country_finest({ noun: summary.finestNoun })]),
          ].join(" "),
        ]),
    (data.current ? m.twin_coverage_current_on : m.twin_coverage_current)({
      year,
    }),
    ...COUNTRY_GOALS.flatMap((goal) => {
      const row = rows.find((found) => found.goal === goal);
      const levels = summary.levels.filter((level) =>
        goal === "fine"
          ? level.fine
          : goal === "shapes"
            ? level.goal === "national" && level.unit === "district"
            : level.goal === goal,
      );
      if (row == null && levels.length === 0) {
        return [];
      }
      return [
        `## ${goal === "national" ? `${m.coverage_goal_now()}: ` : ""}${titles[goal]()}`,
        row == null
          ? m.coverage_country_none()
          : [
              `${m.coverage_progress({ complete: count(row.complete), total: count(row.total) })}, ${share(row)}`,
              REASONS[goal]
                .filter((reason) => reason === "complete" || row[reason] > 0)
                .map(
                  (reason) =>
                    `- ${reasonLabel(goal, reason, data.polity)}: ${count(row[reason])}`,
                )
                .join("\n"),
            ].join("\n\n"),
        `### ${m.coverage_ledger()}`,
        levels
          .map((level) => {
            const notes = summary.notes.filter(
              (note) =>
                note.level === level.key &&
                (note.goal === level.goal || note.goal === goal),
            );
            const gaps = notes.filter(
              (note) => note.from != null && note.listed !== false,
            );
            const counted = spans(
              data.counted.find((found) => found.level === level.key)?.years ??
                [],
              gaps.flatMap((note) => (note.from == null ? [] : [note.from])),
            );
            const unlisted = notes.filter(
              (note) => note.listed === false && note.from != null,
            );
            return [
              `- ${levelLabel(data.polity, level.key)}: ${counted === "" ? m.coverage_ledger_nothing() : `${m.coverage_ledger_counted()} ${counted}`}`,
              ...gaps.map(
                (note) =>
                  `  - ${m.coverage_ledger_uncounted()} ${note.from === note.to ? note.from : `${note.from}–${note.to}`}. ${note.note}${(note.held ?? 0) > 0 ? ` ${m.coverage_ledger_held({ count: count(note.held ?? 0) })}` : ""}`,
              ),
              ...notes
                .filter((note) => note.from == null)
                .map((note) => `  - ${note.note}`),
              ...(unlisted.length === 0
                ? []
                : [
                    `  - ${m.coverage_ledger_unlisted({ years: unlisted.map((note) => `${note.from} (${count(note.held ?? 0)})`).join(", ") })}`,
                  ]),
            ].join("\n");
          })
          .join("\n"),
      ];
    }),
    ...(data.gaps.length === 0
      ? []
      : [
          `## ${m.coverage_where()}`,
          titles.national(),
          table(
            [
              (data.isFlat
                ? m.coverage_column_name_flat
                : m.coverage_column_name_country)(),
              m.coverage_column_short(),
              m.coverage_column_done(),
            ],
            data.gaps.map((row) => [
              data.isFlat
                ? levelLabel(data.polity, row.key)
                : link(
                    row.label ??
                      (row.key === "" ? m.coverage_region_none() : row.key),
                    row.path,
                  ),
              count(row.total - row.complete),
              share(row),
            ]),
          ),
        ]),
    ...(Object.keys(summary.sources).length === 0
      ? []
      : [
          `## ${m.coverage_sources()}`,
          Object.values(summary.sources)
            .map(
              (source) =>
                `- [${source.title}](${source.url})${source.publisher == null ? "" : `, ${source.publisher}`}`,
            )
            .join("\n"),
        ]),
    link(m.coverage_country_back(), "coverage"),
  ];
}

function overviewMarkdown(
  data: Extract<Data, { kind: "coverage_overview" }>,
  link: Link,
) {
  const year = String(new Date().getFullYear());
  return [
    `# ${m.coverage_overview_title()}`,
    m.coverage_overview_lede(),
    ...(data.totals.first == null || data.totals.last == null
      ? []
      : [
          m.coverage_overview_totals({
            elections: count(data.totals.elections),
            contests: count(data.totals.contests),
            first: String(data.totals.first),
            last: String(data.totals.last),
          }),
        ]),
    (data.current ? m.twin_coverage_current_on : m.twin_coverage_current)({
      year,
    }),
    table(
      [
        m.coverage_overview_column_country(),
        m.coverage_overview_column_goal(),
        m.coverage_overview_column_span(),
        m.coverage_overview_column_finest(),
        m.coverage_overview_column_shapes(),
      ],
      data.polities.map((polity) => {
        const lead = polity.goals.find(
          (row) =>
            row.goal === (polity.polity === "us" ? "county" : "national"),
        );
        const shapes = polity.goals.find((row) => row.goal === "shapes");
        return [
          link(polity.name, `coverage/${polity.polity}`),
          lead == null
            ? m.coverage_overview_none()
            : `${(polity.polity === "us" ? m.coverage_overview_goal_us : m.coverage_overview_goal_national)()}: ${m.coverage_progress({ complete: count(lead.complete), total: count(lead.total) })}, ${percent(lead.complete / Math.max(1, lead.total), 1)}`,
          polity.first == null || polity.last == null
            ? ""
            : `${polity.first}–${polity.last}`,
          polity.finestNoun ?? "",
          shapes == null
            ? m.coverage_overview_shapes_none()
            : m.coverage_overview_shapes({
                percent: percent(
                  shapes.complete / Math.max(1, shapes.total),
                  1,
                ),
              }),
        ];
      }),
    ),
    `## ${m.coverage_overview_explain()}`,
    m.coverage_overview_explain_count(),
    m.coverage_overview_explain_goal(),
    m.coverage_overview_explain_gaps(),
    m.coverage_overview_explain_shapes(),
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
      : from?.slice(0, 4) === to.slice(0, 4)
        ? to.slice(0, 4)
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
    ...(() => {
      if (person.record.length === 0) {
        return [];
      }
      const won = person.record.filter(
        (entry) => entry.outcome === "elected" || entry.outcome === "won",
      ).length;
      const advanced = person.record.filter(
        (entry) => entry.outcome === "advanced",
      ).length;
      const first = Math.min(...person.record.map((entry) => entry.year));
      const last = Math.max(...person.record.map((entry) => entry.year));
      return [
        person.record.length === 1
          ? (won > 0 ? m.person_lede_single_won : m.person_lede_single)({
              year: String(first),
            })
          : first === last
            ? (advanced > 0 ? m.person_lede_advanced_year : m.person_lede_year)(
                {
                  won: count(won),
                  advanced: count(advanced),
                  count: count(person.record.length),
                  year: String(first),
                },
              )
            : (advanced > 0 ? m.person_lede_advanced : m.person_lede)({
                won: count(won),
                advanced: count(advanced),
                count: count(person.record.length),
                first: String(first),
                last: String(last),
              }),
      ];
    })(),
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
                `${spell.isBody ? link(spell.name, spell.path ?? `id/${spell.id}`) : link(spell.name, spell.path)}${
                  "seats" in spell && spell.seats.length > 0
                    ? ` (${spell.seats
                        .map((seat) =>
                          [
                            seat.name,
                            years(seat.from, seat.to),
                            endings[seat.ended ?? ""]?.().replace(
                              /^\p{Lu}/u,
                              (letter) => letter.toLowerCase(),
                            ) ?? null,
                          ]
                            .filter((part) => part != null)
                            .join(", "),
                        )
                        .join("; ")})`
                    : ""
                }`,
                ("spans" in spell ? spell.spans : [spell])
                  .map((part) => years(part.from, part.to))
                  .join(", "),
              ]),
            ),
          ],
    ),
    ...(lanes.length === 0
      ? []
      : [
          `## ${m.person_parties()}`,
          ...(() => {
            const named = lanes.filter(
              (lane) =>
                lane.party != null &&
                (lane.ballots.length > 0 ||
                  lane.spans.some((spell) => spell.kind !== "group")),
            ).length;
            const open = person.isGone
              ? person.died
              : new Date().toISOString().slice(0, 10);
            const dates = lanes
              .flatMap((lane) => [
                ...lane.spans.flatMap((spell) =>
                  spell.from == null
                    ? spell.to == null
                      ? []
                      : [spell.to]
                    : [spell.from, spell.to ?? open].filter(
                        (value) => value != null,
                      ),
                ),
                ...lane.ballots.map((ballot) => ballot.date),
              ])
              .toSorted((a, b) => a.localeCompare(b));
            return named > 1 && dates.length > 0
              ? [
                  m.person_parties_lede({
                    count: String(named),
                    first: dates[0]?.slice(0, 4) ?? "",
                    last: dates.at(-1)?.slice(0, 4) ?? "",
                  }),
                ]
              : [];
          })(),
          table(
            [m.col_party(), m.col_role(), m.person_years()],
            lanes.flatMap((lane) => {
              const party = lane.party?.label ?? m.person_parties_none();
              return [
                ...lane.spans
                  .filter(
                    (spell) =>
                      spell.from != null ||
                      spell.to != null ||
                      (lane.ballots.length === 0 &&
                        lane.spans.every(
                          (peer) => peer.from == null && peer.to == null,
                        )),
                  )
                  .map((spell) => [
                    party,
                    lane.party == null && spell.kind === "office"
                      ? m.person_parties_office_independent()
                      : roles[spell.kind](),
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
            (entry.nominated?.length ?? 0) > 0
              ? new Intl.ListFormat(getLocale(), {
                  type: "conjunction",
                }).format((entry.nominated ?? []).map((party) => party.label))
              : (entry.party?.label ??
                (entry.isIndependent === true
                  ? m.person_parties_none()
                  : null)),
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
  {
    id,
    party,
    record,
    members,
    notes,
    history,
    abroad,
  }: Extract<Data, { kind: "party" }>,
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
      : `# ${(party.scope.polity.path != null && party.polity?.path != null && !party.scope.polity.path.startsWith(`${party.polity.path}/`) ? m.party_abroad_title : m.party_scope_title)({ party: party.name, place: party.scope.polity.name })}`,
    ...(party.polity == null
      ? []
      : [
          m.party_in({
            kind: KINDS[party.kind]?.() ?? party.kind,
            polity: party.polity.name,
          }),
        ]),
    ...((party.namesakes ?? []).length === 0
      ? []
      : [
          m.party_namesakes({
            parties: new Intl.ListFormat(getLocale(), {
              type: "conjunction",
            }).format(
              (party.namesakes ?? []).map((namesake) => {
                const from = namesake.span?.from.slice(0, 4);
                const to = namesake.span?.to.slice(0, 4);
                return link(
                  from == null || to == null
                    ? namesake.party.label
                    : m.party_namesake_years({
                        name: namesake.party.label,
                        years: from === to ? from : `${from}–${to}`,
                      }),
                  `id/${namesake.party.id}`,
                );
              }),
            ),
          }),
        ]),
    ...(abroad.length === 0
      ? []
      : [
          m.party_abroad({
            places: new Intl.ListFormat(getLocale(), {
              type: "conjunction",
            }).format(
              abroad.map((place) =>
                link(place.name, `id/${id}?in=${place.path}`),
              ),
            ),
          }),
        ]),
    ...(party.span == null && party.wikidata == null
      ? []
      : [
          [
            ...(party.span == null
              ? []
              : [
                  `${m.party_ballot()}: ${
                    party.span.from.slice(0, 4) === party.span.to.slice(0, 4)
                      ? party.span.from.slice(0, 4)
                      : m.polity_span({
                          first: party.span.from.slice(0, 4),
                          last: party.span.to.slice(0, 4),
                        })
                  }. ${m.party_elections()}: ${count(record.count)}. ${m.party_contests()}: ${count(party.span.contests)}.`,
                ]),
            ...(party.wikidata == null
              ? []
              : [
                  `[${m.card_wikidata()}](https://www.wikidata.org/wiki/${party.wikidata})`,
                ]),
          ].join(" "),
        ]),
    ...notes.map((note) => `> ${note.body}`),
    ...history.flatMap((series) => [
      `## ${history.length > 1 ? `${m.party_history_title()}, ${series.name}` : m.party_history_title()}`,
      m.party_history_lede(),
      table(
        [
          m.col_year(),
          m.col_election(),
          m.party_history_votes(),
          m.party_history_seats(),
        ],
        series.points
          .toReversed()
          .map((point) => [
            point.year,
            `${link(point.name, point.path)}${point.predecessor == null ? "" : ` (${m.party_history_as({ name: point.predecessor })})`}`,
            point.votes == null || point.votes === 0 || point.total == null
              ? null
              : percent(point.votes / point.total),
            point.seats == null || point.filled == null
              ? null
              : m.party_history_seats_of({
                  seats: count(point.seats),
                  filled: count(point.filled),
                }),
          ]),
      ),
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
          [
            m.col_year(),
            m.col_election(),
            m.party_contests(),
            ...(record.elections.some((election) => election.seats > 0)
              ? [m.col_seats()]
              : []),
            ...(record.elections.some((election) => election.won > 0)
              ? [m.person_won()]
              : []),
          ],
          record.elections.map((election) => [
            election.year,
            link(election.name, election.path),
            count(election.races),
            ...(record.elections.some((other) => other.seats > 0)
              ? [election.seats > 0 ? count(election.seats) : null]
              : []),
            ...(record.elections.some((other) => other.won > 0)
              ? [election.won > 0 ? count(election.won) : null]
              : []),
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
  holders: Extract<Data, { kind: "polity" }>["holders"],
  link: Link,
) {
  const latest = polity.latest;
  const labels = new Map(
    record.parties.map((party) => [party.id, party.short ?? party.label]),
  );
  const tally = new Map<string, number>();
  for (const group of polity.results) {
    for (const race of group.races) {
      if (race.source != null) {
        tally.set(race.source, (tally.get(race.source) ?? 0) + 1);
      }
    }
  }
  const usual = tally
    .entries()
    .toArray()
    .toSorted((a, b) => b[1] - a[1])[0]?.[0];
  const sourced = usual == null ? null : { usual, isMixed: tally.size > 1 };
  return [
    `# ${polity.name}`,
    byline(polity.breadcrumbs, link, null),
    ...(polity.latest == null &&
    polity.results.length > 0 &&
    ["county", "municipality", "school_district", "special_district"].includes(
      polity.kind,
    )
      ? [m.polity_no_local({ place: polity.name })]
      : []),
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
              race.isPartyless
                ? m.record_seats_filled({ count: race.contested })
                : table(
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
    ...(holders == null || holders.groups.length === 0
      ? []
      : [
          `## ${m.holders_title()}`,
          m.holders_lede(),
          ...holders.groups.flatMap((group) => {
            const rows = group.rows.map((row) => {
              const party =
                row.party == null
                  ? null
                  : holders.parties.find((entry) => entry.id === row.party);
              const notes = [
                row.isStale ? m.holders_stale() : null,
                row.elected == null
                  ? null
                  : `${m.holders_won({ date: date(row.elected.date) })} ${link(row.elected.name ?? "", row.elected.person == null ? null : `id/${row.elected.person}`)}`,
                row.next == null
                  ? null
                  : row.next.person != null && row.next.person === row.person
                    ? m.holders_reelected({ date: date(row.next.date) })
                    : `${m.holders_successor({ date: date(row.next.date) })} ${link(row.next.name ?? "", row.next.person == null ? null : `id/${row.next.person}`)}`,
              ].filter((note) => note != null);
              return {
                label: row.label,
                holder:
                  row.via === "unknown"
                    ? m.holders_unknown()
                    : row.via === "vacant" || row.person == null
                      ? m.holders_vacant()
                      : [
                          link(row.name ?? "", `id/${row.person}`),
                          party == null ? null : (party.short ?? party.label),
                        ]
                          .filter((part) => part != null && part !== "")
                          .join(", "),
                since:
                  row.since == null
                    ? null
                    : link(
                        row.via === "unknown"
                          ? m.holders_unknown_note({ date: date(row.since) })
                          : (
                              (
                                {
                                  elected: m.holders_elected,
                                  acting: m.holders_acting,
                                  appointed: m.holders_appointed,
                                  trustee: m.holders_trustee,
                                  vacant: m.holders_vacant_since,
                                } as Record<
                                  string,
                                  (input: { date: string }) => string
                                >
                              )[row.via] ?? m.holders_since
                            )({ date: date(row.since) }),
                        row.contest,
                      ),
                notes: notes.join("; "),
              };
            });
            const only = rows[0];
            return !group.isBody && rows.length === 1 && only != null
              ? [
                  `- **${group.name}${only.label == null ? "" : `, ${only.label}`}**: ${[only.holder, only.since, only.notes].filter((part) => part != null && part !== "").join("; ")}`,
                ]
              : [
                  `### ${group.name}`,
                  table(
                    [m.col_seat(), m.col_holder(), m.col_since()],
                    rows.map((row) => [
                      row.label ?? m.holders_at_large(),
                      row.holder,
                      [row.since, row.notes]
                        .filter((part) => part != null && part !== "")
                        .join("; "),
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
                row.label ??
                labels.get(row.party ?? "") ??
                (row.isIndependent === true
                  ? m.person_parties_none()
                  : series.isOffice && point.winner != null
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
            series.isOffice && point.winner != null
              ? `${[
                  point.winner,
                  parties[0]?.label,
                  point.share == null
                    ? null
                    : point.share >= 1
                      ? m.map_unopposed()
                      : percent(point.share),
                ]
                  .filter((part) => part != null)
                  .join(
                    ", ",
                  )}${point.isRunoff ? ` (${link(m.offices_runoff(), point.contest)})` : ""}`
              : point.parties.every(
                    (row) => row.party == null && row.isIndependent !== true,
                  )
                ? (series.points.every((other) =>
                    other.parties.every(
                      (row) => row.party == null && row.isIndependent !== true,
                    ),
                  )
                    ? m.record_seats_filled
                    : m.record_filled)({ count: point.filled })
                : parties
                    .slice(0, 4)
                    .map((row) => `${row.label} ${count(row.seats)}`)
                    .join(", "),
          ];
        }),
      ),
    ]),
    ...(record.governments.length === 0
      ? []
      : [
          `## ${m.governments_title()}`,
          table(
            [
              m.col_name(),
              m.col_led_by(),
              m.col_party(),
              m.col_from(),
              m.col_to(),
            ],
            record.governments
              .toReversed()
              .map((government) => [
                government.isCaretaker
                  ? `${government.name} (${m.governments_caretaker()})`
                  : government.name,
                government.head,
                government.party == null
                  ? null
                  : (labels.get(government.party) ?? null),
                date(government.from),
                government.to == null
                  ? m.governments_present()
                  : date(government.to),
              ]),
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
          ...(sourced == null
            ? []
            : [
                (sourced.isMixed
                  ? m.polity_results_sourced_most
                  : m.polity_results_sourced_one)({
                  place: polity.name,
                  publisher: sourced.usual,
                }),
              ]),
          table(
            [m.col_date(), m.col_contest(), m.col_leader(), m.col_share()],
            polity.results.flatMap((group) =>
              group.races.map((race) => [
                date(group.date),
                race.source == null || race.source === sourced?.usual
                  ? link(race.name, race.path)
                  : `${link(race.name, race.path)} (${m.polity_results_source({ publisher: race.source })})`,
                race.leaders[0]?.label ?? null,
                race.leaders[0] == null ? null : percent(race.leaders[0].share),
              ]),
            ),
          ),
        ]),
    ...(polity.holdings.length === 0
      ? []
      : [
          `## ${m.polity_holdings_title()}`,
          m.polity_holdings_lede({ place: polity.name }),
          table(
            [m.col_government(), m.col_share()],
            polity.holdings.map((row) => [
              (
                {
                  county: m.coverage_group_county,
                  municipality: m.coverage_group_municipality,
                  township: m.coverage_group_township,
                  school: m.coverage_group_school,
                  college: m.coverage_group_college,
                  special: m.coverage_group_special,
                  other: m.coverage_group_other,
                } as Record<string, () => string>
              )[row.level]?.() ?? row.level,
              percent(row.covered / row.population, 0),
            ]),
          ),
        ]),
    ...(polity.holdings.length === 0
      ? []
      : [
          `## ${m.polity_largest_cities()}`,
          polity.polities
            .filter(
              (child) =>
                child.kind === "municipality" && child.population != null,
            )
            .toSorted((a, b) => (b.population ?? 0) - (a.population ?? 0))
            .slice(0, 10)
            .map(
              (child, index) => `${index + 1}. ${link(child.name, child.path)}`,
            )
            .join("\n"),
        ]),
    ...(polity.polities.length === 0
      ? []
      : [
          `## ${polity.kind === "municipality" ? m.polity_districts() : m.polity_subdivisions()}`,
          ...Map.groupBy(polity.polities, (child) => child.kind)
            .entries()
            .toArray()
            .toSorted(
              ([a], [b]) =>
                (Object.keys(PLACE_KINDS).indexOf(a) + 1 || 99) -
                (Object.keys(PLACE_KINDS).indexOf(b) + 1 || 99),
            )
            .flatMap(([kind, children], _, groups) => [
              ...(groups.length > 1
                ? [`### ${PLACE_KINDS[kind]?.() ?? m.polity_subdivisions()}`]
                : []),
              table(
                [m.col_name(), m.polity_elections(), m.person_years()],
                children.map((child) => [
                  link(child.name, child.path),
                  count(child.elections),
                  span(child.first, child.last),
                ]),
              ),
            ]),
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
  {
    governments,
    regions,
    alongside,
    local,
    counties,
  }: Extract<Data, { kind: "election" }>["sections"],
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
      row.to != null &&
      row.from != null &&
      !row.isOther &&
      label(row.to) !== label(row.from),
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
  const overall = new Map(
    (tiered[0]?.overall ?? []).map((entry) => [entry.key, entry.seats]),
  );
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
          }${election.turnout.reporting < election.turnout.of ? ` ${m.turnout_partial({ reporting: String(election.turnout.reporting), of: String(election.turnout.of) })}` : ""}${election.turnout.basis === "rejected" ? ` ${m.turnout_basis_rejected()}` : election.turnout.basis === "valid" ? ` ${m.turnout_basis_valid()}` : ""}`,
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
          }. ${election.control.isRolled === true ? m.control_rolled() : m.control_derived()}`,
        ]),
    ...(election.composition == null
      ? []
      : [
          `## ${m.composition_title()}`,
          table(
            [
              m.col_party(),
              ...(election.composition.hasBefore ? [m.col_before()] : []),
              m.col_after(),
              ...(election.composition.hasBefore ? [m.col_change()] : []),
            ],
            election.composition.rows.map((row) => [
              row.label,
              ...(election.composition?.hasBefore === true
                ? [count(row.before ?? 0)]
                : []),
              count(row.after),
              ...(election.composition?.hasBefore === true
                ? [points((row.after - (row.before ?? 0)) / 100, 0)]
                : []),
            ]),
          ),
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
              ...(overall.size > 0 ? [m.col_change()] : []),
            ],
            tiers
              .entries()
              .filter(([, entry]) => entry.constituencies + entry.lists > 0)
              .toArray()
              .toSorted(
                ([, a], [, b]) =>
                  b.constituencies + b.lists - (a.constituencies + a.lists),
              )
              .map(([key, entry]) => [
                entry.label,
                count(entry.constituencies),
                count(entry.lists),
                count(entry.constituencies + entry.lists),
                ...(overall.size > 0
                  ? [
                      overall.has(key)
                        ? signed
                            .format(
                              entry.constituencies +
                                entry.lists -
                                (overall.get(key) ?? 0),
                            )
                            .replace("-", "−")
                        : null,
                    ]
                  : []),
              ]),
          ),
        ]),
    ...[...rest.slice(0, at), ...lists, ...rest.slice(at)].flatMap((entry) => {
      const isRound =
        entry.round != null &&
        election.races.some(
          (other) =>
            other !== entry &&
            other.name === entry.name &&
            other.round !== entry.round,
        );
      const race = isRound
        ? {
            ...entry,
            name: m.race_round({ name: entry.name, round: entry.round ?? 1 }),
          }
        : tiered.includes(entry) && entry.kind === "list_allocation"
          ? { ...entry, name: m.race_list({ name: entry.name }) }
          : entry.places?.levels[0] != null &&
              (tiered.includes(entry) ||
                (entry.kind !== "list_allocation" &&
                  entry.count > 1 &&
                  election.races.some(
                    (other) =>
                      other !== entry &&
                      other.name === entry.name &&
                      other.count === 1,
                  )))
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
        race.results.every(
          (row) => !row.key.startsWith("alliance:") || (row.seats ?? 0) === 0,
        ) &&
        race.results.some((row) => (row.seats ?? 0) > 0 && before.has(row.key));
      const units = Map.groupBy(
        race.results.filter((row) => (row.seats ?? 0) > 0),
        (row) =>
          race.partners.find((pair) => pair.includes(row.key))?.[0] ?? row.key,
      )
        .values()
        .map((group) => {
          let won = 0;
          let share = 0;
          for (const row of group) {
            won += row.seats ?? 0;
            share += row.share;
          }
          return group.length === 1
            ? { ...group[0], won }
            : {
                ...group[0],
                won,
                share,
                label: group.map((row) => row.short ?? row.label).join("/"),
              };
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
        (race.kind !== "body_seats" && race.kind !== "list_allocation") ||
        race.body == null ||
        tiered.includes(entry) ||
        leader.key === "unrecorded" ||
        leader.key?.startsWith("person:") === true
          ? null
          : ((race.unopposed?.seats ?? 0) * 10 >= filled ||
              (race.multiple ?? 0) > 0
              ? m.race_lede_seats_only
              : isRound && race.round === 1
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
      const wards = race.wards ?? [];
      if (wards.length > 0) {
        const round = (runoff: { date: string; isGeneral: boolean }) => {
          const day = new Intl.DateTimeFormat(getLocale(), {
            month: "long",
            day: "numeric",
            timeZone: "UTC",
          }).format(new Date(`${runoff.date}T00:00:00Z`));
          return runoff.isGeneral
            ? m.wards_round_general({ date: day })
            : m.wards_round_runoff({ date: day });
        };
        const sent = wards.filter((row) => row.runoff != null);
        const won = wards.filter(
          (row) => row.runoff == null && row.isWon,
        ).length;
        const first = sent[0]?.runoff;
        return [
          `## ${link(race.name, race.path)}`,
          ...(first == null
            ? won === wards.length
              ? [m.wards_lede_all({ count: count(wards.length) })]
              : []
            : [
                m.wards_lede({
                  won: count(won),
                  count: count(wards.length),
                  sent: count(sent.length),
                  round: round(first),
                }),
              ]),
          table(
            [m.col_seat(), m.col_candidate(), m.col_result()],
            wards.map((row) => {
              const final = row.runoff?.isWon === true ? row.runoff : null;
              return [
                link(row.label, row.path),
                (final ?? row).name,
                row.runoff == null
                  ? (row.isWon ? m.wards_won : m.wards_led)({
                      share: percent(row.share),
                    })
                  : final == null
                    ? m.wards_pending({
                        share: percent(row.share),
                        round: round(row.runoff),
                      })
                    : final.person != null && final.person === row.person
                      ? m.wards_kept({
                          share: percent(row.share),
                          round: round(row.runoff),
                          final: percent(final.share),
                        })
                      : m.wards_took({
                          round: round(row.runoff),
                          final: percent(final.share),
                          name: row.name ?? "",
                          share: percent(row.share),
                        }),
              ];
            }),
          ),
        ];
      }
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
                          : row.nominated.length > 0
                            ? new Intl.ListFormat(getLocale(), {
                                type: "conjunction",
                              }).format(
                                row.nominated.map((party) => party.label),
                              )
                            : (row.short ??
                              (row.isIndependent === true
                                ? m.seats_independents()
                                : null)),
                      ]
                    : []),
                  row.votes === 0 && (row.seats ?? 0) > 0
                    ? null
                    : count(row.votes),
                  row.votes === 0 && (row.seats ?? 0) > 0
                    ? null
                    : percent(row.share),
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
        ...(isChanged && race.isNotional === true && race.since != null
          ? [m.seats_change_notional({ year: race.since })]
          : []),
        ...(isChanged &&
        race.resized != null &&
        race.size != null &&
        race.since != null
          ? [
              m.seats_change_resized({
                year: race.since,
                before: count(race.resized),
                now: count(race.size),
              }),
            ]
          : []),
        ...race.results.flatMap((row) =>
          row.supported.length === 0
            ? []
            : [
                `${row.label}: ${m.supported_by({
                  parties: new Intl.ListFormat(getLocale(), {
                    type: "conjunction",
                  }).format(row.supported.map((party) => party.label)),
                })}.`,
              ],
        ),
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
        ...(race.covered != null && race.size != null
          ? [
              m.race_partial_seats({
                count: count(race.covered),
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
        ...(race.repeats ?? []).map((repeat) =>
          repeat.people.length === 1
            ? m.race_repeat_one({
                name: repeat.people[0]?.name ?? "",
                count: count(repeat.people[0]?.returns ?? 0),
                party: definite(repeat.label),
                seats: count(repeat.seats),
                members: count(repeat.members),
              })
            : m.race_repeat_many({
                names: new Intl.ListFormat(getLocale(), {
                  style: "long",
                  type: "conjunction",
                }).format(repeat.people.map((person) => person.name)),
                party: definite(repeat.label),
                seats: count(repeat.seats),
                members: count(repeat.members),
              }),
        ),
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
    ...(local == null || local.tiers.every((tier) => tier.kind === "county")
      ? []
      : [
          `## ${m.local_section({ place: local.name })}`,
          m.local_lede({
            governments: m.local_governments({
              count: local.tiers
                .filter((tier) => tier.kind !== "county")
                .flatMap((tier) => tier.governments).length,
            }),
            contests: m.race_contests({
              count: local.tiers
                .filter((tier) => tier.kind !== "county")
                .flatMap((tier) => tier.governments)
                .flatMap((government) => government.contests).length,
            }),
          }),
          ...local.tiers
            .filter((tier) => tier.kind !== "county")
            .flatMap((tier) => [
              `### ${{ county: m.local_tier_county, municipal: m.local_tier_municipal, school: m.local_tier_school, special: m.local_tier_special }[tier.kind]()}`,
              ...tier.governments.flatMap((government) => [
                `#### ${link(government.name, government.path)}`,
                table(
                  [m.col_name(), m.col_leader(), m.col_share(), m.col_result()],
                  government.contests.map((contest) => {
                    const final =
                      contest.runoff?.isWon === true ? contest.runoff : null;
                    const shown = final ?? contest;
                    const round =
                      contest.runoff == null
                        ? ""
                        : (contest.runoff.isGeneral
                            ? m.wards_round_general
                            : m.wards_round_runoff)({
                            date: new Intl.DateTimeFormat(getLocale(), {
                              month: "long",
                              day: "numeric",
                              timeZone: "UTC",
                            }).format(
                              new Date(`${contest.runoff.date}T00:00:00Z`),
                            ),
                          });
                    return [
                      link(contest.name, contest.path),
                      shown.leader,
                      shown.share == null ? null : percent(shown.share),
                      contest.runoff == null || contest.share == null
                        ? null
                        : final == null
                          ? m.wards_pending({
                              share: percent(contest.share),
                              round,
                            })
                          : final.person != null &&
                              final.person === contest.person
                            ? m.wards_kept({
                                share: percent(contest.share),
                                round,
                                final: percent(final.share),
                              })
                            : m.wards_took({
                                round,
                                final: percent(final.share),
                                name: contest.leader ?? "",
                                share: percent(contest.share),
                              }),
                    ];
                  }),
                ),
              ]),
            ]),
        ]),
    ...(counties == null || counties.counties.length === 0
      ? []
      : [
          `## ${m.counties_title()}`,
          m.counties_lede({
            governments: m.local_governments({
              count: counties.counties.flatMap((county) => county.governments)
                .length,
            }),
            counties: m.local_counties({ count: counties.counties.length }),
            contests: m.race_contests({ count: counties.contests }),
          }),
          table(
            [m.col_county(), m.election_contests(), m.col_governments()],
            counties.counties.map((county) => {
              let held = 0;
              for (const government of county.governments) {
                held += government.contests;
              }
              return [
                link(county.name, county.path),
                count(held),
                county.governments
                  .map((government) => link(government.name, government.path))
                  .join(", "),
              ];
            }),
          ),
        ]),
    ...(alongside.length === 0
      ? []
      : [
          `## ${m.alongside_title()}`,
          m.alongside_lede(),
          ...alongside.flatMap((group) => [
            `### ${link(group.name, group.path)}`,
            table(
              [m.col_name(), m.col_leader(), m.col_share()],
              group.contests.map((contest) => [
                link(contest.name, contest.path),
                contest.leader,
                contest.share == null ? null : percent(contest.share),
              ]),
            ),
          ]),
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
                  : row.from != null && label(row.to) !== label(row.from)
                    ? m.byes_change_other()
                    : m.byes_hold({ party: label(row.to) }),
              row.majority == null ? null : count(row.majority),
              row.member == null
                ? row.cause
                : (reasons[row.reason ?? ""]?.({
                    name: link(row.member.name, row.member.path),
                  }) ??
                  (row.cause == null
                    ? link(row.member.name, row.member.path)
                    : `${link(row.member.name, row.member.path)}: ${row.cause}`)),
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
  rounds: Extract<Data, { kind: "contest" }>["rounds"],
  link: Link,
) {
  const groups: Record<string, (place: string) => string> = {
    owner: () => "",
    governor: m.cycle_governor,
    legislature: m.cycle_legislature,
    regional_office: m.cycle_regional_office,
    national_measure: m.cycle_national_measure,
    regional_measure: m.cycle_regional_measure,
    local: (place) => m.cycle_local({ place }),
    local_measure: (place) => m.cycle_local_measure({ place }),
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
  const [gain, hold, allied, other] =
    handover?.lead === "margin"
      ? [
          m.change_gain_margin,
          m.change_hold_margin,
          m.change_allied_margin,
          m.change_other_margin,
        ]
      : [m.change_gain, m.change_hold, m.change_allied, m.change_other];
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
                    : handover.isOther
                      ? m.change_other_bare({ party, from })
                      : m.change_hold_bare({ party })
                  : handover.isGain
                    ? gain({ party, from, majority })
                    : handover.isOther
                      ? other({ party, from, majority })
                      : hold({ party, majority })
          }${handover.basis === "same" ? "" : "*"}`,
          ...(handover.basis === "same"
            ? []
            : [
                handover.basis === "notional"
                  ? `${String.raw`\*`} ${m.change_note_notional({ year: String(handover.since) })}`
                  : `${String.raw`\*`} ${(handover.basis === "area" ? m.change_note_area : m.change_note_name)({ year: String(handover.since) })} ${link(handover.prior.name, handover.prior.path)}.`,
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
    [verdict(contest), comeback(contest, rounds)]
      .filter((part) => part != null)
      .join(" "),
    ...contest.notes.map((note) => `> ${note.body}`),
    ...compared,
    ...held,
    ...(handover?.vacated == null ||
    contest.notes.some((note) => note.body.startsWith("By-election held"))
      ? []
      : [
          (VACANCIES[handover.vacated.via ?? ""] ?? m.byes_reason_none)({
            name: handover.vacated.name,
          }),
        ]),
    ...(rounds.length < 2
      ? []
      : [
          `## ${m.rounds_title()}`,
          ...rounds.flatMap((round, index) => {
            const label =
              round.kind === "primary"
                ? m.round_primary()
                : round.kind === "runoff"
                  ? m.round_runoff()
                  : round.kind === "indirect"
                    ? rounds.length === 2
                      ? index === 0
                        ? m.round_electoral()
                        : m.round_indirect()
                      : m.round_ballot({ round: index + 1 })
                    : round.kind === "general"
                      ? index === 0
                        ? m.round_first()
                        : m.round_general()
                      : m.round_number({ round: index + 1 });
            return [
              `### ${round.isCurrent ? label : link(label, round.path)}${round.date == null ? "" : `, ${date(round.date)}`}`,
              round.leaders
                .map(
                  (leader) =>
                    `- ${leader.label}, ${percent(leader.share)}${leader.outcome == null ? "" : ` (${outcome(leader.outcome)})`}`,
                )
                .join("\n"),
            ];
          }),
        ]),
    resultsMarkdown(contest, then),
    ...(additional != null || !lists?.isOpen
      ? []
      : [
          `## ${m.lists_open_title()}`,
          m.lists_open_summary({ seats: lists.seats }),
          ...lists.lists.flatMap((list) => [
            `### ${list.label}`,
            [
              list.votes == null
                ? null
                : m.lists_votes({ votes: count(list.votes) }),
              m.lists_seats({ count: list.seats }),
            ]
              .filter((part) => part != null)
              .join(", "),
            table(
              [m.col_candidate(), m.col_party(), m.col_votes(), m.col_result()],
              list.members.map((member) => [
                member.name,
                member.short ?? list.short ?? "",
                member.votes == null ? "" : count(member.votes),
                member.fate === "elected"
                  ? m.lists_elected()
                  : member.fate === "withdrew"
                    ? m.lists_withdrew()
                    : member.fate === "disqualified"
                      ? m.lists_disqualified()
                      : "",
              ]),
            ),
          ]),
        ]),
    ...(additional != null ||
    lists == null ||
    lists.isRanked ||
    lists.isOpen ||
    lists.seats === 0
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
    ...(lists == null || !lists.isRanked || lists.lists.length === 0
      ? []
      : [
          `## ${m.lists_title()}`,
          m.lists_summary({ seats: lists.seats }),
          ...lists.lists.flatMap((list) => {
            let cut = 0;
            for (const [index, member] of list.members.entries()) {
              if (member.fate === "elected") {
                cut = index + 1;
              }
            }
            const next =
              list.seats > 0
                ? list.members.findIndex(
                    (member, index) =>
                      index >= cut &&
                      member.fate == null &&
                      member.elsewhere == null,
                  )
                : -1;
            const fates: Record<string, () => string> = {
              elected: m.lists_elected,
              withdrew: m.lists_withdrew,
              disqualified: m.lists_disqualified,
            };
            const lines = list.members.map((member, index) => {
              const status =
                fates[member.fate ?? ""]?.() ??
                (member.elsewhere == null
                  ? index === next
                    ? m.lists_next()
                    : null
                  : m.lists_elsewhere_detail({
                      contest: member.elsewhere.name,
                    }));
              return `${member.position}. ${member.name}${status == null ? "" : ` (${status})`}`;
            });
            return [
              `### ${list.label}`,
              [
                list.votes == null
                  ? null
                  : m.lists_votes({ votes: count(list.votes) }),
                m.lists_seats({ count: list.seats }),
              ]
                .filter((part) => part != null)
                .join(", "),
              ...(cut === 0 || cut >= lines.length
                ? [lines.join("\n")]
                : [
                    lines.slice(0, cut).join("\n"),
                    m.lists_cut(),
                    lines.slice(cut).join("\n"),
                  ]),
            ];
          }),
        ]),
    ...(additional == null
      ? []
      : [
          `## ${additional.isTopUp ? m.ams_title() : m.pr_title()}`,
          `${(additional.isTopUp ? m.ams_lede : m.pr_lede)({ seats: count(additional.seats) })} ${
            additional.threshold == null
              ? ""
              : `${m.pr_threshold({ percent: String(additional.threshold) })} `
          }${
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
              ...(additional.isTopUp ? [m.col_constituency_seats()] : []),
              ...additional.picks.map((_, seat) =>
                m.ams_seat({ count: seat + 1 }),
              ),
              ...(additional.isTopUp ? [m.col_list_seats()] : []),
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
                ...(additional.isTopUp ? [count(party.constituencies)] : []),
                ...additional.picks.map((pick, seat) => {
                  const quotient = party.quotients[seat];
                  return quotient == null
                    ? null
                    : pick === party.key
                      ? `**${count(Math.round(quotient))}**`
                      : count(Math.round(quotient));
                }),
                ...(additional.isTopUp ? [count(party.declared)] : []),
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
          ...(additional.isTopUp
            ? [
                `### ${m.ams_constituency_members()}`,
                additional.constituencies
                  .map(
                    (seat) =>
                      `- ${link(seat.name, seat.path)}${seat.member == null ? "" : `: ${link(seat.member, seat.memberPath)}`}`,
                  )
                  .join("\n"),
              ]
            : []),
          ...(additional.members.length === 0
            ? []
            : [
                `### ${additional.isTopUp ? m.ams_list_members() : m.pr_members()}`,
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
    ...(lists == null || lists.replacements.length === 0
      ? []
      : [
          `## ${m.lists_changes()}`,
          lists.replacements
            .map((change) => {
              const reasons: Record<string, () => string> = {
                resigned: m.lists_reason_resigned,
                died: m.lists_reason_died,
                removed: m.lists_reason_removed,
                expelled: m.lists_reason_expelled,
                disqualified: m.lists_reason_disqualified,
                lost_seat: m.lists_reason_lost_seat,
              };
              const reason = reasons[change.replaced.via ?? ""]?.();
              const label = lists.lists.find(
                (list) => list.key === change.list,
              )?.label;
              const line =
                reason == null
                  ? m.lists_change({
                      successor: link(change.name, `id/${change.person}`),
                      predecessor: link(
                        change.replaced.name,
                        `id/${change.replaced.person}`,
                      ),
                      date: date(change.date),
                    })
                  : m.lists_change_reason({
                      successor: link(change.name, `id/${change.person}`),
                      predecessor: link(
                        change.replaced.name,
                        `id/${change.replaced.person}`,
                      ),
                      reason,
                      date: date(change.date),
                    });
              return `- ${label == null ? "" : `${label}: `}${line}`;
            })
            .join("\n"),
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
                `- ${link(group.label ?? groups[group.kind]?.(definite(cycle.day.place)) ?? "", group.path)}${group.count > 1 ? ` (${count(group.count)})` : ""}`,
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
      Math.abs(value) < 0.0005
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
          ...tally.rows
            .map((row) => ({
              row,
              elected: stv.steps.findIndex((step) =>
                step.elected.includes(row.key),
              ),
              out: stv.steps.findIndex(
                (step) =>
                  step.kind === "exclusion" && step.from.includes(row.key),
              ),
            }))
            .toSorted(
              (a, b) =>
                (a.elected === -1 ? Infinity : a.elected) -
                  (b.elected === -1 ? Infinity : b.elected) ||
                (b.out === -1 ? Infinity : b.out) -
                  (a.out === -1 ? Infinity : a.out) ||
                (b.row.votes.at(-1) ?? 0) - (a.row.votes.at(-1) ?? 0),
            )
            .map(({ row, elected, out }) => [
              row.label,
              elected !== -1
                ? row.votes.every((value) => (value ?? 0) < stv.quota)
                  ? m.stv_elected_short()
                  : m.stv_elected({ count: elected + 1 })
                : out === -1
                  ? m.stv_unelected()
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
            ]),
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
        ...(tally.exhausted.every((spent) => spent == null)
          ? []
          : [
              [
                m.count_exhausted(),
                ...tally.exhausted.map((spent) =>
                  spent == null ? null : count(spent),
                ),
              ],
            ]),
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
  const hasSeats =
    won > 1 &&
    contest.rows.some(
      (row) => (row.current?.seats ?? 0) > 0 && row.current?.person == null,
    );
  const hasParty = contest.rows.some(
    (row) => row.current?.person != null && row.short != null,
  );
  const joined = new Intl.ListFormat(getLocale(), { type: "conjunction" });
  const parties = new Map(
    contest.rows.map((row) => [
      row.key,
      row.nominated.length > 0
        ? joined.format(row.nominated.map((party) => party.label))
        : (row.short ??
          ((row.current ?? row.earlier)?.isIndependent === true
            ? m.seats_independents()
            : null)),
    ]),
  );
  const hasOutcome = contest.rows.some((row) => row.current?.outcome != null);
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
      ...(current == null || (current.votes === 0 && (current.seats ?? 0) > 0)
        ? [null, null]
        : [count(current.votes), percent(current.share)]),
      ...(hasSeats
        ? [current?.seats == null ? null : count(current.seats)]
        : []),
      ...(then == null
        ? []
        : [
            earlier && percent(earlier.share),
            delta == null ? null : points(delta),
          ]),
      ...(hasOutcome
        ? [
            current?.outcome === "lost"
              ? m.outcome_lost()
              : outcome(current?.outcome ?? null),
          ]
        : []),
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
          (contest.count?.stv != null && contest.count.round === 1) ||
          (contest.isStv && contest.count == null)
            ? m.stv_first()
            : m.col_votes(),
          m.col_share(),
          ...(hasSeats ? [m.col_seats()] : []),
          ...(then == null
            ? []
            : [m.col_share_in({ year: then }), m.col_change()]),
          ...(hasOutcome ? [m.col_result()] : []),
        ],
        results,
      )}\n\n${contest.ballot != null ? m.stat_votes_cast() : contest.count != null && contest.count.stv == null && contest.count.round > 1 ? m.stat_final_round() : m.stat_valid()}: ${count(contest.current.valid)}${contest.ballot == null ? "" : `. ${m.term_votes_cast({ count: String(contest.ballot) })}`}${
        contest.turnout == null || contest.turnout.registered === 0
          ? ""
          : `\n\n${m.stat_turnout()}: ${percent(contest.turnout.voters / contest.turnout.registered)} (${m.stat_turnout_count({ voters: count(contest.turnout.voters), registered: count(contest.turnout.registered) })})${contest.turnout.basis === "rejected" ? ` ${m.turnout_basis_rejected()}` : contest.turnout.basis === "valid" ? ` ${m.turnout_basis_valid()}` : ""}`
      }${contest.partial == null ? "" : `\n\n${m.race_partial_counties(contest.partial)}`}${contest.rows
        .flatMap((row) =>
          row.current == null || row.supported.length === 0
            ? []
            : [
                `\n\n${row.label}: ${m.supported_by({ parties: joined.format(row.supported.map((party) => party.label)) })}.`,
              ],
        )
        .join("")}${contest.rows
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
  const hasMargin = places.rows.some((row) => row.margin != null);
  const led = Map.groupBy(
    places.rows.filter((row) => row.leader != null && row.valid > 0),
    (row) => row.leader ?? -1,
  )
    .entries()
    .toArray()
    .toSorted((a, b) => b[1].length - a[1].length);
  return [
    `## ${m.places_title({ noun })}`,
    ...(led.length === 0 || places.rows.length < 3
      ? []
      : [
          led
            .map(([index, rows]) =>
              m.places_leads({
                label: places.legend[index]?.label ?? "",
                count: m.map_won({
                  count: count(rows.length),
                  noun,
                  nouns: plural(noun),
                }),
              }),
            )
            .join("; ") + ".",
        ]),
    table(
      [
        m.col_name(),
        m.col_leader(),
        m.col_share(),
        ...(hasMargin ? [m.col_margin()] : []),
        ...(isCompared ? [m.col_share_in({ year: then }), m.col_change()] : []),
        m.stat_valid(),
      ],
      places.rows.map((row) => {
        const before =
          row.leader == null ? null : (row.earlier?.[row.leader] ?? null);
        return [
          link(row.name, row.path),
          ...leading(row, places.legend),
          ...(hasMargin
            ? [
                row.margin == null
                  ? null
                  : places.legend.length === 1
                    ? m.map_unopposed()
                    : points(row.margin),
              ]
            : []),
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
  const sitting = (members?.members ?? [])
    .filter((member) => member.isVoting)
    .toSorted(
      (a, b) =>
        (a.place ?? "").localeCompare(b.place ?? "") ||
        (a.from ?? "").localeCompare(b.from ?? ""),
    );
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
          sitting.some((member) => member.from != null || member.to != null)
            ? table(
                [
                  m.col_name(),
                  m.col_party(),
                  m.chamber_joined(),
                  m.chamber_left(),
                ],
                sitting.map((member) => [
                  `${link(member.name, `id/${member.person}`)}${member.place == null ? "" : ` (${link(member.place, member.path)})`}`,
                  label(effective(member)),
                  date(member.from),
                  date(member.to),
                ]),
              )
            : table(
                [m.col_name(), m.col_party()],
                sitting.map((member) => [
                  `${link(member.name, `id/${member.person}`)}${member.place == null ? "" : ` (${link(member.place, member.path)})`}`,
                  label(effective(member)),
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
