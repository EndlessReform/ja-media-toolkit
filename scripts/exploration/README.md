# Exploration scripts

This directory is for quick, disposable analysis scripts over local media
metadata dumps. These scripts are allowed to be a little scrappy: their job is
to turn a messy question into a concrete report, not to become durable service
code.

If a script graduates into production, move the useful contract or parser into
`packages/` or the relevant `envs/` service and leave behind a note explaining
what replaced it.

Run Python exploration from an environment directory, not the repository root.
For example:

```sh
cd envs/services
uv run ../../scripts/exploration/episode_coverage.py \
  --input ../../kitsuinfo_filenames.jsonl.gz \
  --out-dir ../../scripts/exploration/out
```

