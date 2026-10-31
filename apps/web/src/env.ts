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
  RESEND_API_KEY: {
    schema: v.pipe(v.string(), v.nonEmpty()),
    description: "Resend API key",
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
