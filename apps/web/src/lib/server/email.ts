const SITE = "https://electionsarchive.com";

const FONT =
  "'Sofia Sans Semi Condensed',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif";

const light = {
  paper: "#ffffff",
  ink: "#15171a",
  muted: "#5b616a",
  rule: "#dadde2",
};

const dark = {
  paper: "#111315",
  ink: "#e8eaed",
  muted: "#9aa0a9",
  rule: "#2b2f34",
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
${scope} .ea-button { background-color: ${dark.ink} !important; }
${scope} .ea-button-label { color: ${dark.paper} !important; }`;

export const renderEmail = (email: {
  subject: string;
  preheader: string;
  heading: string;
  body: string;
  action: string;
  url: string;
  note: string;
  fallback: string;
  reason: string;
  replies: string;
  wordmark: string;
}) => {
  const url = escape(email.url);
  const action = escape(email.action);
  const buttonWidth = Math.round(email.action.length * 8.6 + 48);
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
<tr>
<td class="ea-rule" style="padding:0 0 20px;border-bottom:1px solid ${light.rule};">
<a href="${SITE}" style="text-decoration:none;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0">
<tr>
<td style="padding:0 10px 0 0;vertical-align:middle;"><img src="${SITE}/icon-192.png" width="28" height="28" alt="" style="display:block;width:28px;height:28px;border-radius:4px;"></td>
<td class="ea-ink" style="vertical-align:middle;font-family:${FONT};font-size:19px;line-height:28px;font-weight:700;letter-spacing:-0.01em;color:${light.ink};">${escape(email.wordmark)}</td>
</tr>
</table>
</a>
</td>
</tr>
<tr>
<td style="padding:32px 0 0;">
<h1 class="ea-ink ea-heading" style="margin:0;font-family:${FONT};font-size:28px;line-height:34px;font-weight:700;letter-spacing:-0.015em;color:${light.ink};">${escape(email.heading)}</h1>
</td>
</tr>
<tr>
<td style="padding:16px 0 0;">
<p class="ea-ink" style="margin:0;font-family:${FONT};font-size:17px;line-height:26px;color:${light.ink};">${escape(email.body)}</p>
</td>
</tr>
<tr>
<td style="padding:28px 0 0;">
<table role="presentation" class="ea-button-table" cellspacing="0" cellpadding="0" border="0">
<tr>
<td class="ea-button" align="center" bgcolor="${light.ink}" style="border-radius:6px;background-color:${light.ink};">
<!--[if mso]><v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" href="${url}" style="height:46px;v-text-anchor:middle;width:${buttonWidth}px;" arcsize="13%" stroke="f" fillcolor="${light.ink}"><w:anchorlock/><center style="color:${light.paper};font-family:Arial,Helvetica,sans-serif;font-size:16px;font-weight:bold;">${action}</center></v:roundrect><![endif]-->
<!--[if !mso]><!--><a class="ea-button-link ea-button-label" href="${url}" target="_blank" style="display:inline-block;padding:13px 24px;font-family:${FONT};font-size:17px;line-height:20px;font-weight:600;color:${light.paper};text-decoration:none;border-radius:6px;mso-hide:all;">${action}</a><!--<![endif]-->
</td>
</tr>
</table>
</td>
</tr>
<tr>
<td style="padding:16px 0 0;">
<p class="ea-muted" style="margin:0;font-family:${FONT};font-size:15px;line-height:22px;color:${light.muted};">${escape(email.note)}</p>
</td>
</tr>
<tr>
<td style="padding:24px 0 0;">
<p class="ea-muted" style="margin:0;font-family:${FONT};font-size:14px;line-height:20px;color:${light.muted};">${escape(email.fallback)}</p>
<p style="margin:6px 0 0;font-family:${FONT};font-size:14px;line-height:20px;word-break:break-all;"><a class="ea-ink" href="${url}" target="_blank" style="color:${light.ink};text-decoration:underline;">${url}</a></p>
</td>
</tr>
<tr>
<td class="ea-rule" style="padding:32px 0 0;border-bottom:1px solid ${light.rule};font-size:0;line-height:0;">&nbsp;</td>
</tr>
<tr>
<td style="padding:20px 0 0;">
<p class="ea-muted" style="margin:0;font-family:${FONT};font-size:14px;line-height:20px;color:${light.muted};">${escape(email.reason)}</p>
<p class="ea-muted" style="margin:12px 0 0;font-family:${FONT};font-size:14px;line-height:20px;color:${light.muted};">${escape(email.replies)}</p>
<p class="ea-muted" style="margin:12px 0 0;font-family:${FONT};font-size:14px;line-height:20px;color:${light.muted};">${escape(email.wordmark)} &middot; <a class="ea-muted" href="${SITE}" target="_blank" style="color:${light.muted};text-decoration:underline;">electionsarchive.com</a></p>
</td>
</tr>
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
    email.wordmark,
    "",
    email.heading,
    "",
    email.body,
    "",
    `${email.action}: ${email.url}`,
    "",
    email.note,
    "",
    "--",
    email.reason,
    "",
    email.replies,
    "",
    `${email.wordmark} · ${SITE}`,
  ].join("\n");
  return { subject: email.subject, html, text };
};
