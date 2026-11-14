export const origins = ["human", "machine", "official"] as const;

export type Prose = {
  body: string;
  lang: string;
  source_lang: string | null;
  origin: (typeof origins)[number] | null;
  stale: boolean;
  alternate: string | null;
};
