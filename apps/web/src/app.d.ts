/* eslint-disable sonarjs/no-commented-code */
/* eslint-disable ts/consistent-type-definitions */
// See https://svelte.dev/docs/kit/types#app.d.ts

import type { Session, User } from "#lib/auth-client.js";

// for information about these interfaces
declare global {
  interface Window {
    takeEarlyClick: () => { event: MouseEvent; target: HTMLElement } | null;
  }
  namespace App {
    // interface Error {}
    interface Locals {
      user: User | null;
      session: Session["session"] | null;
      view?: "contest" | "day" | "election" | "polity";
    }
    interface PageData {
      user: { name: string; email: string } | null;
      unread: number;
    }
    interface PageState {
      saved?: boolean;
    }
    // interface Platform {}
  }
}
