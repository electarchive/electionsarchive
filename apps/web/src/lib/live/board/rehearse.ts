import type {
  LiveElection,
  LiveEstimate,
  LiveRace,
  LiveUnitCount,
} from "@ea/analytics/live";
import { phi } from "#lib/live/calibrate.js";
import { hash, stream } from "#lib/live/random.js";

const STEP = 10_000;
const HOUR = 150_000;
const SPAN = 420_000;

function side(race: LiveRace) {
  const find = (prefix: string) =>
    race.options.findIndex(
      (option) => option.party?.name.startsWith(prefix) === true,
    );
  const blue = find("Democrat");
  const red = find("Republican");
  return {
    blue: blue === -1 ? 0 : blue,
    red: red === -1 ? Math.min(1, race.options.length - 1) : red,
  };
}

function lean(race: LiveRace) {
  const state = stream(hash(`lean:${race.state}`));
  const own = stream(hash(`lean:${race.id}`));
  return (
    (state.next() - 0.5) * 0.5 +
    own.normal() * (race.office === "house" ? 0.14 : 0.05)
  );
}

function progress(race: LiveRace, close: number, now: number, unit = "") {
  const delay = stream(hash(`delay:${race.id}:${unit}`)).next() * 60_000;
  const value = (now - close - delay) / SPAN;
  return Math.max(0, Math.min(1, value)) ** 0.85;
}

function shares(race: LiveRace, margin: number) {
  const { blue, red } = side(race);
  const rest = race.options.length > 2 ? 0.035 : 0;
  return race.options.map((_, index) => {
    if (index === blue) {
      return (1 - rest) * (0.5 + margin / 2);
    }
    if (index === red) {
      return (1 - rest) * (0.5 - margin / 2);
    }
    return rest / Math.max(1, race.options.length - 2);
  });
}

function compress(election: LiveElection, started: number) {
  const times = election.pollClose.map((entry) => Date.parse(entry.at));
  const first = times.length === 0 ? 0 : Math.min(...times);
  return new Map(
    election.pollClose.map((entry) => [
      entry.state,
      Math.round(
        started + 20_000 + ((Date.parse(entry.at) - first) / 3_600_000) * HOUR,
      ),
    ]),
  );
}

function closer(election: LiveElection, started: number) {
  const at = compress(election, started);
  return (race: LiveRace) => at.get(race.state) ?? started + 20_000;
}

function drift(race: LiveRace, share: number) {
  const early = stream(hash(`early:${race.id}`)).normal() * 0.09;
  return early * (1 - share) ** 2;
}

export function rehearse(
  election: LiveElection,
  started: number,
  now: number,
): LiveElection {
  const closeOf = closer(election, started);
  const quantum = Math.floor(now / STEP) * STEP;
  const races = election.races.map((race): LiveRace => {
    const close = closeOf(race);
    const base = lean(race);
    const size =
      (race.office === "house" ? 280_000 : 1_400_000) *
      (0.6 + stream(hash(`size:${race.state}`)).next() * 2.4);
    const frames = [];
    for (let at = close; at <= quantum; at += STEP) {
      const share = progress(race, close, at);
      if (share <= 0) {
        continue;
      }
      const split = shares(race, base + drift(race, share));
      frames.push({
        at: new Date(at).toISOString(),
        totals: split.map((value) => Math.round(value * size * share)),
        reporting: Math.round(share * 100),
        expected: 100,
      });
    }
    const counts: Array<LiveUnitCount> = [];
    if (race.units.length > 0 && quantum > close) {
      const weights = race.units.map(
        (unit) =>
          race.baseline.find((entry) => entry.unitId === unit.id)?.turnout ??
          1 + stream(hash(`unit:${unit.id}`)).next() * 40,
      );
      let weight = 0;
      for (const value of weights) {
        weight += value;
      }
      for (const [index, unit] of race.units.entries()) {
        const share = progress(race, close, quantum, unit.id);
        if (share <= 0) {
          continue;
        }
        const local = base + stream(hash(`tilt:${unit.id}`)).normal() * 0.22;
        const votes = ((weights[index] ?? 1) / weight) * size * share;
        counts.push({
          unitId: unit.id,
          at: new Date(quantum).toISOString(),
          votes: shares(race, Math.max(-0.9, Math.min(0.9, local))).map(
            (value) => Math.round(value * votes),
          ),
          reporting: Math.round(share * 100),
          expected: 100,
        });
      }
    }
    const last = frames.at(-1);
    const done = progress(race, close, quantum) >= 1;
    const { blue, red } = side(race);
    const winner = Math.abs(base) > 0.015 ? (base > 0 ? blue : red) : null;
    return {
      ...race,
      frames,
      counts,
      certified:
        done && last != null && winner != null
          ? { totals: last.totals, winners: [race.options[winner]?.id ?? ""] }
          : null,
    };
  });
  const newest = races
    .flatMap((race) => race.frames.map((frame) => frame.at))
    .toSorted((a, b) => a.localeCompare(b))
    .at(-1);
  const closes = compress(election, started);
  return {
    ...election,
    races,
    newest: newest ?? null,
    pollClose: election.pollClose.map((entry) => ({
      state: entry.state,
      at: new Date(closes.get(entry.state) ?? started).toISOString(),
    })),
  };
}

export function forecast(
  election: LiveElection,
  started: number,
  now: number,
): Record<string, LiveEstimate> {
  const closeOf = closer(election, started);
  const found: Record<string, LiveEstimate> = {};
  for (const race of election.races) {
    const share = progress(race, closeOf(race), now);
    const { blue, red } = side(race);
    const blueId = race.options[blue]?.id ?? null;
    const redId = race.options[red]?.id ?? null;
    const base = lean(race);
    const sd = 0.16 * (1 - share) + 0.006;
    const guess = base + stream(hash(`guess:${race.id}`)).normal() * sd * 0.6;
    const p = phi(guess / sd);
    const band = { low: guess - sd * 1.3, mid: guess, high: guess + sd * 1.3 };
    const isBlue = guess >= 0;
    const margin = isBlue
      ? band
      : { low: -band.high, mid: -band.mid, high: -band.low };
    const probability: Record<string, number> = {};
    if (blueId != null) {
      probability[blueId] = p;
    }
    if (redId != null) {
      probability[redId] = 1 - p;
    }
    found[race.id] = {
      status:
        share === 0 ? "waiting" : share < 0.04 ? "too-early" : "estimating",
      at: new Date(now).toISOString(),
      counted: share,
      expectedTotal: { low: 0.9, mid: 1, high: 1.1 },
      shareIn: {
        low: Math.max(0, share - 0.04),
        mid: share,
        high: Math.min(1, share + 0.04),
      },
      margin,
      leader: isBlue ? blueId : redId,
      runnerUp: isBlue ? redId : blueId,
      winProbability: probability,
      remaining: {
        votes: { low: 0.9 - share, mid: 1 - share, high: 1.1 - share },
        margin,
      },
      late: null,
      units: [],
    };
  }
  return found;
}
