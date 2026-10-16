import { Resvg } from "@resvg/resvg-js";
import { error, isHttpError, isRedirect } from "@sveltejs/kit";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import satori from "satori";
import { date, percent } from "#lib/format.js";
import { m } from "#lib/paraglide/messages.js";
import { archive, home, party, person } from "#lib/server/pages.js";
import type { RequestHandler } from "./$types";

type Node = {
  type: string;
  props: { style?: Record<string, string | number>; children?: unknown };
};
type Bar = { key: string; label: string; color: string | null; share: number };

const INK = "#191816";
const MUTED = "#67635b";
const PAPER = "#fbfaf7";
const FRAME = "#f1efea";
const RULE = "#cfcac0";
const HIGHLIGHT = "#ffe03a";
const NEUTRALS = ["#8f8a81", "#aba69d", "#c8c3ba"];
const ANSWERS: Record<string, string> = {
  "answer:yes": "#2e7d4f",
  "answer:sì": "#2e7d4f",
  "answer:evet": "#2e7d4f",
  "answer:no": "#c2410c",
  "answer:hayır": "#c2410c",
};

const require = createRequire(import.meta.url);
const fonts = Promise.all(
  (
    [
      ["Piazzolla", "piazzolla", 600],
      ["Sofia", "sofia-sans-semi-condensed", 400],
      ["Sofia", "sofia-sans-semi-condensed", 600],
      ["Martian", "martian-mono", 400],
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
  type: string,
  style: Record<string, string | number>,
  children?: unknown,
): Node => ({ type, props: { style, children } });

const tint = (bar: Bar, index: number) =>
  bar.color ?? ANSWERS[bar.key] ?? NEUTRALS[index % NEUTRALS.length] ?? MUTED;

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
  const data = await archive(segments.join("/"), search);
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
  if (data.kind === "election") {
    return {
      kicker: data.election.breadcrumbs.map((crumb) => crumb.name).join(" / "),
      title: data.election.name,
      line: date(data.election.date),
      bars: data.election.races[0]?.results ?? [],
    };
  }
  const { contest } = data;
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
              label: row.label,
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
  const bars = found.bars.slice(0, 3);
  const accent = "color" in found ? found.color : null;
  const svg = await satori(
    h(
      "div",
      {
        display: "flex",
        width: "100%",
        height: "100%",
        padding: 28,
        backgroundColor: FRAME,
        fontFamily: "Sofia latin, Sofia latin-ext",
        color: INK,
      },
      h(
        "div",
        {
          display: "flex",
          flexDirection: "column",
          width: 1012,
          height: 474,
          padding: "44px 64px 40px",
          backgroundColor: PAPER,
          border: `2px solid ${RULE}`,
          borderTop: `14px solid ${accent ?? INK}`,
          borderRadius: 10,
        },
        [
          h(
            "div",
            { display: "flex", fontSize: 26, color: MUTED },
            found.kicker,
          ),
          h(
            "div",
            {
              display: "flex",
              marginTop: 18,
              fontFamily: "Piazzolla latin, Piazzolla latin-ext",
              fontWeight: 600,
              fontSize: found.title.length > 60 ? 54 : 68,
              lineHeight: 1.04,
              letterSpacing: "-0.01em",
            },
            found.title,
          ),
          h(
            "div",
            { display: "flex", marginTop: 16, fontSize: 30, color: MUTED },
            found.line,
          ),
          h(
            "div",
            {
              display: "flex",
              flexDirection: "column",
              flexGrow: 1,
              justifyContent: "flex-end",
            },
            bars.length === 0
              ? []
              : [
                  h(
                    "div",
                    {
                      display: "flex",
                      height: 22,
                      borderRadius: 3,
                      overflow: "hidden",
                      backgroundColor: FRAME,
                    },
                    found.bars.map((bar, index) =>
                      h("div", {
                        width: `${bar.share * 100}%`,
                        height: "100%",
                        backgroundColor: tint(bar, index),
                      }),
                    ),
                  ),
                  h(
                    "div",
                    {
                      display: "flex",
                      marginTop: 18,
                      gap: 28,
                      overflow: "hidden",
                    },
                    bars.map((bar, index) =>
                      h(
                        "div",
                        {
                          display: "flex",
                          alignItems: "center",
                          flexShrink: 0,
                          gap: 12,
                          fontSize: 28,
                          fontWeight: index === 0 ? 600 : 400,
                        },
                        [
                          h("div", {
                            width: 16,
                            height: 16,
                            borderRadius: 3,
                            backgroundColor: tint(bar, index),
                          }),
                          h(
                            "div",
                            {
                              display: "block",
                              maxWidth: 260,
                              overflow: "hidden",
                              whiteSpace: "nowrap",
                              textOverflow: "ellipsis",
                            },
                            bar.label,
                          ),
                          h(
                            "div",
                            {
                              display: "flex",
                              fontFamily: "Martian latin, Martian latin-ext",
                              fontSize: 24,
                            },
                            percent(bar.share),
                          ),
                        ],
                      ),
                    ),
                  ),
                ],
          ),
          h(
            "div",
            { display: "flex", marginTop: 30, fontSize: 24, fontWeight: 600 },
            h(
              "div",
              {
                display: "flex",
                padding: "2px 8px",
                backgroundColor: HIGHLIGHT,
              },
              m.wordmark(),
            ),
          ),
        ],
      ),
    ) as Parameters<typeof satori>[0],
    { width: 1200, height: 630, fonts: await fonts },
  );
  return new Response(new Uint8Array(new Resvg(svg).render().asPng()), {
    headers: {
      "content-type": "image/png",
      "cache-control": "public, max-age=86400",
    },
  });
};
