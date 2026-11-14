import { sql } from "drizzle-orm";
import { authDb } from "#lib/server/db/index.js";

export async function isAdmin(userId: string | undefined) {
  if (userId == null) {
    return false;
  }
  const { rows } = await authDb.execute<{ one: number }>(sql`
    select 1 as one from user_role where user_id = ${userId} and role = 'admin'
  `);
  return rows.length > 0;
}
