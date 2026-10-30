import { error } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import { createHash, randomUUID } from "node:crypto";
import { createWriteStream } from "node:fs";
import { mkdir, readFile, rename, rm } from "node:fs/promises";
import path from "node:path";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";
import * as v from "valibot";
import { roles } from "#lib/server/contrib.js";
import { db } from "#lib/server/db/index.js";

const DATA = path.resolve("../../data");
const INBOX = path.join(DATA, "inbox", "uploads");
const PAGES = "raw/contrib/pages/";
const LIMIT = 200 * 1024 ** 2;
const DAILY = 2 * 1024 ** 3;

const streaming = new Set<string>();

export const Filename = v.pipe(
  v.string(),
  v.normalize("NFC"),
  v.trim(),
  v.minLength(1),
  v.maxLength(255),
  v.regex(/^[^/\\\0]+$/),
);

export type Upload = {
  id: string;
  filename: string;
  media_type: string;
  bytes: number;
  sha256: string;
  pages: number | null;
  status: "received" | "ready" | "rejected";
  error: string | null;
  public_record: boolean;
  created_at: string;
};

const columns = sql.raw(
  "id, filename, media_type, bytes::float8 as bytes, sha256, pages, status, error, public_record, created_at::text as created_at",
);

class TooLarge extends Error {}

export async function receive({
  userId,
  keyId = null,
  filename,
  publicRecord,
  length,
  body,
}: {
  userId: string;
  keyId?: string | null;
  filename: string;
  publicRecord: boolean;
  length: number | null;
  body: ReadableStream<Uint8Array>;
}): Promise<Upload> {
  if (length != null && length > LIMIT) {
    error(413, "Files can be up to 200 MB.");
  }
  if (streaming.has(userId)) {
    error(
      429,
      "You already have an upload in progress. Wait for it to finish, then try again.",
    );
  }
  streaming.add(userId);
  try {
    const { rows } = await db.execute<{ busy: boolean; used: string }>(sql`
      select bool_or(status = 'received') as busy, coalesce(sum(bytes), 0)::text as used
      from upload
      where owner_user_id = ${userId} and created_at > now() - interval '1 day'
    `);
    const used = Number(rows[0]?.used ?? 0);
    if (rows[0]?.busy === true) {
      error(
        429,
        "You already have an upload in progress. Wait for it to finish, then try again.",
      );
    }
    if (used + (length ?? 0) > DAILY) {
      error(
        429,
        "This file would take you past the 2 GB daily upload limit. Try again later.",
      );
    }
    await mkdir(INBOX, { recursive: true });
    const partial = path.join(INBOX, `${randomUUID()}.part`);
    const hash = createHash("sha256");
    let bytes = 0;
    try {
      await pipeline(
        Readable.fromWeb(body as Parameters<typeof Readable.fromWeb>[0]),
        new Transform({
          transform(chunk: Uint8Array, _, done) {
            bytes += chunk.length;
            if (bytes > LIMIT) {
              done(new TooLarge());
              return;
            }
            hash.update(chunk);
            done(null, chunk);
          },
        }),
        createWriteStream(partial, { mode: 0o644 }),
      );
      if (bytes === 0) {
        error(400, "The file is empty.");
      }
      if (used + bytes > DAILY) {
        error(
          429,
          "This file would take you past the 2 GB daily upload limit. Try again later.",
        );
      }
      return await db.transaction(async (tx) => {
        const sha256 = hash.digest("hex");
        const { rows: inserted } = await tx.execute<Upload>(sql`
          insert into upload (owner_user_id, agent_key_id, sha256, media_type, bytes, filename, public_record)
          values (${userId}, ${keyId}, ${sha256}, 'application/octet-stream', ${bytes}, ${filename}, ${publicRecord})
          on conflict (owner_user_id, sha256) do update
            set status = 'received', error = null, created_at = now(),
                filename = excluded.filename, public_record = excluded.public_record
            where upload.status = 'rejected'
          returning ${columns}
        `);
        const row = inserted[0];
        if (row == null) {
          await rm(partial, { force: true });
          const { rows: found } = await tx.execute<Upload>(sql`
            select ${columns} from upload
            where owner_user_id = ${userId} and sha256 = ${sha256}
          `);
          const existing = found[0];
          if (existing == null) {
            error(409, "There is no upload with that ID.");
          }
          return existing;
        }
        await rename(partial, path.join(INBOX, row.id));
        await tx.execute(
          sql`select pg_notify('proposal_job', json_build_object('kind', 'upload', 'id', ${row.id}::uuid)::text)`,
        );
        return row;
      });
    } catch (error_) {
      await rm(partial, { force: true });
      if (error_ instanceof TooLarge) {
        error(413, "Files can be up to 200 MB.");
      }
      throw error_;
    }
  } finally {
    streaming.delete(userId);
  }
}

async function visible(id: string, userId: string | undefined) {
  const { rows } = await db.execute<
    Upload & { owner_user_id: string | null; cited: boolean }
  >(sql`
    select u.id, u.filename, u.media_type, u.bytes::float8 as bytes, u.sha256, u.pages, u.status,
           u.error, u.public_record, u.created_at::text as created_at, u.owner_user_id,
           u.public_record and exists (
             select 1 from proposal p
             join proposal_revision r on r.proposal_id = p.id
             cross join jsonb_each(coalesce(r.payload -> 'evidence', '{}'::jsonb)) e
             where p.state <> 'draft' and e.value ->> 'upload' = u.id::text
           ) as cited
    from upload u where u.id = ${id}
  `);
  const row = rows[0];
  if (row == null) {
    error(404, "There is no upload with that ID.");
  }
  if (row.cited) {
    return { row, open: true };
  }
  const allowed = await roles(userId);
  if (row.owner_user_id !== userId && !allowed.admin && !allowed.reviewer) {
    error(404, "There is no upload with that ID.");
  }
  return { row, open: false };
}

export async function upload(
  id: string,
  userId: string | undefined,
): Promise<Upload> {
  const { row } = await visible(id, userId);
  return {
    id: row.id,
    filename: row.filename,
    media_type: row.media_type,
    bytes: row.bytes,
    sha256: row.sha256,
    pages: row.pages,
    status: row.status,
    error: row.error,
    public_record: row.public_record,
    created_at: row.created_at,
  };
}

export async function page(id: string, n: number, userId: string | undefined) {
  const { open } = await visible(id, userId);
  const { rows } = await db.execute<{ image_key: string }>(sql`
    select p.image_key from upload_page p join upload u on u.id = p.upload_id
    where p.upload_id = ${id} and p.page_number = ${n} and u.status = 'ready'
  `);
  const key = rows[0]?.image_key;
  if (key == null || !key.startsWith(PAGES) || key.includes("..")) {
    error(404, "That upload has no such page.");
  }
  return { image: await readFile(path.join(DATA, key)), open };
}
