import { BASE_URL } from "$app/env/public";
import { adminClient } from "better-auth/client/plugins";
import { createAuthClient } from "better-auth/svelte";

export const authClient = createAuthClient({
  baseURL: BASE_URL,
  plugins: [adminClient()],
});

export type Session = typeof authClient.$Infer.Session;
export type User = (typeof authClient.$Infer.Session)["user"];
