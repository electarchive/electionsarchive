import { m } from "#lib/paraglide/messages.js";

export type Logo = {
  src: string;
  width: number;
  height: number;
  isLight: boolean;
  license: string;
  artist: string | null;
  source: string;
};

export function logoCredit(logo: Logo) {
  return logo.artist == null
    ? m.logo_credit_anonymous({ license: logo.license })
    : m.logo_credit({ artist: logo.artist, license: logo.license });
}
