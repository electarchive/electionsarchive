export function shapeSkill(text: string, mode: "read" | "write") {
  const dropped = mode === "read" ? "write" : "read";
  return text
    .replaceAll(
      new RegExp(
        String.raw`^<!-- ${dropped} -->\n[\s\S]*?<!-- /${dropped} -->\n`,
        "gmu",
      ),
      "",
    )
    .replaceAll(
      new RegExp(
        String.raw`<!-- ${dropped} -->[\s\S]*?<!-- /${dropped} -->`,
        "gu",
      ),
      "",
    )
    .replaceAll(new RegExp(String.raw`^<!-- /?${mode} -->\n`, "gmu"), "")
    .replaceAll(new RegExp(`<!-- /?${mode} -->`, "gu"), "")
    .replaceAll(/\n{3,}/gu, "\n\n")
    .replace(/\n+$/u, "\n");
}
