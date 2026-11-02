import type { Nodes, Root } from "mdast";
import { fromMarkdown } from "mdast-util-from-markdown";
import { gfmAutolinkLiteralFromMarkdown } from "mdast-util-gfm-autolink-literal";
import { gfmAutolinkLiteral } from "micromark-extension-gfm-autolink-literal";
import { piecesOf } from "#lib/markdown.js";

function strip(node: Nodes) {
  delete node.position;
  if ("children" in node) {
    for (const child of node.children) {
      strip(child);
    }
  }
}

export function parseMarkdown(body: string): Root {
  const tree = fromMarkdown(body, {
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
  strip(tree);
  return tree;
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
