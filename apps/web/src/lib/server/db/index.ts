import { DATABASE_URL } from "$app/env/private";
import * as schema from "@ea/db/schema";
import { defineRelations } from "drizzle-orm";
import { drizzle } from "drizzle-orm/node-postgres";
import pg from "pg";
import { PROJECT } from "#lib/constants.js";

const pool = new pg.Pool({
  connectionString: DATABASE_URL,
  idleTimeoutMillis: 30_000,
  connectionTimeoutMillis: 10_000,
  statement_timeout: 300_000,
  application_name: PROJECT,
});
pool.on("error", (error) => {
  console.error("An idle database connection failed:", error);
});

export const db = drizzle({
  client: pool,
  relations: defineRelations(schema, (r) => ({
    users: { accounts: r.many.accounts(), sessions: r.many.sessions() },
    accounts: {
      user: r.one.users({ from: r.accounts.userId, to: r.users.id }),
    },
    sessions: {
      user: r.one.users({ from: r.sessions.userId, to: r.users.id }),
    },
  })),
});
