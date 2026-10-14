import { defineConfig } from "drizzle-kit";
import { env } from "node:process";

export default defineConfig({
  dialect: "postgresql",
  schema: "./src/lib/server/db/schema.ts",
  out: "./drizzle",
  dbCredentials: { url: env.DATABASE_URL ?? "" },
  migrations: { schema: "public", table: "migrations" },
});
