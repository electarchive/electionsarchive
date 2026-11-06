export class Settled<T> {
  value = $state.raw<T | null>(null);

  constructor(value: T | Promise<T>) {
    if (value instanceof Promise) {
      void value
        .then((found) => {
          this.value = found;
        })
        .catch(() => null);
    } else {
      this.value = value;
    }
  }
}
