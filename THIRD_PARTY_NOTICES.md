# Third-party components

The browser terminal bundles these packages locally, with no CDN dependency:

- **xterm.js 6.0.0**, MIT license. [Project](https://github.com/xtermjs/xterm.js).
  See its [license](src/vendor/xterm-LICENSE).
- **@xterm/addon-fit 0.11.0**, MIT license. The same project supplies this addon.
  See its [license](src/vendor/addon-fit-LICENSE).

The package version, registry tarball URL and verified SHA-512 integrity are
recorded beside each vendored component in `src/vendor/*-package.json`.
These licenses cover the terminal components; they do not change the rights
or provenance of the preserved experimental artefacts.

## Test-only tooling

- **Playwright 1.64.0**, Apache License 2.0. [Project](https://playwright.dev/).
  It is a development dependency used only by the optional browser smoke tests
  and is not shipped in the static report.
