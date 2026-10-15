import { defineConfig } from "drizzle-kit";
import { existsSync } from "node:fs";
import { env, loadEnvFile } from "node:process";

const envFile = new URL("../../.env", import.meta.url);
if (existsSync(envFile)) loadEnvFile(envFile);

export default defineConfig({
  dialect: "postgresql",
  schema: "./src/schema.ts",
  out: "./migrations",
  dbCredentials: { url: env.DATABASE_URL ?? "" },
  migrations: { schema: "public", table: "migrations" },
});
