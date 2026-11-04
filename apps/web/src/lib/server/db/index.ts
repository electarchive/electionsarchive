import { dev } from "$app/env";
import { AUTH_DATABASE_URL, DATABASE_URL } from "$app/env/private";
import * as schema from "@ea/db/schema";
import { defineRelations } from "drizzle-orm";
import { drizzle } from "drizzle-orm/node-postgres";
import pg from "pg";
import { PROJECT } from "#lib/constants.js";

function connect(connectionString: string, max: number, timeout: number) {
  const pool = new pg.Pool({
    connectionString,
    max,
    idleTimeoutMillis: 30_000,
    connectionTimeoutMillis: 30_000,
    statement_timeout: dev ? 300_000 : timeout,
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

export const db = connect(DATABASE_URL, 8, 60_000);

export const liveDb = connect(DATABASE_URL, 4, 30_000);

export const authDb =
  AUTH_DATABASE_URL == null ? db : connect(AUTH_DATABASE_URL, 2, 60_000);
