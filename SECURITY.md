# Security

Report vulnerabilities privately through GitHub: open the repository's [Security tab](https://github.com/electarchive/electionsarchive/security) and choose **Report a vulnerability**. Please don't open a public issue, pull request, or discussion about it.

Include what you found, where (a URL, an API route, an MCP tool, or a file and line), the steps to reproduce it, and what an attacker could do with it. We'll reply in the advisory, keep you posted while we fix it, and credit you when the fix ships unless you'd rather we didn't.

## Scope

In scope: the code in this repository and the services it runs at electionsarchive.com, including sign-in, API keys, uploads, proposals and review, the REST API, and the MCP server. Only the latest commit on `main` and the live site are supported.

Out of scope:

- Wrong or missing election data. Correct it on the site with Edit, or open a data error issue.
- Denial of service, load testing, and rate-limit testing against the live site.
- Social engineering, physical attacks, and reports from automated scanners without a working proof of concept.

## Testing

Test against your own local setup when you can (see [CONTRIBUTING.md](CONTRIBUTING.md)). On the live site, use only accounts you own, don't access or change other people's data, and stop as soon as you've confirmed the issue.
