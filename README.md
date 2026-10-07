[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
![](https://github.com/FAIRmat-NFDI/nomad-eosc-galaxy-actions/actions/workflows/actions.yml/badge.svg)
![](https://github.com/FAIRmat-NFDI/nomad-eosc-galaxy-actions/actions/workflows/mkdocs-deploy.yml/badge.svg)
[![DOI](https://zenodo.org/badge/759916501.svg)](https://doi.org/10.5281/zenodo.23110600)


# `nomad-eosc-galaxy-actions`: NOMAD ↔ Galaxy EOSC interoperability demonstrator

[NOMAD](https://nomad-lab.eu) is FAIRmat's research data platform for materials science; [Galaxy](https://usegalaxy.eu) is a widely-used scientific workflow platform. `nomad-eosc-galaxy-actions` connects the two: it provides a NOMAD Action that sends an XPS spectrum stored in NOMAD to Galaxy for peak finding, and links the annotated result back into NOMAD as a new entry once the Galaxy run completes. It's an interoperability demonstrator for the [German node of the European Open Science Cloud (EOSC)](https://eosc.eu/building-the-eosc-federation/eosc-node-germany). It demonstrate the interoperability of the platform and shows that NOMAD-stored data can be processed on an external workflow platform without leaving NOMAD's data model.

## Docs

More information about this plugin is available in the [documentation](https://fairmat-nfdi.github.io/nomad-eosc-galaxy-actions/). If you are new here, or from the Galaxy team and want to see the workflow without a NOMAD deployment, start with the [tutorial](https://fairmat-nfdi.github.io/nomad-eosc-galaxy-actions/tutorial/tutorial.html).

## Contact person in FAIRmat for this plugin

Lukas Pielsticker

## How to cite this work

Pielsticker, L., Grüning, B., Krieger, M., & Weber, H. B. (2026). nomad-eosc-galaxy-actions: A NOMAD Actions plugin for the EOSC demonstrator with Galaxy. (Version v0.1.0) [Computer software]. Zenodo. https://doi.org/10.5281/zenodo.23110601
