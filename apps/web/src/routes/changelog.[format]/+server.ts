import { m } from "#lib/paraglide/messages.js";
import { formatOf, FORMATS, twinResponse } from "#lib/server/contrib-twins.js";
import { changelog } from "#lib/server/site.js";
import { REPOSITORY } from "#lib/site.js";
import type { RequestHandler } from "./$types";

const escape = (value: string) =>
  value.replaceAll(/[\\`*_[\]<>|]/gu, (character) => `\\${character}`);

export const GET: RequestHandler = async ({ params, url }) => {
  const format = formatOf(params.format, FORMATS);
  const versions = await changelog();
  return twinResponse({
    format,
    origin: url.origin,
    page: "/changelog",
    title: m.changelog_title(),
    subtitle: m.changelog_lede(),
    markdown: () => {
      const lines = [`# ${m.changelog_title()}`, "", m.changelog_lede()];
      if (versions.length === 0) {
        lines.push("", m.changelog_empty());
      }
      for (const [index, version] of versions.entries()) {
        lines.push(
          "",
          `## ${version.version}`,
          "",
          `${version.date.slice(0, 10)}, [${version.commit.slice(0, 7)}](${REPOSITORY}/commit/${version.commit})`,
          "",
        );
        if (index === versions.length - 1 && version.commits.length === 0) {
          lines.push(m.changelog_first());
        }
        for (const commit of version.commits) {
          lines.push(
            `- ${escape(commit.subject)} ([${commit.sha.slice(0, 7)}](${REPOSITORY}/commit/${commit.sha}))`,
          );
        }
      }
      lines.push("", `[${m.changelog_history()}](${REPOSITORY}/commits/main)`);
      return `${lines.join("\n")}\n`;
    },
    json: () => ({ repository: REPOSITORY, versions }),
    entries: () =>
      versions.map((version) => ({
        id: `changelog:${version.version}`,
        title: m.footer_version({ version: version.version }),
        updated: version.date,
        author: null,
        path: `/changelog#v${version.version}`,
      })),
  });
};
