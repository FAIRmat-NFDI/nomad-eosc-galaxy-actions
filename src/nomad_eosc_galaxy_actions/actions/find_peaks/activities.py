"""Activities for the find-peaks action.

This is deliberately the "empty run" from the sequencing plan: it proves the
Action round-trips through Temporal, without yet reading the spectrum's NeXus
file or calling Galaxy.
"""

from temporalio import activity

from nomad_eosc_galaxy_actions.actions.find_peaks.models import EchoInput


@activity.defn
def echo_spectrum_entry_id(data: EchoInput) -> dict:
    """Acknowledge the referenced spectrum entry without processing it."""
    return {'received_entry_id': data.spectrum_entry_id}
