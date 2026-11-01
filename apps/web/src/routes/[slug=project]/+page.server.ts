import { project } from "#lib/server/project.js";
import type { PageServerLoad } from "./$types";

export const load: PageServerLoad = ({ params }) => project(params.slug);
