# Security policy

## Reporting a vulnerability

Report vulnerabilities privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability** ([direct link](https://github.com/mirzahusadzic/after-the-poem/security/advisories/new)). Do not open a public issue for a vulnerability.

Include the affected commit, your platform and the steps to reproduce. Never include real API keys, router or Claude tokens, transcripts or session files; use synthetic values instead.

## Scope

In scope:

- The preview boundary: sandboxed browser frames with their content policy and host adapter, and the isolated local runner with its macOS sandbox profile, resource limits and local API checks.
- Import, validation and build: any path by which a preserved world could execute, write outside its copy or reach the network.
- Records: anything that lets a credential, a private conversation, a raw inference event or a host path into the repository or onto the page.
- The dream harnesses: the isolation, permission policy and canaries around Codex and Claude Code dreams.

Out of scope:

- Vulnerabilities in Codex, the Codex Unified Model Router, Claude Code, model providers, browsers or the macOS sandbox itself. Report those to their projects or vendors.
- Attacks that require running code as the same user or as an administrator. The runner is a local single-user tool.
- A world misbehaving inside its own sandbox. That is expected; escaping the sandbox is in scope.

## Security model

- Preserved worlds are untrusted code. Import, analysis, validation and build never execute them, and no candidate runs in its preserved directory.
- Browser worlds run as copies in sandboxed frames with opaque origins. A content policy blocks connections, workers, objects, forms and cross-origin navigation, and the host adapter keeps storage in memory. The frame is defense in depth, not an operating-system firewall.
- Native worlds run only through the isolated local runner on macOS: a fresh disposable copy, writes confined to it, no network except a Ruby world's loopback listener, per-process limits, and a local API that checks `Host`, a strong same-origin browser signal before issuing its per-server token, and both `Origin` and that token for actions. The local static server disables directory listings and sends `nosniff` and `no-referrer` headers. There is no fallback to unrestricted execution and no public shell endpoint.
- Model-authored text renders as escaped text or inert Markdown.
- Credentials are never stored in the repository. Router and Claude tokens stay in private local files or the environment, and the validator rejects credential-like metadata keys.
- Host paths are withheld from public metadata: bundle validation rejects them, report evidence text replaces them with a placeholder, and console recordings stop on any host path other than the copy's own folder, which they replace with a placeholder. A preserved candidate can still contain a path it wrote into its own source bytes; source views preserve that original evidence.
- The static site runs nothing native, and its publishing workflow runs only when started by hand.
