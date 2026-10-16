import type { LayoutServerLoad } from "./$types";

export const load: LayoutServerLoad = ({ locals }) => ({
  user:
    locals.user == null
      ? null
      : { name: locals.user.name, email: locals.user.email },
});
