import { VISITOR_SECRET } from "$app/env/private";
import { Buffer } from "node:buffer";
import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export const VISITOR = "ea_v";

export const KEPT = 31_536_000;

const SHAPE = /^(?<id>[\w-]{22})\.(?<issued>\d{1,12})\.(?<mac>[\w-]{43})$/u;

const FOUND = /(?:^|;\s*)ea_v=(?<value>[^;]*)/u;

const SKEW = 300;

function sign(body: string, secret: string) {
  return createHmac("sha256", secret).update(body).digest("base64url");
}

export function visitor(cookie: string | null) {
  if (VISITOR_SECRET == null || cookie?.includes(`${VISITOR}=`) !== true) {
    return { status: "absent" } as const;
  }
  const parts = SHAPE.exec(FOUND.exec(cookie)?.groups?.value?.trim() ?? "");
  const id = parts?.groups?.id;
  const issued = parts?.groups?.issued;
  const mac = parts?.groups?.mac;
  if (id == null || issued == null || mac == null) {
    return { status: "forged" } as const;
  }
  const expected = Buffer.from(sign(`${id}.${issued}`, VISITOR_SECRET));
  const given = Buffer.from(mac);
  if (given.length !== expected.length || !timingSafeEqual(given, expected)) {
    return { status: "forged" } as const;
  }
  const age = Date.now() / 1000 - Number(issued);
  return age < -SKEW || age > KEPT + SKEW
    ? ({ status: "absent" } as const)
    : ({ status: "valid", id } as const);
}

export function issue() {
  if (VISITOR_SECRET == null) {
    return null;
  }
  const body = `${randomBytes(16).toString("base64url")}.${Math.floor(Date.now() / 1000)}`;
  return `${body}.${sign(body, VISITOR_SECRET)}`;
}
