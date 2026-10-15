import { customType } from "drizzle-orm/pg-core";

export const daterange = customType<{
  data: { from: string | null; to: string | null };
  driverData: string;
}>({
  dataType: () => "daterange",
  fromDriver: (value) => {
    if (value === "empty") throw new Error("Empty daterange");
    const [from = "", to = ""] = value.slice(1, -1).split(",", 2);
    return { from: from === "" ? null : from, to: to === "" ? null : to };
  },
  toDriver: ({ from, to }) => `[${from ?? ""},${to ?? ""})`,
});
