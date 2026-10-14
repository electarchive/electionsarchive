/* eslint-disable sonarjs/no-commented-code */
/* eslint-disable ts/consistent-type-definitions */
// See https://svelte.dev/docs/kit/types#app.d.ts

import type { Session, User } from "#lib/auth-client.js";

// for information about these interfaces
declare global {
  namespace App {
    // interface Error {}
    interface Locals {
      user: User | null;
      session: Session["session"] | null;
    }
    // interface PageData {}
    // interface PageState {}
    // interface Platform {}
  }
}
