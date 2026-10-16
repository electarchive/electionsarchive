export const provenance = $state<{
  isOpen: boolean;
  value: {
    label: string;
    figure: string;
    measure: "votes" | "share" | "seats" | "margin" | "swing";
    path: string | null;
    key: string | null;
    area: number | null;
  } | null;
}>({ isOpen: false, value: null });
