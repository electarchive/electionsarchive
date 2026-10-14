import { defineEnvVars } from "@sveltejs/kit/env";
import * as v from "valibot";

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
