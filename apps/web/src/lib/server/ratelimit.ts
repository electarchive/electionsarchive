import { error } from "@sveltejs/kit";
import { PROJECT } from "#lib/constants.js";
import { redis } from "#lib/server/redis.js";

/**
 * A named set of rules that must all allow a request. Each rule allows at
 * most `limit` requests per `window` seconds, spread smoothly (GCRA) with
 * bursts of up to `limit`.
 */
type RateLimitPolicy = {
  name: string;
  rules: Array<{ limit: number; window: number }>;
};

const STRIKE_MEMORY_MS = 86_400_000;
const MAX_LOCAL_ENTRIES = 50_000;

function evictOldest(map: Map<string, number>) {
  if (map.size < MAX_LOCAL_ENTRIES) {
    return;
  }

  let toEvict = Math.ceil(MAX_LOCAL_ENTRIES / 10);
  for (const key of map.keys()) {
    map.delete(key);
    if (--toEvict === 0) {
      return;
    }
  }
}

/**
 * A distributed rate limiter on Redis. Each policy's rules are checked and
 * consumed atomically in one round trip. With a `penalty`, a client that
 * keeps hitting a limit (`violations` denials within `window` seconds) is
 * blocked from every policy for `block` seconds, doubling with each further
 * block within a day up to `maxBlock`; blocked clients are then turned away
 * locally without touching Redis. While Redis is unreachable, the same rules
 * are enforced per process from memory instead of failing open.
 */
export function createRateLimiter({
  prefix,
  penalty = { violations: 30, window: 300, block: 300, maxBlock: 86_400 },
}: {
  prefix: string;
  penalty?: {
    violations: number;
    window: number;
    block: number;
    maxBlock: number;
  } | null;
}) {
  const blockedUntil = new Map<string, number>();
  const fallbackTats = new Map<string, number>();

  function limitLocally(
    rules: Array<{ key: string; interval: number; window: number }>,
    cost: number,
  ) {
    const now = Date.now();
    const nextTats: Array<number> = [];
    let retryAfterMs = 0;

    for (const rule of rules) {
      const tat = Math.max(fallbackTats.get(rule.key) ?? now, now);
      const nextTat = tat + rule.interval * cost;
      retryAfterMs = Math.max(retryAfterMs, nextTat - rule.window - now);
      nextTats.push(nextTat);
    }

    if (retryAfterMs === 0) {
      evictOldest(fallbackTats);
      for (const [index, rule] of rules.entries()) {
        fallbackTats.delete(rule.key);
        fallbackTats.set(rule.key, nextTats[index] ?? now);
      }
    }

    return {
      isAllowed: retryAfterMs === 0,
      retryAfter: Math.ceil(retryAfterMs / 1000),
    };
  }

  return async (policy: RateLimitPolicy, identity: string, cost = 1) => {
    const base = `${prefix}:{${identity}}`;
    const now = Date.now();
    const until = blockedUntil.get(identity);

    if (until != null && until > now) {
      return { isAllowed: false, retryAfter: Math.ceil((until - now) / 1000) };
    }

    const rules = policy.rules.map((rule) => ({
      key: `${base}:${policy.name}:${rule.limit}/${rule.window}`,
      interval: (rule.window * 1000) / rule.limit,
      window: rule.window * 1000,
    }));

    const client = redis();
    if (!client.isReady) {
      return limitLocally(rules, cost);
    }

    let reply: Array<number>;
    try {
      reply = await client.rateLimit(
        [
          `${base}:block`,
          `${base}:violations`,
          `${base}:strikes`,
          ...rules.map((rule) => rule.key),
        ],
        [
          cost.toString(),
          (penalty?.violations ?? 0).toString(),
          ((penalty?.window ?? 0) * 1000).toString(),
          ((penalty?.block ?? 0) * 1000).toString(),
          ((penalty?.maxBlock ?? 0) * 1000).toString(),
          STRIKE_MEMORY_MS.toString(),
          ...rules.flatMap((rule) => [
            rule.interval.toString(),
            rule.window.toString(),
          ]),
        ],
      );
    } catch (error_) {
      console.error("Rate limiting fell back to memory:", error_);
      return limitLocally(rules, cost);
    }

    const [isAllowed, retryAfterMs = 0, isBlocked] = reply;

    if (isBlocked === 1) {
      blockedUntil.delete(identity);
      evictOldest(blockedUntil);
      blockedUntil.set(identity, now + retryAfterMs);
    }

    return {
      isAllowed: isAllowed === 1,
      retryAfter: Math.ceil(retryAfterMs / 1000),
    };
  };
}

const rateLimit = createRateLimiter({ prefix: `${PROJECT}:ratelimit` });

export async function checkRateLimit(
  policy: RateLimitPolicy,
  identity: string,
) {
  const { isAllowed } = await rateLimit(policy, identity);

  if (!isAllowed) {
    error(429, "Too many requests");
  }
}
