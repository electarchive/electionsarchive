import { AUTH_DATABASE_URL, DATABASE_URL } from "$app/env/private";
import * as schema from "@ea/db/schema";
import { defineRelations } from "drizzle-orm";
import { drizzle } from "drizzle-orm/node-postgres";
import pg from "pg";
import { PROJECT } from "#lib/constants.js";

function connect(connectionString: string) {
  const pool = new pg.Pool({
    connectionString,
    idleTimeoutMillis: 30_000,
    connectionTimeoutMillis: 10_000,
    statement_timeout: 300_000,
    application_name: PROJECT,
  });
  pool.on("error", (error) => {
    console.error("An idle database connection failed:", error);
  });
  return drizzle({
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
}

export const db = connect(DATABASE_URL);

export const authDb =
  AUTH_DATABASE_URL == null ? db : connect(AUTH_DATABASE_URL);
