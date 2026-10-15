import { defineConfig } from "drizzle-kit";
import config from "./drizzle.config.ts";

export default defineConfig({
  ...config,
  out: "./pull",
  schemaFilter: ["public"],
  extensionsFilters: ["postgis"],
  tablesFilter: [
    "*",
    "!result_value_p*",
    "!result_flow_p*",
    "!*_history",
    "!users",
    "!accounts",
    "!sessions",
    "!verifications",
    "!apikeys",
    "!user_role",
    "!saved_view",
    "!migrations",
  ],
});
