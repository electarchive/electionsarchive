import { m } from "#lib/paraglide/messages.js";

const SITE = "https://electionsarchive.com";

const FONT =
  "'Sofia Sans Semi Condensed',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif";

const light = {
  paper: "#ffffff",
  ink: "#15171a",
  muted: "#5b616a",
  rule: "#dadde2",
  marker: "#ffe03a",
};

const dark = {
  paper: "#111315",
  ink: "#e8eaed",
  muted: "#9aa0a9",
  rule: "#2b2f34",
  marker: "#e8c547",
};

const escape = (text: string) =>
  text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

const darkRules = (scope: string) => `
${scope} .ea-paper { background-color: ${dark.paper} !important; }
${scope} .ea-ink { color: ${dark.ink} !important; }
${scope} .ea-muted { color: ${dark.muted} !important; }
${scope} .ea-rule { border-color: ${dark.rule} !important; }
${scope} .ea-button { background-color: ${dark.marker} !important; border-color: ${dark.ink} !important; }
${scope} .ea-button-label { color: ${light.ink} !important; }`;

const row = (padding: string, content: string, rule = false) =>
  `<tr>
<td${rule ? ' class="ea-rule"' : ""} style="padding:${padding};${rule ? `border-bottom:1px solid ${light.rule};` : ""}">
${content}
</td>
</tr>`;

const paragraph = (
  text: string,
  {
    size,
    line,
    muted,
    top,
  }: { size: number; line: number; muted: boolean; top: number },
) =>
  `<p class="${muted ? "ea-muted" : "ea-ink"}" style="margin:${top}px 0 0;font-family:${FONT};font-size:${size}px;line-height:${line}px;color:${muted ? light.muted : light.ink};">${text}</p>`;

const footnote = (text: string, top: number) =>
  paragraph(text, { size: 14, line: 20, muted: true, top });

const link = (url: string, text: string, muted: boolean) =>
  `<a class="${muted ? "ea-muted" : "ea-ink"}" href="${url}" target="_blank" style="color:${muted ? light.muted : light.ink};text-decoration:underline;">${text}</a>`;

const button = (url: string, label: string) => {
  const width = Math.round(label.length * 8.6 + 48);
  return `<table role="presentation" class="ea-button-table" cellspacing="0" cellpadding="0" border="0">
<tr>
<td class="ea-button" align="center" bgcolor="${light.marker}" style="border-radius:4px;border:1px solid ${light.ink};background-color:${light.marker};">
<!--[if mso]><v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" href="${url}" style="height:46px;v-text-anchor:middle;width:${width}px;" arcsize="9%" strokecolor="${light.ink}" strokeweight="1px" fillcolor="${light.marker}"><w:anchorlock/><center style="color:${light.ink};font-family:Arial,Helvetica,sans-serif;font-size:16px;font-weight:bold;">${escape(label)}</center></v:roundrect><![endif]-->
<!--[if !mso]><!--><a class="ea-button-link ea-button-label" href="${url}" target="_blank" style="display:inline-block;padding:13px 24px;font-family:${FONT};font-size:17px;line-height:20px;font-weight:600;color:${light.ink};text-decoration:none;border-radius:4px;mso-hide:all;">${escape(label)}</a><!--<![endif]-->
</td>
</tr>
</table>`;
};

const eventRow = (event: { title: string; meta: string; url: string }) =>
  row(
    "14px 0",
    `<a class="ea-ink" href="${escape(event.url)}" target="_blank" style="font-family:${FONT};font-size:17px;line-height:24px;font-weight:600;color:${light.ink};text-decoration:none;">${escape(event.title)}</a>
${footnote(escape(event.meta), 2)}`,
    true,
  );

