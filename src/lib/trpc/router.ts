import { exampleRouter } from "#lib/server/api/routers/example.js";
import { createTRPCRouter } from "#lib/server/api/trpc.js";

export const appRouter = createTRPCRouter({ example: exampleRouter });

export type AppRouter = typeof appRouter;
