declare module "virtual:view-preloads" {
  export const preloads: Partial<
    Record<"contest" | "day" | "election" | "polity", Array<string>>
  >;
}
