export const CLOSES: Record<string, number> = {
  ga: 1140,
  in: 1140,
  ky: 1140,
  sc: 1140,
  vt: 1140,
  va: 1140,
  nc: 1170,
  oh: 1170,
  wv: 1170,
  al: 1200,
  ct: 1200,
  de: 1200,
  dc: 1200,
  fl: 1200,
  il: 1200,
  me: 1200,
  md: 1200,
  ma: 1200,
  ms: 1200,
  mo: 1200,
  nh: 1200,
  nj: 1200,
  ok: 1200,
  pa: 1200,
  ri: 1200,
  tn: 1200,
  ar: 1230,
  az: 1260,
  co: 1260,
  ia: 1260,
  ks: 1260,
  la: 1260,
  mi: 1260,
  mn: 1260,
  ne: 1260,
  nm: 1260,
  ny: 1260,
  sd: 1260,
  tx: 1260,
  wi: 1260,
  wy: 1260,
  mt: 1320,
  nv: 1320,
  nd: 1320,
  ut: 1320,
  ca: 1380,
  id: 1380,
  or: 1380,
  wa: 1380,
  hi: 1440,
  ak: 1500,
};

export function eastern(date: string, minutes: number) {
  const noon = new Date(`${date}T12:00:00Z`);
  const shown = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "numeric",
    hourCycle: "h23",
  }).format(noon);
  const offset = 12 - Number(shown);
  return new Date(noon.getTime() + (minutes - 720 + offset * 60) * 60_000)
    .toISOString()
    .replace(/\.\d+Z$/u, "Z");
}

export function pollsClose(path: string | null, date: string | null) {
  const state = path?.match(
    /^us\/([a-z]{2})\/\d{4}-\d{2}-\d{2}-general\//u,
  )?.[1];
  const minutes = state == null ? undefined : CLOSES[state];
  return minutes == null || date == null ? null : eastern(date, minutes);
}
