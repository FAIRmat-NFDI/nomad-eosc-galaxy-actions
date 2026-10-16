# Contribute to This Plugin

??? info "Structure of this repository"
    The software is located inside [`src/nomad_eosc_galaxy_actions`](https://github.com/FAIRmat-NFDI/nomad-eosc-galaxy-actions/tree/main/src/nomad_eosc_galaxy_actions){:target="_blank" rel="noopener"}, with unit tests in [`tests`](https://github.com/FAIRmat-NFDI/nomad-eosc-galaxy-actions/tree/main/tests){:target="_blank" rel="noopener"},
    standalone scripts (not part of the installed package) in [`scripts`](https://github.com/FAIRmat-NFDI/nomad-eosc-galaxy-actions/tree/main/scripts){:target="_blank" rel="noopener"},
    and the Galaxy-team-facing demo in [`examples/galaxy_demo`](https://github.com/FAIRmat-NFDI/nomad-eosc-galaxy-actions/tree/main/examples/galaxy_demo){:target="_blank" rel="noopener"}.

## Branching model

This plugin follows the same branching model as the core NOMAD packages.

| Branch | Role |
|---|---|
| `develop` | Default branch. All regular development PRs target this branch. |
| `main` | Released versions only. Only `release/*` branches are merged here, so its history is the release history. |
| `release/vX.Y.Z` | Temporary release branch, created from `develop` and merged into `main`. The version may carry a pre-release suffix, e.g. `release/vX.Y.Z-beta` or `release/vX.Y.Z-rc1`. |

| Activity | Branch |
|---|---|
| Feature development | `develop` |
| PR review and integration | `develop` |
| Selecting changes for a release | `release/vX.Y.Z` |
| Release-specific bug fixes | `release/vX.Y.Z` |
| Published release history | `main` |
| Porting release fixes back to ongoing development | Merge `release/vX.Y.Z` back into `develop` |

The `Check if source is release/*` workflow
([`.github/workflows/check-source-is-release.yml`](https://github.com/FAIRmat-NFDI/nomad-eosc-galaxy-actions/blob/develop/.github/workflows/check-source-is-release.yml){:target="_blank" rel="noopener"})
allows PRs into `main` only from `release/*` branches. PRs from any other branch, **including `develop`**,
fail this check. It is enforced by marking it as a required status check in the branch protection rule
for `main`.

## Setup

Requires Python 3.10+. We recommend [`uv`](https://github.com/astral-sh/uv){:target="_blank" rel="noopener"}, an extremely fast Python package and project manager; a more classical `venv`/`pip`
approach works too.

=== "uv"
    ```bash
    uv venv
    ```

=== "venv"
    ```bash
    python3 -m venv .venv
    . .venv/bin/activate
    ```

## Development installation

```console
git clone https://github.com/FAIRmat-NFDI/nomad-eosc-galaxy-actions.git \
    --branch develop
cd nomad-eosc-galaxy-actions
```

Install the package in editable mode, together with its dev dependencies:

=== "uv"
    ```bash
    uv pip install -e ".[dev]"
    ```

=== "pip"
    ```bash
    pip install --upgrade pip
    pip install -e ".[dev]"
    ```

## Linting, formatting, and pre-commit hooks

We use [Ruff](https://docs.astral.sh/ruff/){:target="_blank" rel="noopener"} for linting/formatting and [mypy](https://mypy-lang.org/){:target="_blank" rel="noopener"} for type checking, run via [pre-commit](https://pre-commit.com/){:target="_blank" rel="noopener"}:

```console
pre-commit install          # installs the git hook, once per clone
pre-commit run --all-files  # run all hooks against the whole repo once
```

You can also run Ruff directly:

```console
ruff check .
ruff format . --check
```

In the pre-commit as well as in the GitHub CI, we use [`cspell`](https://cspell.org/){:target="_blank" rel="noopener"} for spellchecking. If `cspell` flags a real (correctly spelled) word, add it to `.cspell/custom-dictionary.txt` through this script:

```console
scripts/generate_custom_dict.sh
```

## Testing

Unit tests are written with [pytest](https://docs.pytest.org/en/stable/){:target="_blank" rel="noopener"}:

```console
pytest -sv tests
```

The Galaxy round trip itself isn't exercised by the unit test suite — it needs a real Galaxy account. `scripts/test_galaxy_roundtrip.py` covers that separately; see the [tutorial](../tutorial/tutorial.md).

## Contributing on GitHub

Commit your changes on a separate branch created from `develop` and open a pull request against `develop`:

```console
git switch develop && git pull
git switch -c <short-descriptive-name>
# commit your changes, then
git push -u origin <short-descriptive-name>
```

Never open a pull request against `main`: it receives only `release/*` branches. CI checks linting, runs the tests, and builds the docs; once those pass and a review happens, the PR can be merged.

Changing something in `docs/`? See [How-to guides > Contribute to the Documentation](contribute_to_the_documentation.md) for the writing conventions, how to build the docs locally, and how to add a new page.

## Release workflow

1. Create the release branch from `develop`:

    ```console
    git switch develop && git pull
    git switch -c release/vX.Y.Z
    git push -u origin release/vX.Y.Z
    ```

1. Commit only release-specific changes on this branch (version bumps, changelog, last-minute bug fixes). New features keep going into `develop`.
1. Bump `version` in `CITATION.cff` to `X.Y.Z`, including any pre-release suffix (e.g. `0.1.2-rc1` for the tag `v0.1.2-rc1`). The package version itself comes from the git tag; `publish.yml`'s `cff-version-check` job fails if `CITATION.cff` differs from it.
1. Open a PR from `release/vX.Y.Z` into `main`. Do not merge yet!
1. Create a GitHub release with the tag `vX.Y.Z` and select `release/vX.Y.Z` as its target branch. The publish workflow refuses to upload to PyPI if the release does not target `release/vX.Y.Z` or if the tagged commit is not on that branch.
1. Once CI passes and the release is published to PyPI, merge the PR.
1. If you fixed anything directly on the release branch, open a PR from `release/vX.Y.Z` into `develop` so the fixes are not lost in ongoing development.
1. Delete the `release/vX.Y.Z` branch.

## Developing this plugin as part of NOMAD

If you're working on this plugin's NOMAD integration (the Action, the trigger entry schema) rather than just `galaxy_client.py` in isolation, use [`nomad-distro-dev`](https://github.com/FAIRmat-NFDI/nomad-distro-dev){:target="_blank" rel="noopener"}, FAIRmat's development environment for NOMAD and its plugins. See [How-to guides > Install this Plugin](install_this_plugin.md) for how this repo is
wired into that workspace.

## Troubleshooting

If you hit an issue with the plugin or with setting up the development environment, open a [GitHub issue](https://github.com/FAIRmat-NFDI/nomad-eosc-galaxy-actions/issues/new){:target="_blank" rel="noopener"}.
