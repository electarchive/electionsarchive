import { openapi } from "@orpc/openapi";
import { ORPCError } from "@orpc/server";
import { Buffer } from "node:buffer";
import * as v from "valibot";
import { keyed, Number_, run, writer } from "#lib/server/rpc/keyed.js";
import { Filename, page, receive, upload } from "#lib/server/uploads.js";

const Uuid = v.pipe(v.string(), v.uuid());

export const uploads = {
  create: writer
    .meta(
      openapi({
        method: "POST",
        path: "/uploads",
        summary:
          "Upload a document of up to 20 MB as base64 to cite as evidence; it's ready once its pages are rendered, usually within a minute",
      }),
    )
    .input(
      v.object({
        filename: Filename,
        data: v.pipe(v.string(), v.base64(), v.maxLength(28_000_000)),
        public_record: v.optional(v.boolean(), false),
      }),
    )
    .handler(async ({ input, context: { owner } }) => {
      const bytes = Buffer.from(input.data, "base64");
      if (bytes.length === 0) {
        throw new ORPCError("BAD_REQUEST", { message: "The file is empty." });
      }
      return run(async () =>
        receive({
          userId: owner.userId,
          keyId: owner.keyId,
          filename: input.filename,
          publicRecord: input.public_record,
          length: bytes.length,
          body: new Blob([bytes]).stream(),
        }),
      );
    }),
  get: keyed
    .meta(
      openapi({
        method: "GET",
        path: "/uploads/{id}",
        summary:
          "One upload's status (received, ready, or rejected), page count, and error",
      }),
    )
    .input(v.object({ id: Uuid }))
    .handler(async ({ input, context: { owner } }) =>
      run(async () => upload(input.id, owner.userId)),
    ),
  page: keyed
    .meta(
      openapi({
        method: "GET",
        path: "/uploads/{id}/pages/{n}",
        summary:
          "One rendered page of a ready upload as a base64 PNG, to read before marking an evidence region",
      }),
    )
    .input(v.object({ id: Uuid, n: Number_ }))
    .handler(async ({ input, context: { owner } }) =>
      run(async () => {
        const { image } = await page(input.id, Number(input.n), owner.userId);
        return { media_type: "image/png", data: image.toString("base64") };
      }),
    ),
};
