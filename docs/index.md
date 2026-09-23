# Welcome to the `nomad-eosc-galaxy-actions` documentation

A NOMAD Actions plugin for the EOSC demonstrator with Galaxy.

## What this plugin does

[NOMAD](https://nomad-lab.eu){:target="_blank" rel="noopener"} is FAIRmat's research data platform for Materials Science; [Galaxy](https://usegalaxy.eu){:target="_blank" rel="noopener"} is a widely-used scientific workflow platform. This NOMAD plugin connects the two: it provides [**NOMAD Actions**](https://docs.nomad-lab.eu/howto/plugins/types/actions.html){:target="_blank" rel="noopener"} (NOMAD's plugin entry point for long-standing asynchronous tasks) — that take data already stored in NOMAD, sends them to Galaxy to run tools abd workflows, and links the annotated result back into NOMAD as a new entry once the Galaxy run completes.

Currently, one Action is implemented: `find_peaks`, which sends an XPS spectrum to a Galaxy workflow bundling its [`XPS peak finding` tool](https://usegalaxy.eu/?tool_id=pynxtools_peak_finding){:target="_blank" rel="noopener"} (built on [`pynxtools-xps`](https://github.com/FAIRmat-NFDI/pynxtools-xps){:target="_blank" rel="noopener"}) for peak detection. The interface itself is generic, so this is meant to be the first of several Actions, not the only one.

The round trip end to end:

```
Entry in NOMAD --> Action trigger --> NOMAD Action --> Galaxy workflow run
                                                            │
New NOMAD entry <-------------------------------------------┘
```

This is an interoperability demonstrator in the context of the [German node of the European Open Science Cloud (EOSC)](https://eosc.eu/building-the-eosc-federation/eosc-node-germany){:target="_blank" rel="noopener"}. It cuts both ways: for NOMAD, the concrete goal is to show that a NOMAD-stored dataset can be processed by an external workflow platform like Galaxy without leaving NOMAD's data model, and that the round trip is reproducible and inspectable on both sides; for Galaxy, it shows that Galaxy can interface with other platforms under the EOSC node, with NOMAD as an example. See the [Explanation](explanation/explanation.md) page for the full technical picture, including the contract this plugin relies on from the Galaxy side.

## Introduction

<div markdown="block" class="home-grid">
<div markdown="block">

### Tutorial

New to this plugin? Or are you from the Galaxy team and want to see the round trip work without a NOMAD deployment? Start here.

- [Tutorial](tutorial/tutorial.md)

</div>
<div markdown="block">

### How-to guides

How-to guides provide step-by-step instructions for a wide range of tasks, with the overarching topics:

- [How-to guides > Install this Plugin](how_to/install_this_plugin.md)
- [How-to guides > Use this Plugin](how_to/use_this_plugin.md)
- [How-to guides > Contribute to this Plugin](how_to/contribute_to_this_plugin.md)
- [How-to guides > Contribute to the Documentation](how_to/contribute_to_the_documentation.md)

</div>

<div markdown="block">

### Explanation

The [Explanation](explanation/explanation.md) section provides background knowledge on this plugin.

</div>
<div markdown="block">

### Reference

The [Reference](reference/references.md) section includes all CLI commands and arguments, all configuration options, the possible schema annotations and their arguments, and a glossary of used terms.

</div>
</div>

<h2> Contact </h2>

For questions or suggestions:

- Open an issue on the [`nomad-eosc-galaxy-actions` GitHub](https://github.com/FAIRmat-NFDI/nomad-eosc-galaxy-actions/issues){:target="_blank" rel="noopener"}
- Join our [Discord channel](https://discord.gg/Gyzx3ukUw8){:target="_blank" rel="noopener"}
- Get in contact with our [lead developers](contact.md).

<h2>Project and community</h2>

The work is funded by the Deutsche Forschungsgemeinschaft (DFG, German Research Foundation) - [460197019 (FAIRmat)](https://gepris.dfg.de/project/460197019?lang=en){:target="_blank" rel="noopener"}.