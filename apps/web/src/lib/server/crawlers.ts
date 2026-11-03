import crawlers from "./crawlers.json";

const AGENTS = crawlers.userAgents.map((name) => name.toLowerCase());

const EXCLUDED = crawlers.excluded.map((name) => name.toLowerCase());

export const TRAINING = [...crawlers.userAgents, ...crawlers.robotsTokens];

export function isTraining(userAgent: string | null) {
  const agent = userAgent?.toLowerCase();
  return (
    agent != null &&
    AGENTS.some((name) => agent.includes(name)) &&
    EXCLUDED.every((name) => !agent.includes(name))
  );
}
