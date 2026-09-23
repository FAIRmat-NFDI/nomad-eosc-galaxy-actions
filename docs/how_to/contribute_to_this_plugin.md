# Contribute to This Plugin

??? info "Structure of this repository"
    The software is located inside [`src/nomad_eosc_galaxy_actions`](https://github.com/FAIRmat-NFDI/nomad-eosc-galaxy-actions/tree/main/src/nomad_eosc_galaxy_actions){:target="_blank" rel="noopener"}, with unit tests in [`tests`](https://github.com/FAIRmat-NFDI/nomad-eosc-galaxy-actions/tree/main/tests){:target="_blank" rel="noopener"},
    standalone scripts (not part of the installed package) in [`scripts`](https://github.com/FAIRmat-NFDI/nomad-eosc-galaxy-actions/tree/main/scripts){:target="_blank" rel="noopener"},
    and the Galaxy-team-facing demo in [`examples/galaxy_demo`](https://github.com/FAIRmat-NFDI/nomad-eosc-galaxy-actions/tree/main/examples/galaxy_demo){:target="_blank" rel="noopener"}.

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
git clone https://github.com/FAIRmat-NFDI/nomad-eosc-galaxy-actions.git
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

Commit your changes on a separate branch and open a pull request. CI checks linting, runs the tests, and builds the docs; once those pass and a review happens, the PR can be merged. Before releasing, keep the version in sync between `pyproject.toml` and `CITATION.cff` — `publish.yml`'s `cff-version-check` job fails the release otherwise.

Changing something in `docs/`? See [How-to guides > Contribute to the Documentation](contribute_to_the_documentation.md) for the writing conventions, how to build the docs locally, and how to add a new page.

## Developing this plugin as part of NOMAD

If you're working on this plugin's NOMAD integration (the Action, the trigger entry schema) rather than just `galaxy_client.py` in isolation, use [`nomad-distro-dev`](https://github.com/FAIRmat-NFDI/nomad-distro-dev){:target="_blank" rel="noopener"}, FAIRmat's development environment for NOMAD and its plugins. See [How-to guides > Install this Plugin](install_this_plugin.md) for how this repo is
wired into that workspace.

## Troubleshooting

If you hit an issue with the plugin or with setting up the development environment, open a [GitHub issue](https://github.com/FAIRmat-NFDI/nomad-eosc-galaxy-actions/issues/new){:target="_blank" rel="noopener"}.
