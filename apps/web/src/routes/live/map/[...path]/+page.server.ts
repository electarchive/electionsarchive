import type { LiveElection } from "@ea/analytics/live";
import { error } from "@sveltejs/kit";
import { placeScheme } from "#lib/live/format.js";
import { remember } from "#lib/server/cache.js";
import { liveBallot } from "#lib/server/live-ballot.js";
import {
  boxes,
  control,
  geography,
  house,
  states,
} from "#lib/server/live-board.js";
import { night } from "#lib/server/live-page.js";
import type { PageServerLoad } from "./$types";

const OUTLYING = new Set(["ak", "hi", "pr", "vi", "gu", "mp", "as"]);

export const load: PageServerLoad = async ({ params }) => {
  const segments = params.path.split("/");
  if (segments[0] !== "us" || segments.length < 2 || segments.length > 3) {
    error(404);
  }
  const found = await night(params.path);
  const { election } = found;
  if (election.slug !== params.path) {
    error(404);
  }
  const nationPath = `us/${segments.at(-1) ?? ""}`;
  const whole = nationPath === params.path ? found : await night(nationPath);
  const nationElection: LiveElection = whole.election;
  const [held, districts, nation] = await Promise.all([
    remember(`live:control:v3:${election.date}`, async () =>
      control(nationElection.races, election.date),
    ),
    remember(`live:house:v3:${election.date}`, async () =>
      house(nationElection.id, election.date),
    ),
    remember(`live:nation:v2:${election.date}`, async () =>
      states(election.date),
    ),
  ]);
  const lower = Object.entries(nation?.codes ?? {}).flatMap(([key, id]) =>
    OUTLYING.has(key) ? [] : [id],
  );
  const [frame, framed] = await Promise.all([
    remember(`live:lower:v1:${election.date}`, async () =>
      lower.length === 0
        ? null
        : ((await geography(lower, election.date, lower, false))?.frame ??
          null),
    ),
    remember(`live:boxes:v1:${election.date}`, async () =>
      boxes(
        [
          ...Object.values(nation?.codes ?? {}),
          ...Object.values(districts.codes),
        ],
        election.date,
      ),
    ),
  ]);
  if (segments.length === 2) {
    return {
      ...found,
      nationPath,
      nation,
      names: nation?.labels ?? {},
      control: held,
      districts:
        districts.map == null
          ? null
          : { map: districts.map, codes: districts.codes },
      geography: null,
      legislature: null,
      around: null,
      frame,
      boxes: framed,
    };
  }
  const areas = [
    ...new Set(
      election.races.flatMap((race) =>
        race.units.flatMap((unit) =>
          unit.scheme === placeScheme(race.state) && unit.areaId != null
            ? [unit.areaId]
            : [],
        ),
      ),
    ),
  ];
  const code = segments[1] ?? "";
  const own = nation?.codes[code];
  const [built, ballot] = await Promise.all([
    remember(`live:full:v3:${election.slug}`, async () =>
      geography(
        areas.length === 0 && own != null ? [own] : areas,
        election.date,
      ),
    ),
    liveBallot(params.path),
  ]);
  const home = framed[own ?? ""];
  const map = built == null || home == null ? built : { ...built, frame: home };
  const ids = new Set(election.races.map((race) => race.id));
  const mine = Object.fromEntries(
    Object.entries(districts.codes).filter(([key]) =>
      key.startsWith(`${code}-`),
    ),
  );
  const kept = new Set(Object.values(mine));
  const places = (districts.map?.places ?? []).filter((place) =>
    kept.has(place.areaId),
  );
  return {
    ...found,
    nationPath,
    nation: null,
    names: nation?.labels ?? {},
    control: {
      ...held,
      holders: Object.fromEntries(
        Object.entries(held.holders).filter(([id]) => ids.has(id)),
      ),
    },
    districts:
      districts.map == null || places.length < 2 || map == null
        ? null
        : {
            map: {
              ...districts.map,
              places,
              ground: [],
              frame: map.frame,
              outline: map.outline,
            },
            codes: mine,
          },
    geography: map,
    legislature:
      ballot == null || map == null
        ? null
        : {
            chambers: ballot.chambers.flatMap((chamber) => {
              const drawn = ballot.maps[chamber.id];
              return drawn == null
                ? []
                : [
                    {
                      ...chamber,
                      map: {
                        ...drawn,
                        ground: [],
                        frame: map.frame,
                        outline: map.outline,
                      },
                    },
                  ];
            }),
            contests: ballot.contests.filter(
              (row) => row.tier === "upper" || row.tier === "lower",
            ),
            parties: ballot.parties,
            families: ballot.families,
          },
    around:
      nation == null || OUTLYING.has(code)
        ? null
        : {
            tiles: nation.map.tiles,
            places: nation.map.places.filter(
              (place) => place.areaId !== nation.codes[code],
            ),
            codes: Object.fromEntries(
              Object.entries(nation.codes).filter(([key]) => key !== code),
            ),
          },
    frame,
    boxes: Object.fromEntries(
      [...Object.values(nation?.codes ?? {}), ...kept].flatMap((id) => {
        const box = framed[id];
        return box == null ? [] : [[id, box]];
      }),
    ),
  };
};
