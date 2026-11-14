import shapes from "./world.json";

type Shape = { d: string; at: Array<number>; area: number };

const WORLD: Record<string, Shape> = shapes.world.countries;
const EUROPE: Record<string, Shape> = shapes.europe.countries;
const MEMBERS = new Set(shapes.europe.members);

export function atlas(
  polities: Array<{
    kind: string;
    isoCode: string | null;
    name: string;
    path: string | null;
    first: number | null;
  }>,
) {
  const listed = new Map(
    polities.flatMap((polity) =>
      polity.kind === "sovereign_state" &&
      polity.isoCode != null &&
      polity.path != null &&
      polity.first != null
        ? [
            [
              polity.isoCode.toLowerCase(),
              { name: polity.name, path: polity.path, first: polity.first },
            ] as const,
          ]
        : [],
    ),
  );
  const union = polities.find(
    (polity) =>
      polity.kind === "supranational" &&
      polity.isoCode === "EU" &&
      polity.path != null &&
      polity.first != null,
  );
  const inset = new Set(
    Object.keys(EUROPE).filter((key) => {
      const at = WORLD[key]?.at;
      return listed.has(key) && at != null && within(at);
    }),
  );
  const split = (
    countries: Record<string, Shape>,
    tiny: number,
    isWorld: boolean,
  ) => {
    let rest = "";
    const covered = [];
    for (const [key, shape] of Object.entries(countries)) {
      const polity = listed.get(key);
      if (polity == null) {
        rest += shape.d;
        continue;
      }
      const [x = 0, y = 0] = shape.at;
      covered.push({
        key,
        ...polity,
        d: shape.d,
        x,
        y,
        isTiny: shape.area < tiny && !(isWorld && inset.has(key)),
        isAside: isWorld && inset.has(key),
        isMember: MEMBERS.has(key),
      });
    }
    return { rest, covered: covered.toSorted((a, b) => a.x - b.x) };
  };
  const [a, b, c, d] = shapes.world.corners;
  return {
    world: {
      width: shapes.world.width,
      height: shapes.world.height,
      frame: shapes.world.frame,
      corners: [a, b, c, d].map((corner) => ({
        x: corner?.[0] ?? 0,
        y: corner?.[1] ?? 0,
      })),
      ...split(WORLD, 6, true),
    },
    europe: {
      width: shapes.europe.width,
      height: shapes.europe.height,
      border: shapes.europe.border,
      ...split(EUROPE, 200, false),
    },
    union: union?.path == null ? null : { name: union.name, path: union.path },
    count: listed.size,
  };
}

function within([x = 0, y = 0]: Array<number>) {
  const xs = shapes.world.corners.map((corner) => corner[0] ?? 0);
  const ys = shapes.world.corners.map((corner) => corner[1] ?? 0);
  return (
    x >= Math.min(...xs) &&
    x <= Math.max(...xs) &&
    y >= Math.min(...ys) &&
    y <= Math.max(...ys)
  );
}
