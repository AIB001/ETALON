"""Render the bibliography of everything the builder can put in a cascade.

A screening campaign ends in a methods section, and a methods section has to
name the tools that decided which molecules survived.  Vendored weights carry a
``CITATION.md`` next to the bytes because there is a folder to put it in; a
pip-installed backend has no such folder, so without this document its paper
lives only in a Python literal nobody writing up results is going to read.

The file is generated rather than maintained.  A hand-written bibliography drifts
the moment an option is added -- and it drifts silently, which is the failure
mode that matters, because a missing citation looks exactly like a tool that was
never used.  ``tests/cascade/test_citations.py`` regenerates it and compares, so
adding an option without its reference fails the suite rather than the review.
"""

from __future__ import annotations

from molcascade.assets.catalog import iter_assets
from molcascade.assets.models import AssetSpec
from molcascade.cascade.catalog import CRITERIA, BackendOption, CriterionSpec

_HEADER = """# What to cite

Every tool the cascade builder can put in a screen, and the reference to cite when
it decided which molecules survived.

This file is generated from `src/molcascade/cascade/catalog.py`. Do not edit it by
hand -- regenerate it with:

```bash
molcascade cite --output docs/citations.md
```

Vendored weights and rule tables carry their own citation next to the bytes; see
[`vendor/MANIFEST.md`](../vendor/MANIFEST.md). MolCascade itself is not a method
and does not want citing in place of the tools it orchestrates.

**Runnable here** means an adapter is registered and the option can be selected in
the builder. **Reviewed** means the tool was assessed and its reference recorded,
but nothing in MolCascade can execute it yet -- it is listed so that the menu shows
what was considered and rejected, not only what shipped.
"""

_UNCITED = "_No reference recorded._"


def _option_block(option: BackendOption) -> list[str]:
    state = "Runnable here" if option.executable else "Reviewed, no adapter"
    lines = [f"#### {option.label}", ""]
    facts = [f"- **Engine** — {option.engine}", f"- **Licence** — {option.license_spdx}"]
    facts.append(f"- **Status** — {state}")
    if option.requires:
        facts.append(f"- **Needs installed** — {', '.join(option.requires)}")
    if option.requires_assets:
        facts.append(f"- **Needs assets** — {', '.join(option.requires_assets)}")
    if option.throughput_per_second is not None:
        facts.append(
            f"- **Throughput** — ~{option.throughput_per_second:,} molecules/second per "
            "lane (one core, or one GPU for the isolated engines)"
        )
    lines.extend(facts)
    lines.append("")
    lines.append(option.citation.strip() if option.citation.strip() else _UNCITED)
    lines.append("")
    return lines


def _criterion_block(spec: CriterionSpec) -> list[str]:
    lines = [f"### {spec.label}", "", spec.summary.strip(), ""]
    for option in spec.options:
        lines.extend(_option_block(option))
    return lines


def _asset_block(spec: AssetSpec) -> list[str]:
    megabytes = spec.total_bytes / (1024 * 1024)
    lines = [f"### {spec.display_name}", "", spec.summary.strip(), ""]
    lines.append(f"- **Version** — {spec.version}")
    lines.append(f"- **Licence** — {spec.license_spdx}")
    lines.append(f"- **Size on disk** — {megabytes:.1f} MiB across {len(spec.files)} file(s)")
    lines.append(f"- **Trust** — {spec.trust.value}")
    if spec.used_by:
        lines.append(f"- **Read by** — {', '.join(spec.used_by)}")
    lines.append(f"- **Local path** — `vendor/{spec.id}/`")
    lines.append("")
    if spec.citations:
        for citation in spec.citations:
            lines.append(f"{citation.reference}")
            lines.append("")
    else:
        lines.append(_UNCITED)
        lines.append("")
    return lines


_ASSET_HEADER = """## Weights and rule tables

Bytes that ship separately from the code, each pinned by SHA-256 so the thing that
ran is the thing that was reviewed. Every directory also carries its own
`CITATION.md`; this section is the same content in one place, and
[`vendor/MANIFEST.md`](../vendor/MANIFEST.md) records the digests.

One set of weights is not listed here because it is not vendored: ADMET-AI v2 ships
its checkpoints inside its wheel, at `admet_ai/resources/models`. The digest check
still applies -- see the ADMET endpoint options above for its reference.
"""


def render_citations() -> str:
    """Build the whole document as one markdown string."""

    lines = [_HEADER.strip(), ""]
    runnable = sum(1 for spec in CRITERIA for option in spec.options if option.executable)
    total = sum(len(spec.options) for spec in CRITERIA)
    assets = tuple(iter_assets())
    lines.append(
        f"{total} tools across {len(CRITERIA)} criteria; {runnable} of them have an "
        f"adapter and can run here. {len(assets)} vendored asset(s) carry weights or "
        "rule tables of their own."
    )
    lines.append("")
    lines.append("## Tools")
    lines.append("")
    for spec in CRITERIA:
        lines.extend(_criterion_block(spec))
    lines.append(_ASSET_HEADER.strip())
    lines.append("")
    for asset in assets:
        lines.extend(_asset_block(asset))
    return "\n".join(lines).rstrip() + "\n"


def uncited_options() -> tuple[tuple[str, str], ...]:
    """Return ``(criterion_id, option_id)`` for every option missing a reference.

    Separated from rendering so a test can name what is missing instead of
    diffing a whole document to find out.
    """

    return tuple(
        (spec.id, option.id)
        for spec in CRITERIA
        for option in spec.options
        if not option.citation.strip()
    )


__all__ = ["render_citations", "uncited_options"]
