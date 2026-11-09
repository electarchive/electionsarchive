import { defineEnvVars } from "@sveltejs/kit/env";
import * as v from "valibot";

const optional = v.optional(
  v.pipe(
    v.string(),
    v.transform((value) => (value === "" ? undefined : value)),
  ),
);

export const variables = defineEnvVars({
  DATABASE_URL: {
    schema: v.pipe(
      v.string(),
      v.url("Invalid URL"),
      v.regex(/^postgres(ql)?:\/\//, "Invalid PostgreSQL URL"),
    ),
    description: "PostgreSQL connection string",
  },
  AUTH_DATABASE_URL: {
    schema: optional,
    description:
      "PostgreSQL connection string for accounts and sessions, when they live apart from the archive",
  },
  PAGES_DATABASE_URL: {
    schema: optional,
    description:
      "PostgreSQL connection string for the persistent procedure cache, a database the nightly restore never replaces",
  },
  DB_POOL_MAX: {
    schema: v.optional(
      v.pipe(v.string(), v.digits(), v.transform(Number), v.minValue(3)),
    ),
    description:
      "Archive connections each web process may hold, split between pages and live results; deploys set it from the reader role's budget",
  },
  CACHE_SHADOW: {
    schema: (value) => value === "1" || value === "true",
    description:
      "Record what each cached result read and log, on each renewal, whether per-polity versions would have kept it correctly",
  },
  BETTER_AUTH_SECRET: {
    schema: v.pipe(v.string(), v.nonEmpty()),
    description: "Better Auth secret key",
  },
  BETTER_AUTH_URL: {
    schema: v.pipe(v.string(), v.url("Invalid URL")),
    description: "Better Auth URL",
  },
  GITHUB_CLIENT_ID: { schema: optional, description: "GitHub OAuth client ID" },
  GITHUB_CLIENT_SECRET: {
    schema: optional,
    description: "GitHub OAuth client secret",
  },
  GOOGLE_CLIENT_ID: { schema: optional, description: "Google OAuth client ID" },
  GOOGLE_CLIENT_SECRET: {
    schema: optional,
    description: "Google OAuth client secret",
  },
  CLOUDFLARE_ACCOUNT_ID: {
    schema: optional,
    description: "Cloudflare account that sends sign-in email",
  },
  CLOUDFLARE_EMAIL_TOKEN: {
    schema: optional,
    description:
      "Cloudflare API token with Email Sending Write permission on that account",
  },
  NOTIFY_TOKEN: {
    schema: optional,
    description:
      "Bearer token the notification timer sends to POST /internal/notifications",
  },
  PROJECTION_TOKEN: {
    schema: optional,
    description:
      "Bearer token the ops board sends to GET /internal/projections",
  },
  REDIS_URL: {
    schema: v.pipe(
      v.string(),
      v.url("Invalid URL"),
      v.regex(/^rediss?:\/\//, "Invalid Redis URL"),
    ),
    description: "Redis connection string",
  },
  BASE_URL: {
    public: true,
    schema: v.pipe(v.string(), v.url("Invalid URL")),
    description: "Base URL",
  },
  READ_ONLY: {
    public: true,
    schema: (value) => value === "1" || value === "true",
    description:
      "Serve the archive read-only: sign-in and usernames stay on, and every other write is refused",
  },
});
