# Previews

The workbench shows each world's preserved sources, its inspection image and an explicit preview control. Opening a preview runs a fresh copy of the world; viewing, building or validating the page never executes a candidate. Nothing a preview does changes a reading or the preserved sources.

A preview fills the browser window with the work itself, without operating-system fullscreen. A short "Press Esc to exit" notice fades after three seconds, Escape closes the preview, and reopening it starts a fresh session.

## How each world opens

| World | Kind | At home (`npm run dev`) | Static site |
| --- | --- | --- | --- |
| Dreamy Sol | Browser | Sandboxed frame | Sandboxed frame |
| Astra · xhigh, Astra · max | Zsh | Console in the isolated runner | Replays a recording |
| Sol 5.6 | Ruby + browser | Loopback server in a sandboxed frame | Screenshot and note |
| Sol 6.1 | Browser snapshot | Sandboxed frame | Sandboxed frame |
| DeepSeek V4.1 Flash 1017 · dream 1 | Ruby + browser | Loopback server in its own tab | Screenshot and note |
| DeepSeek V4.1 Flash 1017 · dream 2 | Browser | Sandboxed frame | Sandboxed frame |
| Claude Opus 5.5 | Python | Console in the isolated runner | Replays a recording |
| Claude Fable 5.1 | Python | Console in the isolated runner | Replays a recording |

## Browser worlds

Browser copies run in sandboxed frames with opaque origins. A world opts into a browser preview only through an explicit entry in the [preview registry](../data/artefacts.json); importing a bundle does not. The entry must be a retained HTML source inside that run's preview directory, and candidate files cannot replace the host adapter.

During the build, every copied HTML document receives the same content policy and host adapter; a document without an explicit `<head>` is rejected rather than copied without a policy. The policy blocks resource connections, workers, objects, forms and ordinary cross-origin navigation. The adapter supplies memory-only browser storage, disables service-worker registration and blocks external link navigation and popups. Resetting or closing the frame discards its temporary state.

Serve the page over HTTP, with `npm run dev` or a static host. Opened from disk, sandboxed frames cannot load `file:` assets, so the preview shows how to start a local server instead of relaxing the sandbox.

The frame is defense in depth, not an operating-system firewall: a document can still attempt self-navigation, and a static host cannot guarantee network policy. The browser smoke test injects an external navigation probe and fails if any external URL is requested.

Sol 6.1's browser world is itself a saved snapshot: choosing a future changes the display, not its SQLite ledger. The second DeepSeek dream runs in the static mode its README offers, without the Ruby server that writes its filesystem archive.

## Native worlds

Native worlds run only through the isolated local runner, on macOS, with `npm run dev` at <http://127.0.0.1:4317>. Native previews require the macOS sandbox and never fall back to unrestricted execution on another platform.

- **Zsh:** the two Astra worlds run with the system Zsh in an in-browser xterm.js console.
- **Python:** Claude Opus 5.5's *Tendora Workbench* and Claude Fable 5.1's *Tendora dream world* run in the same console with the Command Line Tools' Python 3 in isolated mode (`-I -B`); only that tree is added to what the sandbox may execute. Their writing gestures are signed by a visitor or kept in the copy's own ledger, and options that would write outside the copy are refused.
- **Ruby:** Sol 5.6 runs with the system Ruby and bundled WEBrick on its own loopback port, and its browser interface appears in a sandboxed frame. The first DeepSeek dream runs the same way, but its server sends `frame-ancestors 'none'`. Its headers are not altered: the preview links to the running copy in its own tab, and Escape stops that session.

Console worlds, their entries and their commands are listed in `CONSOLES` and `COMMANDS` in the [preview runner](../scripts/previews.py); server worlds are in `SERVERS`.

### Isolation

Each session starts from the verified corpus in a fresh temporary root with two sibling trees: `world/`, which the candidate may write, and `control/`, which only the host uses. The sandbox profile, server log and command output live in `control/`, where the candidate can neither read nor rewrite them. Command output is read through an already-open file descriptor rather than by reopening a path the candidate controls.

Commands receive a small environment and a temporary home inside `world/`. The macOS sandbox permits system executables and reads of the copied world, restricts writes to that world, and denies network access except a Ruby server's loopback listener. Candidate files cannot be executed as host programs.

The console passes a bounded set of a world's own commands as arguments, never through a general shell, and quoted question text stays literal. Each process has a 15-second command timeout, a 20-second CPU limit, a 16-MiB file-size limit and 128 open files; the host monitors a 64-process session group, and output is limited to 64 KiB.

The local API checks the host, the origin and a per-server token. Closing, resetting or switching worlds removes the previous session; sessions also expire after 30 minutes without a console command and are cleaned up when the server exits normally. At most six native sessions exist at once. A force-killed runner may leave a temporary directory for manual cleanup, and it never writes back to `corpus/`. No remote execution service or public shell endpoint exists.

## The static site

`npm run build:pages` builds the static site and `npm run serve:pages` shows it at <http://127.0.0.1:4318>. The Pages workflow (`.github/workflows/pages.yml`) runs only when started by hand.

Browser worlds keep their sandboxed copies. Nothing native can run, so console worlds replay a recording made in the isolated runner ([Record a console](pipeline.md#record-a-console-for-the-static-site)): the console says it replays answers and runs nothing, and answers any other command with how to run the world locally. Server worlds show their screenshot with a note on running them from a local copy.

## What is never executed

- A candidate in its preserved directory. Previews run only copies, and only when opened.
- Any candidate during import, analysis, validation or build.
- The preserved directory during a screenshot. Screenshots come from a disposable browser copy that blocks external requests, service workers, WebSockets and non-loopback DNS.
- A console recording during the build, which only reads and validates it.

## Evidence boundary

Preview observations and console recordings are presentation, never assessment evidence. Where an inspection image is a current capture of a sandboxed preview rather than a retained original, its caption says so. A current preview can differ from the retained record: Sol 5.6, for example, catches up its copied state to the current time.

To exercise the previews, start the research view and run the preview test against it:

```sh
npm run dev:full
PREVIEW_URL=http://127.0.0.1:4317 npm run test:preview
```

The boundary tests check where the sandbox policy sits, deny reads of the host's control tree and reject a candidate-supplied executable. They skip only when an outer test sandbox does not permit a nested `sandbox-exec`.
