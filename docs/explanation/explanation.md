# Explanation

This page assumes no prior knowledge of either NOMAD or Galaxy. It's written for readers who know either a bit of NOMAD but not Galaxy, vice-versa, or none of the two. It is, however, particularly helpful for the Galaxy team building and maintaining the tools and workflow this plugin calls.

## What NOMAD is, briefly

[NOMAD](https://nomad-lab.eu){:target="_blank" rel="noopener"} is FAIRmat's research data platform for materials science. It stores data (an **entry**) together with a structured, searchable description of it, not just an opaque file. Entries live inside an **upload** (NOMAD's GUI calls this a *project*), the unit a user manages together — it's roughly analogous to a Galaxy **history**, in that it's the container a user's related datasets live in.

## What Galaxy is, briefly

[Galaxy](https://usegalaxy.eu){:target="_blank" rel="noopener"} is a web-based platform for running scientific computing **workflows** without writing code. A **tool** wraps one command-line program behind a form; a **workflow** chains several tools together, with one tool's outputs feeding the next tool's inputs. Running a workflow against a given set of inputs is an **invocation** — Galaxy schedules and runs it on its own compute backend, and reports back which step is queued, running, or done. A user's uploaded files and workflow results live in a **history**, the role NOMAD's **upload** plays on the NOMAD side. This NOMAD plugin talks to Galaxy the same way a human would through the Galaxy GUI, just through its REST API (via [`bioblend`](https://bioblend.readthedocs.io/){:target="_blank" rel="noopener"}) instead of clicking: upload a file into a history, invoke a workflow, poll the invocation, and download the resulting dataset.

## What a NOMAD Action is

A NOMAD **Action** is a plugin mechanism for triggering a long-running external job from NOMAD (in the NOMAD UI or programmatically) and getting the result back once it's done. It's built on [Temporal](https://temporal.io){:target="_blank" rel="noopener"} for its retry/durability capabilities — conceptually similar to what Galaxy's own job scheduler already has for Galaxy jobs.

## The general round trip pattern

`find_peaks` (below) follows one common shape for this kind of Action — read data out of NOMAD, hand it to Galaxy, wait for the run, write the result back — but that's a pattern this plugin's Actions tend to follow, not a contract every Action must:

```
NOMAD entry
     │  a button on NOMAD triggers the Action
     ▼
NOMAD Action
     │  ① read the relevant data out of NOMAD upload storage
     │  ② upload it to Galaxy, invoke a workflow
     │  ③ poll until the run finishes
     │  ④ download the result
     │  ⑤ write it back into NOMAD as a new or updated entry
     ▼
New/updated NOMAD entry, linked back to the source
```

How an Action gets *triggered* varies too — `find_peaks` is a button on a NOMAD entry, but nothing about the Actions mechanism requires that: a future Action could just as well be triggered from NOMAD's generic `/actions` interface, or run unattended on a schedule (e.g. a cron job) with no NOMAD-side click at all. Adding a new Action means whatever fits its own trigger and data flow — the `find_peaks` implementation is a starting reference, not a mechanism to conform to.

## `find_peaks`: the XPS peak finding action currently implemented

### The round trip, concretely

```
XPS entry in NOMAD
       │  "Find Peaks" button on a small entry referencing the spectrum
       ▼
NOMAD Action (this plugin)
       │  ① read the spectrum's NeXus file out of NOMAD upload storage
       │  ② upload it to Galaxy, invoke "XPS peak finding"
       │  ③ poll until the run finishes
       │  ④ download the annotated result
       │  ⑤ add it as a new entry in the same NOMAD upload
       ▼
New NOMAD entry: original spectrum + detected peaks, linked back to the source
```

Steps ②–④ are performed in `galaxy_client.py`, and can also be executed standalone (i.e., with a NOMAD deployment) with `scripts/test_galaxy_roundtrip.py` (see the [tutorial](../tutorial/tutorial.md)) — that script is the fastest way to check the Galaxy side works without needing a NOMAD deployment at all.

### The contract between this plugin and the Galaxy tool/workflow

This plugin's code depends on some specifics of `pynxtools_peak_finding` and `XPS peak finding` that aren't otherwise obvious from the NOMAD side, and would break silently if changed without a corresponding update here:

- **Output parameter names**: `poll_invocation` (in `galaxy_client.py`) reads completed   job outputs by name and looks specifically for `output_nxs` (the annotated NeXus file) when deciding what to download. If the tool's `<data name="...">` output names ever change, this plugin needs updating too.
- **The workflow's step layout**: `activities/find_peaks/activities.py` hardcodes step index `"0"` as the data-input step and `"1"` as the tool step, matching `Galaxy-Workflow-XPS_peak_finding.ga`'s current two-step layout. A workflow restructure (e.g. inserting a step before the tool) would need this updated.
- **The workflow doesn't mark any step as an explicit workflow output** (`"workflow_outputs": []` in the `.ga` file for both steps), so `invocation['outputs']` stays empty. `poll_invocation` reads each step's *job* state and outputs directly instead, which always works regardless of whether outputs are declared, and merges in `invocation['outputs']` on top for the labels it does have. Marking explicit workflow outputs in a future version of the workflow wouldn't break anything on the NOMAD side -- those labels would just start showing up too.

## Why a workflow, not the bare tool

This plugin invokes the *workflow*, not the tool directly, even though today the workflow has exactly one step. That's deliberate: if peak finding ever grows a second step (background subtraction, a trained classifier, a second technique), that's a change to the workflow definition on the Galaxy side only — this plugin's code doesn't need to change to call a second tool or start orchestrating a sequence itself. Bioblend can invoke either a bare tool or a workflow with about the same amount of code, so this costs nothing today and keeps the door open.
