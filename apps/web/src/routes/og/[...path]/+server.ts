import { Resvg } from "@resvg/resvg-js";
import { error, isHttpError, isRedirect } from "@sveltejs/kit";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import satori from "satori";
import { render as markup } from "svelte/server";
import AgentsScene from "#lib/components/agents-scene.svelte";
import { countryName, describe } from "#lib/downloads.js";
import { date, percent } from "#lib/format.js";
import { m } from "#lib/paraglide/messages.js";
import { side } from "#lib/party.js";
import { remember } from "#lib/server/cache.js";
import { latestCalver, release } from "#lib/server/downloads.js";
import { archive, home, party, person } from "#lib/server/pages.js";
import type { RequestHandler } from "./$types";

type Node = {
  type: string;
  props: { style?: Record<string, string | number>; children?: unknown };
};
type Bar = { key: string; label: string; color: string | null; share: number };

const INK = "#15171a";
const MUTED = "#5b616a";
const PAPER = "#ffffff";
const FILL = "#f2f3f5";
const RULE = "#dadde2";
const MARKER = "#ffe03a";
const NEUTRALS = ["#8a9099", "#a9aeb6", "#c6cad0"];
const MONOGRAM = `data:image/svg+xml;utf8,${encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="3" fill="${MARKER}"/><path fill="none" stroke="${INK}" stroke-width="2.4" stroke-linecap="square" d="M9.5 7 V25 M13.75 7 V25 M18 7 V25 M22.25 7 V25 M5.5 21 L26.5 11"/></svg>`,
)}`;
const ANSWERS: Record<string, string> = {
  "var(--yes)": "#2e7d4f",
  "var(--no)": "#c2410c",
};

const require = createRequire(import.meta.url);
const fonts = Promise.all(
  (
    [
      ["Piazzolla", "piazzolla", 400],
      ["Sofia", "sofia-sans-semi-condensed", 400],
      ["Sofia", "sofia-sans-semi-condensed", 600],
      ["Sofia", "sofia-sans-semi-condensed", 800],
    ] as const
  ).flatMap(([name, family, weight]) =>
    ["latin", "latin-ext"].map(async (subset) => ({
      name: `${name} ${subset}`,
      weight,
      style: "normal" as const,
      data: await readFile(
        require.resolve(
          `@fontsource/${family}/files/${family}-${subset}-${weight}-normal.woff`,
        ),
      ),
    })),
  ),
);

const h = (
  style: Record<string, string | number>,
  children?: unknown,
): Node => ({ type: "div", props: { style, children } });

const tint = (bar: Bar, index: number) => {
  const sided = side(bar.key);
  return (
    (bar.color?.startsWith("#") === true ? bar.color : null) ??
    (sided == null ? null : (ANSWERS[sided] ?? sided)) ??
    NEUTRALS[index % NEUTRALS.length] ??
    MUTED
  );
};

async function card(segments: Array<string>, search: URLSearchParams) {
  const [head = "", ...rest] = segments;
  if (segments.length === 0) {
    const { polities } = await home();
    let total = 0;
    for (const polity of polities) {
      total += polity.elections + polity.within;
    }
    return {
      kicker: "",
      title: m.home_intro(),
      line: m.home_elections({ count: total }),
      bars: [],
    };
  }
  if (head === "downloads" && rest.length <= 1) {
    const calver = rest[0] ?? (await latestCalver());
    const found = calver == null ? null : await release(calver);
    const countries = found?.summary?.countries ?? [];
    let total = 0;
    for (const country of countries) {
      total += country.results;
    }
    return {
      kicker: m.wordmark(),
      title:
        rest[0] == null
          ? m.downloads_title()
          : m.downloads_release_title({ version: rest[0] }),
      line: found == null ? m.downloads_lede() : describe(found),
      bars:
        total === 0
          ? []
          : countries
              .slice(0, 3)
              .map((country) => ({
                key: country.code,
                label: countryName(country.code, country.name),
                color: null,
                share: country.results / total,
              })),
    };
  }
  if (head === "people" && rest.length === 1) {
    const { person: found } = await person(rest[0] ?? "");
    return {
      kicker: found.party?.label ?? "",
      title: found.name,
      line: m.home_elections({ count: found.record.length }),
      bars: [],
      color: found.party?.color ?? null,
    };
  }
  if (head === "parties" && rest.length > 0) {
    const found = await party(rest.join("/"));
    return {
      kicker: found.party.polity?.name ?? "",
      title: found.party.name,
      line: m.home_elections({ count: found.record.count }),
      bars: [],
      color: found.party.color,
    };
  }
  const data = await archive(segments.join("/"), search, false);
  if (data.kind === "polity") {
    const years = data.polity.elections.flatMap((election) =>
      election.year == null ? [] : [election.year],
    );
    return {
      kicker: data.polity.breadcrumbs.map((crumb) => crumb.name).join(" / "),
      title: data.polity.name,
      line:
        years.length === 0
          ? m.polity_no_elections()
          : m.polity_lede({
              elections: m.home_elections({
                count: data.polity.elections.length,
              }),
              first: String(Math.min(...years)),
              last: String(Math.max(...years)),
            }),
      bars: data.polity.latest?.races[0]?.results ?? [],
    };
  }
  if (data.kind === "day") {
    return {
      kicker: data.local.breadcrumbs.map((crumb) => crumb.name).join(" / "),
      title: m.local_title({
        year: data.date.slice(0, 4),
        place: data.local.name,
      }),
      line: date(data.date),
      bars: [],
    };
  }
  if (data.kind === "election") {
    const { election } = await data.body;
    return {
      kicker: election.breadcrumbs.map((crumb) => crumb.name).join(" / "),
      title: election.name,
      line: date(election.date),
      bars: election.races[0]?.results ?? [],
    };
  }
  const { contest } = await data.body;
  return {
    kicker: [
      ...contest.breadcrumbs.map((crumb) => crumb.name),
      contest.election.name,
    ].join(" / "),
    title:
      contest.scope.noun == null
        ? contest.name
        : `${contest.scope.name}, ${contest.name}`,
    line: date(contest.election.date),
    bars: contest.rows.flatMap((row) =>
      row.current == null
        ? []
        : [
            {
              key: row.key,
              label:
                row.label.length > 24 ? (row.short ?? row.label) : row.label,
              color: row.color,
              share: row.current.share,
            },
          ],
    ),
  };
}

export const GET: RequestHandler = async ({ params, url }) => {
  if (!params.path.endsWith(".png")) {
    error(404);
  }
  if (params.path === "agents.png") {
    return image(await remember("og:agents:v2", agents));
  }
  const segments = params.path
    .slice(0, -4)
    .split("/")
    .filter((segment) => segment !== "" && segment !== "index");
  let found: Awaited<ReturnType<typeof card>>;
  try {
    found = await card(segments, url.searchParams);
  } catch (error_) {
    if (isRedirect(error_)) {
      return new Response(null, {
        status: error_.status,
        headers: { location: `/og${error_.location.split("?", 1)[0]}.png` },
      });
    }
    if (isHttpError(error_)) {
      error(error_.status);
    }
    throw error_;
  }
  return image(
    await remember(
      `og:v2:${createHash("sha256").update(JSON.stringify(found)).digest("base64url")}`,
      async () => render(found),
    ),
  );
};

const image = (png: Uint8Array<ArrayBuffer>) =>
  new Response(png, {
    headers: {
      "content-type": "image/png",
      "cache-control":
        "public, max-age=86400, stale-while-revalidate=604800, stale-if-error=604800",
    },
  });

const rasterize = (svg: string) =>
  new Uint8Array(
    new Resvg(svg, {
      fitTo: { mode: "width", value: 2400 },
      font: { loadSystemFonts: false },
    })
      .render()
      .asPng(),
  );

const brand = () =>
  h(
    {
      display: "flex",
      alignItems: "center",
      gap: 12,
      fontSize: 26,
      fontWeight: 800,
      letterSpacing: "-0.01em",
    },
    [
      { type: "img", props: { src: MONOGRAM, width: 34, height: 34 } },
      m.wordmark(),
    ],
  );

async function agents() {
  const width = 1100;
  const svg = await satori(
    h(
      {
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        width: 1200,
        height: 630,
        backgroundColor: PAPER,
        fontFamily: "Sofia latin, Sofia latin-ext",
        color: INK,
      },
      [
        h(
          {
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            width: 1088,
            paddingTop: 40,
          },
          [
            h({ display: "flex", flexDirection: "column" }, [
              h(
                {
                  display: "flex",
                  fontWeight: 800,
                  fontSize: 68,
                  lineHeight: 1,
                  letterSpacing: "-0.022em",
                },
                m.agents_title(),
              ),
              h(
                { display: "flex", marginTop: 12, fontSize: 30, color: MUTED },
                "electionsarchive.com/mcp",
              ),
            ]),
            brand(),
          ],
        ),
        {
          type: "img",
          props: {
            src: `data:image/svg+xml;utf8,${encodeURIComponent(markup(AgentsScene).body)}`,
            width,
            height: Math.round((width * 520) / 1200),
            style: { marginTop: 4 },
          },
        },
      ],
    ) as Parameters<typeof satori>[0],
    { width: 1200, height: 630, fonts: await fonts },
  );
  return rasterize(svg);
}

async function render(found: Awaited<ReturnType<typeof card>>) {
  const bars = found.bars.slice(0, 3);
  const svg = await satori(
    h(
      {
        display: "flex",
        flexDirection: "column",
        width: 1200,
        height: 630,
        boxSizing: "border-box",
        padding: "52px 72px 40px",
        backgroundColor: PAPER,
        fontFamily: "Sofia latin, Sofia latin-ext",
        color: INK,
      },
      [
        h({ display: "flex", fontSize: 26, color: MUTED }, found.kicker),
        h(
          {
            display: "flex",
            width: 1056,
            marginTop: 14,
            fontWeight: 800,
            fontSize: found.title.length > 60 ? 60 : 76,
            lineHeight: 0.98,
            letterSpacing: "-0.022em",
          },
          found.title,
        ),
        h(
          {
            display: "flex",
            width: 1056,
            marginTop: 18,
            fontFamily: "Piazzolla latin, Piazzolla latin-ext",
            fontSize: 30,
            color: MUTED,
          },
          found.line,
        ),
        h(
          {
            display: "flex",
            flexDirection: "column",
            width: 1056,
            flexGrow: 1,
            justifyContent: "flex-end",
          },
          bars.length === 0
            ? []
            : [
                h(
                  {
                    display: "flex",
                    alignItems: "flex-end",
                    gap: 56,
                    overflow: "hidden",
                  },
                  bars.map((bar, index) =>
                    h(
                      {
                        display: "flex",
                        flexDirection: "column",
                        flexShrink: 0,
                        maxWidth: 320,
                      },
                      [
                        h(
                          {
                            display: "flex",
                            fontWeight: 800,
                            fontSize: index === 0 ? 64 : 48,
                            lineHeight: 1,
                            letterSpacing: "-0.02em",
                            color: index === 0 ? INK : MUTED,
                          },
                          percent(bar.share),
                        ),
                        h(
                          {
                            display: "flex",
                            alignItems: "center",
                            gap: 10,
                            marginTop: 8,
                            fontSize: 24,
                            fontWeight: 600,
                          },
                          [
                            h({
                              width: 14,
                              height: 14,
                              flexShrink: 0,
                              borderRadius: 2,
                              backgroundColor: tint(bar, index),
                            }),
                            h(
                              {
                                display: "block",
                                overflow: "hidden",
                                whiteSpace: "nowrap",
                                textOverflow: "ellipsis",
                              },
                              bar.label,
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                ),
                h(
                  {
                    display: "flex",
                    marginTop: 20,
                    height: 16,
                    backgroundColor: FILL,
                  },
                  found.bars.map((bar, index) =>
                    h({
                      width: `${bar.share * 100}%`,
                      height: "100%",
                      borderRight: `2px solid ${PAPER}`,
                      backgroundColor: tint(bar, index),
                    }),
                  ),
                ),
              ],
        ),
        h(
          {
            display: "flex",
            alignItems: "center",
            width: 1056,
            gap: 12,
            marginTop: 28,
            paddingTop: 22,
            borderTop: `1px solid ${RULE}`,
            fontSize: 26,
            fontWeight: 800,
            letterSpacing: "-0.01em",
          },
          [
            { type: "img", props: { src: MONOGRAM, width: 34, height: 34 } },
            m.wordmark(),
          ],
        ),
      ],
    ) as Parameters<typeof satori>[0],
    { width: 1200, height: 630, fonts: await fonts },
  );
  return rasterize(svg);
}
