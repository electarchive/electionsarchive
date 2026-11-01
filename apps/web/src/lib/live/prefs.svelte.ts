export const prefs = $state({ isBasemap: true, zone: "America/New_York" });

export function localize() {
  prefs.zone = new Intl.DateTimeFormat().resolvedOptions().timeZone;
}
