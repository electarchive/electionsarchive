export const provenance = $state<{
  isOpen: boolean;
  anchor: HTMLElement | null;
  value: {
    label: string;
    figure: string;
    measure: "votes" | "share" | "seats" | "margin" | "swing";
    path: string | null;
    key: string | null;
    area: number | null;
    place: string | null;
    math:
      | { votes: number; valid: number }
      | { first: number; second: number }
      | null;
  } | null;
}>({ isOpen: false, anchor: null, value: null });
