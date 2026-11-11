import { date, dates } from "#lib/format.js";
import { m } from "#lib/paraglide/messages.js";

const eastern = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York",
});

export function slateLine(
  held: string | null,
  closes: string | null,
  now: number,
  isUnopposed: boolean,
  isCounting = false,
) {
  const day = date(held);
  const ended =
    closes == null
      ? Date.parse(`${held}T12:00:00Z`) + 86_400_000
      : Date.parse(closes);
  if (held == null || now >= ended) {
    if (isCounting) {
      return m.slate_counting();
    }
    return (isUnopposed ? m.slate_unreported_unopposed : m.slate_unreported)({
      date: day,
    });
  }
  if (closes != null && eastern.format(now) >= held) {
    return m.slate_voting({
      date: day,
      time: dates({
        hour: "numeric",
        minute: "2-digit",
        timeZone: "America/New_York",
        timeZoneName: "short",
      }).format(ended),
    });
  }
  return m.slate_scheduled({ date: day });
}
