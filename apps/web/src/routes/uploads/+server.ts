import { error } from "@sveltejs/kit";
import * as v from "valibot";
import { receive } from "#lib/server/uploads.js";
import type { RequestHandler } from "./$types";

const Filename = v.pipe(
  v.string(),
  v.normalize("NFC"),
  v.trim(),
  v.minLength(1),
  v.maxLength(255),
  v.regex(/^[^/\\\0]+$/),
);

export const POST: RequestHandler = async ({ request, url, locals }) => {
  if (locals.user == null) {
    error(401, "Sign in to upload a file.");
  }
  const filename = v.safeParse(Filename, url.searchParams.get("filename"));
  if (!filename.success) {
    error(400, "Add the file's name as the filename parameter.");
  }
  if (request.body == null) {
    error(400, "The file is empty.");
  }
  const length = request.headers.get("content-length");
  const row = await receive({
    userId: locals.user.id,
    filename: filename.output,
    publicRecord: url.searchParams.get("public_record") === "true",
    length: length == null ? null : Number(length),
    body: request.body,
  });
  return Response.json(row, { status: 201 });
};
