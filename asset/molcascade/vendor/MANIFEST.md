# Vendored assets

Model weights, rule tables and reference data that MolCascade uses but does not author. Each subdirectory carries a `CITATION.md` naming the paper behind it, the licence it is distributed under, and the SHA-256 of every file.

The asset root is the directory this file sits in -- `vendor/` beside the source tree by default. Override it with the `MOLCASCADE_ASSET_ROOT` environment variable, and run `molcascade assets status` to print the one in effect.

| Asset | Kind | Licence | Size | Cite |
| --- | --- | --- | ---: | --- |
| [`scscore`](scscore/CITATION.md) | weights | MIT | 20.4 MiB | 10.1021/acs.jcim.7b00622 |
| [`rd_filters`](rd_filters/CITATION.md) | rules | MIT | 0.1 MiB | — |

## Getting them

```bash
molcascade assets status          # what is here and whether it verifies
molcascade assets fetch --all     # download everything that is missing
molcascade assets fetch scscore   # or just one
```

## Using them

Refer to a file by `asset:<asset-id>/<path>` in a cascade configuration. That reference resolves to the same content on every machine, and resolution verifies the digest before the path is handed to a backend.

## What a screening run will not do

Download. Ever. A run that needs an absent asset stops and prints the `molcascade assets fetch` command that would provide it. Results therefore depend on files whose digests are recorded, not on what a remote host served that afternoon.

## Software, not just bytes

This file covers assets that ship as files. The backends that read them are pip-installed and have no directory here to hold a citation, so their references live in `docs/citations.md` alongside these -- one place to look when writing up a screen. Regenerate it with `molcascade cite`.
