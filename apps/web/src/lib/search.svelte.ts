export const finder = $state({ isOpen: false, value: "" });

export const editable = $state<{ entity: string | null }>({ entity: null });
