const SLICE = 100;

const nextTurn = async () =>
  new Promise<void>((resolve) => {
    setImmediate(resolve);
  });

export function together<Ask, Answer>(
  run: (asks: Array<Ask>, head: Ask) => Promise<(at: number) => Answer>,
  {
    shape = () => "",
    size = 1000,
    lanes = 2,
  }: { shape?: (ask: Ask) => string; size?: number; lanes?: number } = {},
) {
  type Entry = {
    ask: Ask;
    resolve: (answer: Answer) => void;
    reject: (error: unknown) => void;
  };
  const groups = new Map<string, Array<Entry>>();
  const ready: Array<Array<Entry>> = [];
  let active = 0;

  const settle = async (head: Entry, batch: Array<Entry>): Promise<void> => {
    try {
      const answer = await run(
        batch.map((entry) => entry.ask),
        head.ask,
      );
      for (const [at, entry] of batch.entries()) {
        entry.resolve(answer(at));
        if ((at + 1) % SLICE === 0) {
          await nextTurn();
        }
      }
    } catch (error) {
      if (batch.length === 1) {
        head.reject(error);
        return;
      }
      for (const half of [
        batch.slice(0, batch.length >> 1),
        batch.slice(batch.length >> 1),
      ]) {
        await settle(half[0] ?? head, half);
      }
    }
  };

  const pump = () => {
    while (active < lanes) {
      const batch = ready.shift();
      const head = batch?.[0];
      if (batch == null || head == null) {
        return;
      }
      active += 1;
      void settle(head, batch).finally(() => {
        active -= 1;
        pump();
      });
    }
  };

  return async (ask: Ask) =>
    new Promise<Answer>((resolve, reject) => {
      const key = shape(ask);
      const waiting = groups.get(key);
      if (waiting != null) {
        waiting.push({ ask, resolve, reject });
        return;
      }
      const group: Array<Entry> = [{ ask, resolve, reject }];
      groups.set(key, group);
      let seen = 0;
      const check = () => {
        if (group.length > seen && group.length < size) {
          seen = group.length;
          setImmediate(check);
          return;
        }
        groups.delete(key);
        while (group.length > 0) {
          ready.push(group.splice(0, size));
        }
        pump();
      };
      setImmediate(check);
    });
}
