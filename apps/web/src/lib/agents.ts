import { m } from "#lib/paraglide/messages.js";

export const INSTALL = "npx skills add electarchive/electionsarchive";

const command = (...words: Array<string>) => words.join(" ");

export function connect(origin: string) {
  const mcp = `${origin}/mcp`;
  return {
    mcp,
    clients: [
      {
        value: "claude-code",
        label: "Claude Code",
        code: command(
          "claude",
          "mcp",
          "add",
          "--transport",
          "http",
          "electionsarchive",
          mcp,
        ),
        body: null,
      },
      {
        value: "codex",
        label: "Codex",
        code: command("codex", "mcp", "add", "electionsarchive", "--url", mcp),
        body: null,
      },
      {
        value: "gemini",
        label: "Gemini CLI",
        code: command(
          "gemini",
          "mcp",
          "add",
          "--transport",
          "http",
          "electionsarchive",
          mcp,
        ),
        body: null,
      },
      {
        value: "claude-app",
        label: m.agents_client_claude_app(),
        code: mcp,
        body: m.agents_client_claude_app_body(),
      },
      {
        value: "other",
        label: m.agents_client_other(),
        code: JSON.stringify(
          { mcpServers: { electionsarchive: { type: "http", url: mcp } } },
          null,
          2,
        ),
        body: null,
      },
    ],
    questions: [
      m.agents_ask_1(),
      m.agents_ask_2(),
      m.agents_ask_3(),
      m.agents_ask_4(),
      m.agents_ask_5(),
      m.agents_ask_6(),
    ],
  };
}
