import type { Nodes, Root } from "mdast";
import { fromMarkdown } from "mdast-util-from-markdown";
import { gfmAutolinkLiteralFromMarkdown } from "mdast-util-gfm-autolink-literal";
import { gfmAutolinkLiteral } from "micromark-extension-gfm-autolink-literal";

export const HANDLE = /^[a-z0-9](?:[a-z0-9-]{1,28}[a-z0-9])$/u;

export function parseMarkdown(body: string): Root {
  return fromMarkdown(body, {
    extensions: [
      gfmAutolinkLiteral(),
      {
        disable: {
          null: [
            "htmlFlow",
            "htmlText",
            "labelStartImage",
            "headingAtx",
            "setextUnderline",
            "definition",
          ],
        },
      },
    ],
    mdastExtensions: [gfmAutolinkLiteralFromMarkdown()],
  });
}

export type Piece =
  | { kind: "text"; value: string }
  | { kind: "mention"; value: string }
  | { kind: "reference"; value: string };

const TOKENS =
  /(?<![\w@/])@([a-z0-9](?:[a-z0-9-]{0,28}[a-z0-9])?)(?![\w-])|(?<![\w&/])#([1-9]\d{3,8})(?!\w)/giu;

export function piecesOf(text: string) {
  const pieces: Array<Piece> = [];
  let at = 0;
  for (const match of text.matchAll(TOKENS)) {
    const handle = match[1]?.toLowerCase();
    if (handle != null && !HANDLE.test(handle)) {
      continue;
    }
    if (match.index > at) {
      pieces.push({ kind: "text", value: text.slice(at, match.index) });
    }
    pieces.push(
      handle == null
        ? { kind: "reference", value: match[2] ?? "" }
        : { kind: "mention", value: handle },
    );
    at = match.index + match[0].length;
  }
  if (at < text.length) {
    pieces.push({ kind: "text", value: text.slice(at) });
  }
  return pieces;
}

export function mentionsOf(body: string) {
  const found = new Set<string>();
  const visit = (node: Nodes) => {
    if (node.type === "text") {
      for (const piece of piecesOf(node.value)) {
        if (piece.kind === "mention" && found.size < 10) {
          found.add(piece.value);
        }
      }
      return;
    }
    if (node.type !== "link" && "children" in node) {
      for (const child of node.children) {
        visit(child);
      }
    }
  };
  visit(parseMarkdown(body));
  return [...found];
}

export function safeHref(url: string) {
  try {
    const parsed = new URL(url, "https://electionsarchive.com");
    return ["http:", "https:", "mailto:"].includes(parsed.protocol)
      ? url
      : null;
  } catch {
    return null;
  }
}
