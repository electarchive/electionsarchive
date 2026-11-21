const MARK = "ea:visitor";

const DAY = 86_400_000;

let isSent = false;

export function visit() {
  if (isSent) {
    return;
  }
  isSent = true;
  try {
    if (Date.now() - Number(localStorage.getItem(MARK)) < DAY) {
      return;
    }
  } catch {}
  void fetch("/visitor", { method: "POST", priority: "low" })
    .then((response) => {
      if (response.ok) {
        localStorage.setItem(MARK, String(Date.now()));
      }
    })
    .catch(() => {});
}
