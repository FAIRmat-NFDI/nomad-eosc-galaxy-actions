# Contribute to the documentation

## Writing guide

Follow FAIRmat's [NOMAD Docs Writing Guide](https://github.com/FAIRmat-NFDI/nomad-docs/blob/develop/docs/writing_guide.md){:target="_blank" rel="noopener"} for anything you write here. In short:

- **Structure by [Diátaxis](https://diataxis.fr/){:target="_blank" rel="noopener"}**: tutorials (learning-oriented, step-by-step),how-to guides (goal-oriented, practical), explanations (understanding-oriented, background), or references (information-oriented, precise). Identify which one you're writing before you start — a page that mixes them is harder to navigate, not more complete.
- **Write for the reader's task, not the codebase's layout.** Be as detailed as needed, as concise as possible; state prerequisites up front. Remember this plugin has a mixed audience — NOMAD plugin developers *and* people unfamiliar with the NOMAD infrastructure. The latter includes the Galaxy team maintaining the tools and workflows on their side — so don't assume NOMAD background where the Galaxy side is the more likely reader (the [explanation](../explanation/explanation.md) page is written with that in mind).
- **Stay consistent**: canonical names (NOMAD, NOMAD Oasis, Galaxy), `backticks` for code/file names/literal values, "double quotes" for UI text/labels/error messages, **bold** for UI elements and important emphasis, *italics* for first-introduced terms.
- **Links**: descriptive text, never bare `here`/`link`; internal links use path-hierarchy names (`[How-to > Install this Plugin](...)`); external links open in a new tab (`{:target="_blank" rel="noopener"}`). Broken links fail CI — fix them, or open an issue if you can't.
- **Admonitions**: standard titles only (e.g. `!!! warning "Attention"`,
  `!!! tip "Important"`); no custom ones.
- **Use images sparingly** — each image is a maintainability burden. Prefer content you can express in code: [Mermaid](https://mermaid.js.org/){:target="_blank" rel="noopener"} diagrams first, then SVG, then JPG as a last resort.
- **Verify against the real system** before merging — commands, output, and examples should match what actually happens. For anything touching the Galaxy round trip, that means actually running `scripts/test_galaxy_roundtrip.py`, not just reading the code.

## Build the docs locally

Docs dependencies are part of the `docs` extra:

```console
uv pip install -e ".[docs]"
```

Serve the site locally with live-reload:

```console
mkdocs serve
```

Before opening a PR, build with `--strict` so broken links/nav entries fail locally
instead of in CI:

```console
mkdocs build --strict
```

## Add a new page

1. **Pick the right section** — this repo's `docs/` layout already mirrors the Diátaxis categories:

   | Directory | Kind |
   |---|---|
   | `docs/tutorial/` | Tutorials |
   | `docs/how_to/` | How-to guides |
   | `docs/explanation/` | Explanation |
   | `docs/reference/` | Reference |

2. **Add the Markdown file** in that directory.
3. **Register it in the nav** in [`mkdocs.yml`](https://github.com/FAIRmat-NFDI/nomad-eosc-galaxy-actions/blob/main/mkdocs.yml){:target="_blank" rel="noopener"} — a page not listed there won't appear in the site, even if it builds.
4. **Images/data**: put a page's assets in an `images/`/`data/` subdirectory next to that page, not shared across pages — e.g. `docs/how_to/images/my-diagram.svg`.
5. Preview with `mkdocs serve` and check the page renders and the nav entry shows up where you expect.
