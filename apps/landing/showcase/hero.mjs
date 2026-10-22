import { execFileSync } from "node:child_process";
import { readFileSync, realpathSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "../../..");
const web = path.join(root, "apps/web/node_modules");
const require = createRequire(
  realpathSync(path.join(web, "@sveltejs/kit/package.json")),
);
const { unflatten } = await import(require.resolve("devalue"));
const env = readFileSync(path.join(root, ".env"), "utf8");
const database = /^DATABASE_URL=(.*)$/m.exec(env)?.[1];
const snapshots = path.join(root, "apps/landing/public/showcase");
const page = path.join(root, "apps/landing/public/index.html");
const WIDTH = 1000;
const HEIGHT = 640;

function rows(file) {
  const view = JSON.parse(readFileSync(path.join(snapshots, file), "utf8"));
  const data = unflatten(view.nodes.at(-1));
  const places = data.contest.places;
  return places.rows.flatMap((row) => {
    const color = places.legend[row.leader]?.color;
    return typeof color === "string" &&
      Number.isSafeInteger(row.k) &&
      /^#[\da-f]{6}$/i.test(color)
      ? [{ k: row.k, color, at: row.at }]
      : [];
  });
}

function query(sql) {
  return execFileSync(
    "psql",
    [
      "-X",
      "-At",
      "-F",
      "\t",
      "-v",
      "ON_ERROR_STOP=1",
      "-c",
      `begin read only; ${sql}; commit;`,
      database,
    ],
    { encoding: "utf8", maxBuffer: 1 << 28 },
  )
    .split("\n")
    .filter((line) => line !== "" && line !== "BEGIN" && line !== "COMMIT");
}

function layer(list, detail, frame) {
  const values = list.map(({ k, color }) => `(${k}, '${color}')`).join(",");
  const shapes = `select i.k, i.color, st_transform(g.geom, 5070) as geom from (values ${values}) as i(k, color)
    join area a on a.k = i.k join area_geometry g on g.area_id = a.id and g.valid @> date '2024-11-05'`;
  const [box] = query(
    `select st_xmin(e), st_ymin(e), st_xmax(e), st_ymax(e) from (select st_extent(geom) as e from (${shapes}) s ${frame ?? ""}) b`,
  );
  const [xmin, ymin, xmax, ymax] = box.split("\t").map(Number);
  const scale = WIDTH / (xmax - xmin);
  const paths =
    query(`select color, st_assvg(st_affine(st_simplifypreservetopology(geom, ${detail / scale}), ${scale}, 0, 0, ${scale}, ${-xmin * scale}, ${-ymax * scale}), 1, 1)
    from (${shapes}) s where st_intersects(geom, st_makeenvelope(${xmin}, ${ymin}, ${xmax}, ${ymax}, 5070)) order by color`);
  const groups = new Map();
  for (const line of paths) {
    const [color, d] = line.split("\t", 2);
    groups.set(color, [...(groups.get(color) ?? []), d]);
  }
  return { xmin, ymin, xmax, ymax, scale, groups };
}

const nation = layer(
  rows("president-2024/views/0.json"),
  1.2,
  "where st_y(st_transform(st_centroid(geom), 4326)) between 24 and 50 and st_x(st_transform(st_centroid(geom), 4326)) > -126",
);
const state = layer(rows("philadelphia-precincts/views/1.json"), 1);
const city = layer(rows("philadelphia-precincts/views/0.json"), 1.4);

const place = (inner) => ({
  x: (inner.xmin - nation.xmin) * nation.scale,
  y: (nation.ymax - inner.ymax) * nation.scale,
  w: (inner.xmax - inner.xmin) * nation.scale,
  h: (inner.ymax - inner.ymin) * nation.scale,
});
const focus = (box, fill) => {
  const zoom = Math.min(WIDTH / box.w, HEIGHT / box.h) * fill;
  const cx = box.x + box.w / 2;
  const cy = box.y + box.h / 2;
  return `translate(${(WIDTH / 2 - cx * zoom).toFixed(2)}px, ${(HEIGHT / 2 - cy * zoom).toFixed(2)}px) scale(${zoom.toFixed(3)})`;
};
const nationHeight = (nation.ymax - nation.ymin) * nation.scale;
const home = `translate(0px, ${((HEIGHT - nationHeight) / 2).toFixed(2)}px) scale(1)`;
const draw = (target, name) =>
  [...target.groups]
    .map(
      ([color, list]) =>
        `<path class="${name}" fill="${color}" d="${list.join(" ")}"/>`,
    )
    .join("");
const at = (inner) => {
  const box = place(inner);
  return `translate(${box.x.toFixed(2)} ${box.y.toFixed(2)}) scale(${(box.w / WIDTH).toFixed(6)})`;
};

const svg = `<svg class="atlas" viewBox="0 0 ${WIDTH} ${HEIGHT}" role="img" aria-labelledby="atlas-title" style="--home: ${home}; --state: ${focus(place(state), 0.86)}; --city: ${focus(place(city), 0.9)}">
            <title id="atlas-title">The 2024 presidential election by state, then Pennsylvania by county, then Philadelphia by precinct, each shaded by its winner</title>
            <g class="stage">
              <g class="nation">${draw(nation, "n")}</g>
              <g class="state" transform="${at(state)}">${draw(state, "s")}</g>
              <g class="city" transform="${at(city)}">${draw(city, "c")}</g>
            </g>
          </svg>`;

const html = readFileSync(page, "utf8");
const start = html.indexOf("<!-- atlas -->");
const end = html.indexOf("<!-- /atlas -->");
if (start === -1 || end === -1) {
  throw new Error("index.html has no atlas markers");
}
writeFileSync(
  page,
  `${html.slice(0, start + 14)}\n          ${svg}\n          ${html.slice(end)}`,
);
console.warn(`atlas: ${(svg.length / 1024).toFixed(0)} KB`);
