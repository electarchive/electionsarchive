import { createTRPCRouter, publicProcedure } from "#lib/server/api/trpc.js";

export const exampleRouter = createTRPCRouter({
  greeting: publicProcedure.query(() => {
    return "Hello, world!";
  }),
});
