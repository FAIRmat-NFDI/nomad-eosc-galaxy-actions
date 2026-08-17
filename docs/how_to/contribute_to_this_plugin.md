# Contribute to This Plugin

This guide walks through setting up a working environment for developing
`nomad-eosc-galaxy-actions`.

??? info "Structure of this repository"
    The software is located inside
    [`src/nomad_eosc_galaxy_actions`](https://github.com/FAIRmat_NFDI/nomad-eosc-galaxy-actions/tree/main/src/nomad_eosc_galaxy_actions),
    with unit tests in
    [`tests`](https://github.com/FAIRmat_NFDI/nomad-eosc-galaxy-actions/tree/main/tests).

## Setup

It is recommended to use Python 3.11 with a dedicated virtual environment. We
recommend [`uv`](https://github.com/astral-sh/uv), an extremely fast Python
package and project manager; a more classical `venv`/`pip` approach works too.

=== "uv"
    `uv` is capable of creating a virtual environment and installing the
    required Python version at the same time.

    ```bash
    uv venv --python 3.11
    ```

=== "venv"
    Note that you will need to install the Python version manually beforehand.

    ```bash
    python3.11 -m venv .venv
    . .venv/bin/activate
    ```

## Development installation

Clone the repository:

```console
git clone https://github.com/FAIRmat_NFDI/nomad-eosc-galaxy-actions.git
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

We use [Ruff](https://docs.astral.sh/ruff/) for linting/formatting and mypy
for type checking. `.pre-commit-config.yaml` also runs pyupgrade, nbstripout,
and cspell. We use [`prek`](https://github.com/j178/prek) — a drop-in, faster
reimplementation of `pre-commit` that reads the same config file — as the
runner; it's installed as part of the `dev` extra above, so you just need to
enable the hook once per clone:

```console
prek install          # installs the git hook
prek run --all-files   # run all hooks against the whole repo once
```

You can also run Ruff directly:

```console
ruff check .
ruff format . --check
```

If `cspell` flags a real (correctly spelled) word, add it to
`.cspell/custom-dictionary.txt`, or regenerate it from the current
source/docs:

```console
scripts/generate_custom_dict.sh
```

## Testing

Unit tests are written with [pytest](https://docs.pytest.org/en/stable/):

```console
pytest -sv tests
```

Our CI produces a more comprehensive report using `pytest-cov`:

```console
uv pip install pytest-cov
pytest --cov=src tests
```

### Debugging

For interactive debugging, use `pytest --pdb`, or configure your IDE. In
VS Code, add to `.vscode/launch.json`:

```json
{
  "configurations": [
      {
        "name": "<descriptive tag>",
        "type": "debugpy",
        "request": "launch",
        "cwd": "${workspaceFolder}",
        "program": "${workspaceFolder}/.venv/bin/pytest",
        "justMyCode": true,
        "env": { "_PYTEST_RAISE": "1" },
        "args": ["-sv", "--pdb", "<path-to-plugin-tests>"]
    }
  ]
}
```

`.vscode/settings.json` already applies linting/formatting on save.

## Contributing on GitHub

Commit your changes on a separate branch and open a pull request. CI checks
linting, runs the tests, and builds the docs; once those pass and a review
happens, the PR can be merged. Before releasing, keep the version in sync
between `pyproject.toml` and `CITATION.cff` — `publish.yml`'s
`cff-version-check` job fails the release otherwise.

Changing something in `docs/`? See
[How-to guides > Contribute to the Documentation](contribute_to_the_documentation.md)
for the writing conventions, how to build the docs locally, and how to add a
new page.

## Developing this plugin as part of NOMAD

If you're working on this plugin's NOMAD integration (schema, dashboard) —
not just its own unit tests — use
[`nomad-distro-dev`](https://github.com/FAIRmat-NFDI/nomad-distro-dev),
FAIRmat's development environment for NOMAD and its plugins. See
[How-to guides > Install this Plugin](install_this_plugin.md) for how this
repo is wired into that workspace.

## Troubleshooting

If you hit an issue with the tool or with setting up the development
environment, open a
[GitHub issue](https://github.com/FAIRmat_NFDI/nomad-eosc-galaxy-actions/issues/new).
