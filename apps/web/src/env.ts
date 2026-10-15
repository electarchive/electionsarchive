import { defineEnvVars } from "@sveltejs/kit/env";
import * as v from "valibot";

const oauth = v.optional(
  v.pipe(
    v.string(),
    v.transform((value) => (value === "" ? undefined : value)),
  ),
);

export const variables = defineEnvVars({
  DATABASE_URL: {
    static: true,
    schema: v.pipe(
      v.string(),
      v.url("Invalid URL"),
      v.regex(/^postgres(ql)?:\/\//, "Invalid PostgreSQL URL"),
    ),
    description: "PostgreSQL connection string",
  },
  BETTER_AUTH_SECRET: {
    static: true,
    schema: v.pipe(v.string(), v.nonEmpty()),
    description: "Better Auth secret key",
  },
  BETTER_AUTH_URL: {
    static: true,
    schema: v.pipe(v.string(), v.url("Invalid URL")),
    description: "Better Auth URL",
  },
  GITHUB_CLIENT_ID: {
    static: true,
    schema: oauth,
    description: "GitHub OAuth client ID",
  },
  GITHUB_CLIENT_SECRET: {
    static: true,
    schema: oauth,
    description: "GitHub OAuth client secret",
  },
  GOOGLE_CLIENT_ID: {
    static: true,
    schema: oauth,
    description: "Google OAuth client ID",
  },
  GOOGLE_CLIENT_SECRET: {
    static: true,
    schema: oauth,
    description: "Google OAuth client secret",
  },
  RESEND_API_KEY: {
    static: true,
    schema: v.pipe(v.string(), v.nonEmpty()),
    description: "Resend API key",
  },
  REDIS_URL: {
    static: true,
    schema: v.pipe(
      v.string(),
      v.url("Invalid URL"),
      v.regex(/^rediss?:\/\//, "Invalid Redis URL"),
    ),
    description: "Redis connection string",
  },
  BASE_URL: {
    static: true,
    public: true,
    schema: v.pipe(v.string(), v.url("Invalid URL")),
    description: "Base URL",
  },
});
