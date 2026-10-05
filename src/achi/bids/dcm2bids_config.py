"""Build the dcm2bids 3.x config from the expected-series table.

Generating it (instead of keeping a hand-written JSON next to the table) means the
series check and the conversion can never disagree about which series is which.
Each description matches on SeriesNumber AND the description pattern, so a shifted
series number cannot be converted under the wrong name.

Only the BOLD descriptions get an "id". dcm2bids converts descriptions WITH an id
first and resolves IntendedFor against the ids converted so far; if the field maps
had ids too they could be converted before the BOLD runs and end up with an empty
IntendedFor (= fMRIPrep silently skips distortion correction).
"""
from __future__ import annotations

import re

from .series import Expected


def _fnmatch_from_regex(regex: str) -> str:
    """Turn the table's simple regexes into dcm2bids fnmatch patterns."""
    pat = regex.removeprefix("^").removesuffix("$")
    pat = pat.replace(".*", "*")
    if re.search(r"[\\()\[\]{}+?|]", pat):
        raise ValueError(f"description regex too complex for dcm2bids fnmatch: {regex!r}")
    return pat or "*"


def build_config(expected: list[Expected], intendedfor_style: str = "relative") -> dict:
    descs, bold_ids = [], []
    for e in expected:
        criteria = {"SeriesNumber": e.series, "SeriesDescription": _fnmatch_from_regex(e.description)}
        if e.role == "anat_T1w":
            descs.append({"datatype": "anat", "suffix": "T1w",
                          "criteria": criteria})
        elif e.role in ("fmap_PA", "fmap_AP"):
            d = e.role[-2:]
            descs.append({"datatype": "fmap", "suffix": "epi",
                          "custom_entities": f"dir-{d}", "criteria": criteria,
                          "sidecar_changes": {"IntendedFor": "__BOLD_IDS__"}})
        elif e.role in ("bold", "sbref"):
            desc = {"datatype": "func", "suffix": e.role,
                    "custom_entities": f"task-{e.task}_run-{e.run}",
                    "criteria": criteria,
                    "sidecar_changes": {"TaskName": e.task}}
            if e.role == "bold":
                desc = {"id": f"bold_{e.task}_run{e.run}", **desc}
                bold_ids.append(desc["id"])
            descs.append(desc)
    for d in descs:
        if d.get("sidecar_changes", {}).get("IntendedFor") == "__BOLD_IDS__":
            d["sidecar_changes"]["IntendedFor"] = list(bold_ids)
    return {
        "search_method": "fnmatch",
        "case_sensitive": True,
        "bids_uri": "relative" if intendedfor_style == "relative" else "URI",
        # -ba y: anonymise sidecars (no patient name/ID/birth date in BIDS JSON)
        "dcm2niixOptions": "-b y -ba y -z y -f '%3s_%f_%p_%t'",
        "descriptions": descs,
    }
