import type { PageLoad } from "./$types";

export const load: PageLoad = async ({ data }) => {
  const [Switcher, Stream, Select, Slider, Seats, Roster, Council] =
    await Promise.all([
      import("./switcher.svelte"),
      import("./stream.svelte"),
      import("#lib/components/ui/select/index.js"),
      import("#lib/components/ui/slider/index.js"),
      import("#routes/[...path]/seats.svelte"),
      import("./roster.svelte"),
      import("./council.svelte"),
    ]);
  return {
    ...data,
    Switcher: Switcher.default,
    Stream: Stream.default,
    Select,
    Slider: Slider.Slider,
    Seats: Seats.default,
    Roster: Roster.default,
    Council: Council.default,
  };
};