export const renderEmail = (email: {
  subject: string;
  preheader: string;
  heading: string;
  body?: string;
  action?: { label: string; url: string; note?: string };
  events?: Array<{ title: string; meta: string; url: string }>;
  reason: string;
  unsubscribe?: string;
}) => {
  const wordmark = m.wordmark();
  const paragraphs = email.body?.split("\n\n") ?? [];
  const actionUrl = email.action == null ? null : escape(email.action.url);
  const sections: Array<string> = [
    row(
      "0 0 20px",
      `<a href="${SITE}" style="text-decoration:none;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0">
<tr>
<td style="padding:0 10px 0 0;vertical-align:middle;"><img src="${SITE}/icon-192.png" width="28" height="28" alt="" style="display:block;width:28px;height:28px;border-radius:4px;"></td>
<td class="ea-ink" style="vertical-align:middle;font-family:${FONT};font-size:19px;line-height:28px;font-weight:700;letter-spacing:-0.01em;color:${light.ink};">${escape(wordmark)}</td>
</tr>
</table>
</a>`,
      true,
    ),
    row(
      "32px 0 0",
      `<h1 class="ea-ink ea-heading" style="margin:0;font-family:${FONT};font-size:28px;line-height:34px;font-weight:700;letter-spacing:-0.015em;color:${light.ink};">${escape(email.heading)}</h1>`,
    ),
  ];
  if (paragraphs.length > 0) {
    sections.push(
      row(
        "16px 0 0",
        paragraphs
          .map((text, index) =>
            paragraph(escape(text), {
              size: 17,
              line: 26,
              muted: false,
              top: index === 0 ? 0 : 14,
            }),
          )
          .join("\n"),
      ),
    );
  }
  if (email.events != null && email.events.length > 0) {
    sections.push(
      row(
        "20px 0 0",
        `<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
<tr><td class="ea-rule" style="border-bottom:1px solid ${light.rule};font-size:0;line-height:0;">&nbsp;</td></tr>
${email.events.map((event) => eventRow(event)).join("\n")}
</table>`,
      ),
    );
  }
  if (email.action != null && actionUrl != null) {
    sections.push(row("28px 0 0", button(actionUrl, email.action.label)));
    if (email.action.note != null) {
      sections.push(
        row(
          "16px 0 0",
          paragraph(escape(email.action.note), {
            size: 15,
            line: 22,
            muted: true,
            top: 0,
          }),
        ),
      );
    }
    sections.push(
      row(
        "24px 0 0",
        `${footnote(escape(m.email_link_fallback()), 0)}
<p style="margin:6px 0 0;font-family:${FONT};font-size:14px;line-height:20px;word-break:break-all;">${link(actionUrl, actionUrl, false)}</p>`,
      ),
    );
  }
  sections.push(
    row("32px 0 0", "", true),
    row(
      "20px 0 0",
      [
        footnote(escape(email.reason), 0),
        ...(email.unsubscribe == null
          ? []
          : [
              footnote(
                link(
                  escape(email.unsubscribe),
                  escape(m.email_unsubscribe()),
                  true,
                ),
                12,
              ),
            ]),
        footnote(escape(m.email_footer_replies()), 12),
        footnote(
          `${escape(wordmark)} &middot; ${link(SITE, "electionsarchive.com", true)}`,
          12,
        ),
      ].join("\n"),
    ),
  );
  const html = `<!doctype html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="x-apple-disable-message-reformatting">
<meta name="format-detection" content="telephone=no, date=no, address=no, email=no, url=no">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>${escape(email.subject)}</title>
<!--[if mso]>
<noscript><xml><o:OfficeDocumentSettings><o:AllowPNG/><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript>
<style>body, table, td, h1, p, a { font-family: Arial, Helvetica, sans-serif !important; }</style>
<![endif]-->
<!--[if !mso]><!-->
<link href="https://fonts.googleapis.com/css2?family=Sofia+Sans+Semi+Condensed:wght@400;600;700&amp;display=swap" rel="stylesheet">
<!--<![endif]-->
<style>
:root { color-scheme: light dark; supported-color-schemes: light dark; }
body { margin: 0 !important; padding: 0 !important; width: 100% !important; -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }
table { border-collapse: collapse; mso-table-lspace: 0pt; mso-table-rspace: 0pt; }
img { border: 0; outline: none; text-decoration: none; -ms-interpolation-mode: bicubic; }
a[x-apple-data-detectors] { color: inherit !important; text-decoration: none !important; font: inherit !important; }
u + #body a { color: inherit; text-decoration: none; font: inherit; }
@media (max-width: 520px) {
  .ea-frame { padding: 28px 20px 32px !important; }
  .ea-heading { font-size: 24px !important; line-height: 30px !important; }
  .ea-button-link { display: block !important; text-align: center !important; }
  .ea-button-table { width: 100% !important; }
}
@media (prefers-color-scheme: dark) {${darkRules("")}
}${darkRules("[data-ogsc]")}${darkRules("[data-ogsb]")}
</style>
</head>
<body id="body" class="ea-paper" style="margin:0;padding:0;background-color:${light.paper};">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:${light.paper};opacity:0;">${escape(email.preheader)}${"&#8199;&#65279;&#847; ".repeat(60)}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" class="ea-paper" bgcolor="${light.paper}" style="background-color:${light.paper};">
<tr>
<td align="center" style="padding:0;">
<!--[if mso]><table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0" align="center"><tr><td><![endif]-->
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:600px;margin:0 auto;">
<tr>
<td class="ea-frame" style="padding:40px 32px 40px;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
${sections.join("\n")}
</table>
</td>
</tr>
</table>
<!--[if mso]></td></tr></table><![endif]-->
</td>
</tr>
</table>
</body>
</html>`;
  const text = [
    wordmark,
    email.heading,
    ...paragraphs,
    ...(email.events ?? []).map(
      (event) => `- ${event.title}\n  ${event.meta}\n  ${event.url}`,
    ),
    ...(email.action == null
      ? []
      : [
          `${email.action.label}: ${email.action.url}`,
          ...(email.action.note == null ? [] : [email.action.note]),
        ]),
    `--\n${email.reason}`,
    ...(email.unsubscribe == null
      ? []
      : [`${m.email_unsubscribe()}: ${email.unsubscribe}`]),
    m.email_footer_replies(),
    `${wordmark} · ${SITE}`,
  ].join("\n\n");
  return { subject: email.subject, html, text };
};
