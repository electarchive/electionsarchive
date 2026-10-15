import type { CommandParser } from "@redis/client";
import { REDIS_URL } from "$app/env/private";
import { createClient, defineScript } from "@redis/client";

/**
 * GCRA over any number of rules plus a penalty box, in one atomic round trip
 * timed by the Redis clock.
 *
 * KEYS: block, violations, strikes, then one theoretical-arrival-time key per rule.
 * ARGV: cost, violation threshold (0 disables penalties), violation window ms,
 * base block ms, max block ms, strike memory ms, then per rule the emission
 * interval ms and the window ms.
 *
 * Reply: allowed (0/1), retry after ms, blocked (0/1).
 */
const RATE_LIMIT_SCRIPT = `
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + tonumber(clock[2]) / 1000

local blocked_for = redis.call('PTTL', KEYS[1])
if blocked_for > 0 then
  return {0, blocked_for, 1}
end

local cost = tonumber(ARGV[1])
local tats = {}
local retry_after = 0

for i = 1, #KEYS - 3 do
  local interval = tonumber(ARGV[5 + i * 2])
  local window = tonumber(ARGV[6 + i * 2])
  local tat = math.max(tonumber(redis.call('GET', KEYS[3 + i])) or now, now)
  local next_tat = tat + interval * cost
  local allow_at = next_tat - window

  if allow_at > now then
    retry_after = math.max(retry_after, allow_at - now)
  else
    tats[i] = next_tat
  end
end

if retry_after > 0 then
  local threshold = tonumber(ARGV[2])
  if threshold > 0 then
    local violations = redis.call('INCR', KEYS[2])
    if violations == 1 then
      redis.call('PEXPIRE', KEYS[2], ARGV[3])
    end
    if violations >= threshold then
      local strikes = redis.call('INCR', KEYS[3])
      redis.call('PEXPIRE', KEYS[3], ARGV[6])
      local block = math.ceil(math.min(tonumber(ARGV[4]) * 2 ^ (strikes - 1), tonumber(ARGV[5])))
      redis.call('SET', KEYS[1], strikes, 'PX', block)
      redis.call('DEL', KEYS[2])
      return {0, block, 1}
    end
  end
  return {0, math.ceil(retry_after), 0}
end

for i, tat in pairs(tats) do
  redis.call('SET', KEYS[3 + i], tostring(tat), 'PX', math.ceil(tat - now))
end

return {1, 0, 0}
`;

const client = createClient({
  url: REDIS_URL,
  scripts: {
    rateLimit: defineScript({
      SCRIPT: RATE_LIMIT_SCRIPT,
      parseCommand(
        parser: CommandParser,
        keys: Array<string>,
        args: Array<string>,
      ) {
        parser.push(keys.length.toString());
        parser.pushKeys(keys);
        parser.push(...args);
      },
      transformReply: (reply: Array<number>) => reply,
    }),
  },
  commandOptions: { timeout: 1000 },
  socket: {
    connectTimeout: 5000,
    reconnectStrategy: (retries) =>
      Math.min(2 ** retries * 50, 5000) + Math.floor(Math.random() * 200),
  },
});
client.on("error", (error: unknown) => {
  console.error("Redis error:", error);
});

/**
 * The Redis client, connecting on first use so importing it at build time is
 * free. Commands issued while the connection is (re)established fail after
 * one second, so callers can fall back instead of hanging.
 */
export function redis() {
  if (!client.isOpen) {
    void (async () => {
      try {
        await client.connect();
      } catch (error) {
        console.error("Redis connection failed:", error);
      }
    })();
  }

  return client;
}
