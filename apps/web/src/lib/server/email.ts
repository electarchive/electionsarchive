import {
  CLOUDFLARE_ACCOUNT_ID,
  CLOUDFLARE_EMAIL_TOKEN,
} from "$app/env/private";
import { EMAIL_FROM_ADDRESS, EMAIL_FROM_NAME } from "#lib/constants.js";
import { renderEmail } from "#lib/server/email-layout.js";

export const sendEmail = async (
  to: string,
  content: Parameters<typeof renderEmail>[0],
  headers?: Record<string, string>,
) => {
  if (CLOUDFLARE_ACCOUNT_ID == null || CLOUDFLARE_EMAIL_TOKEN == null) {
    throw new Error(
      "Set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_EMAIL_TOKEN to send email",
    );
  }
  const { subject, html, text } = renderEmail(content);
  const response = await fetch(
    `https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/email/sending/send`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${CLOUDFLARE_EMAIL_TOKEN}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        from: { address: EMAIL_FROM_ADDRESS, name: EMAIL_FROM_NAME },
        to,
        subject,
        html,
        text,
        headers,
      }),
    },
  );
  const result: unknown = await response.json().catch(() => null);
  const sent =
    response.ok &&
    typeof result === "object" &&
    result != null &&
    "success" in result &&
    result.success === true;
  if (!sent) {
    throw new Error(
      `Email to ${to} failed with ${response.status}: ${JSON.stringify(result)}`,
    );
  }
};
