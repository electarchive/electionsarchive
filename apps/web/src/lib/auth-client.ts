import { browser } from "$app/env";
import { BASE_URL } from "$app/env/public";
import { apiKeyClient } from "@better-auth/api-key/client";
import { adminClient } from "better-auth/client/plugins";
import { createAuthClient } from "better-auth/svelte";

export const authClient = createAuthClient({
  baseURL: browser ? location.origin : BASE_URL,
  plugins: [adminClient(), apiKeyClient()],
});

export type Session = typeof authClient.$Infer.Session;
export type User = (typeof authClient.$Infer.Session)["user"];
