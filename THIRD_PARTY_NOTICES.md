# Third-party components

The browser terminal bundles these packages locally, with no CDN dependency:

- **@xterm/xterm 6.0.0**, MIT license. [Project](https://github.com/xtermjs/xterm.js).
  See its [license](src/vendor/xterm-LICENSE).
- **@xterm/addon-fit 0.11.0**, MIT license. The same project supplies this addon.
  See its [license](src/vendor/addon-fit-LICENSE).

The package version, registry tarball URL and verified SHA-512 integrity are
recorded beside each vendored component in `src/vendor/*-package.json`.
These licenses cover the terminal components; they do not change the rights
or provenance of the preserved experimental artefacts.

## Test-only tooling

These packages are development dependencies used only by checks and tests.
They are not shipped in the static report.

- **Playwright 1.64.0**, Apache License 2.0. [Project](https://playwright.dev/).
  It is a development dependency used only by the optional browser smoke tests
  and is not shipped in the static report.
- **Prettier 3.3.3**, MIT license. [Project](https://prettier.io/).
  It is a development dependency pinned in `package.json` and used only by the
  `format` and `format:check` scripts for the study's own `src/*.js`,
  `tests/*.mjs`, and `scripts/*.mjs` files. It is not shipped in the static
  report and does not change the rights of the material it formats.

## Preserved third-party content

Candidate worlds and model readings are preserved as produced and may quote,
reproduce, or build on third-party content under its own notice. Where a
preserved file carries its own license, credit, or provenance header, that
notice remains in force and is not replaced by the study's CC BY 4.0 or MIT
terms. Candidate code and candidate prose may carry different rights from each
other; assess and attribute each on its own notice and provenance rather than
assuming one regime for a whole world.
