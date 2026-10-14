type Replacement = [
  pattern: RegExp,
  replacement: string | ((shouldRetainLength: boolean) => string),
];

const WORD = String.raw`[\w\p{L}]`;
const NONWORD = String.raw`[^\w\p{L}]`;

const REPLACEMENTS: Array<Replacement> = [
  [
    /'''/gu,
    (shouldRetainLength: boolean) =>
      `\u{2034}${shouldRetainLength ? "\u{2063}\u{2063}" : ""}`,
  ],
  // beginning " before guillemet
  [new RegExp(String.raw`(${NONWORD}|^)"(\u{00AB})`, "gu"), "$1\u{201C}$2"],
  // beginning "
  [new RegExp(`(${NONWORD}|^)"(${WORD})`, "gu"), "$1\u{201C}$2"],
  // ending "
  [/(\u{201C}[^"]*)"([^"]*$|[^"\u{201C}]*\u{201C})/gu, "$1\u{201D}$2"],
  // remaining " at end of word
  [/(\D)"/gu, "$1\u{201D}"],
  // double prime as two single quotes
  [
    /''/gu,
    (shouldRetainLength: boolean) =>
      `\u{2033}${shouldRetainLength ? "\u{2063}" : ""}`,
  ],
  // beginning '
  [new RegExp(String.raw`(${NONWORD}|^)'(\S)`, "gu"), "$1\u{2018}$2"],
  // conjunction's possession
  [new RegExp(String.raw`(${WORD})'(\p{L})`, "gu"), "$1\u{2019}$2"],
  // abbrev. years like '93
  [
    /(\u{2018})(\d{2}[^\u{2019}]*)(\u{2018}(\D|$)|$|\u{2019}\p{L})/gu,
    "\u{2019}$2$3",
  ],
  // ending '
  [/((\u{2018}[^']*)|\p{L})'(\D|$)/gu, "$1\u{2019}$3"],
  // backwards apostrophe
  [
    new RegExp(
      String.raw`(?:\B|^)\u{2018}(?=(?:[^\u{2018}\u{2019}]*\u{2019}\b)*(?:[^\u{2018}\u{2019}]*\B${NONWORD}[\u{2018}\u{2019}]\b|[^\u{2018}\u{2019}]*$))`,
      "gu",
    ),
    "\u{2019}",
  ],
  // double prime
  [/"/gu, "\u{2033}"],
  // prime
  [/'/gu, "\u{2032}"],
];

export function replaceSmartQuotes(
  str: string,
  options?: { retainLength?: boolean },
) {
  const { retainLength = false } = options ?? {};

  for (const replace of REPLACEMENTS) {
    const replacement =
      typeof replace[1] === "function" ? replace[1](retainLength) : replace[1];
    // eslint-disable-next-line unicorn/no-unsafe-string-replacement -- replacements use intentional $1/$2 backreferences
    str = str.replace(replace[0], replacement);
  }
  return str;
}
