import { PROJECT } from "#lib/constants.js";
import { createRateLimiter } from "#lib/server/ratelimit.js";

const limiter = createRateLimiter({
  prefix: `${PROJECT}:account-mail`,
  penalty: null,
});

export const allowAccountMail = async (name: string, key: string) =>
  (
    await limiter(
      { name, rules: [{ limit: 1, window: 60 }] },
      key.toLowerCase(),
    )
  ).isAllowed;
