export const page = $state<{
  url: URL;
  params: Record<string, string>;
  data: Record<string, unknown>;
  state: Record<string, unknown>;
  route: { id: string | null };
  status: number;
  error: null;
  form: null;
}>({
  url: new URL(location.href),
  params: {},
  data: {},
  state: {},
  route: { id: null },
  status: 200,
  error: null,
  form: null,
});

export const navigating = { to: null, from: null, type: null, complete: null };

export const updated = { current: false, check: async () => false };
