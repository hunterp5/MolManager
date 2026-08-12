# Random Molecule

Random Molecule generates or fetches random molecules into the table, with count, optional seed, skip-existing, and optional ChEMBL fetch.

## Goal

Populate a sandbox library for UI testing, method demos, or exploratory browsing.

## When to use

Use when you need quick structures without importing a file, or to pull random ChEMBL examples.

## Inputs / scope

Adds new rows to the current table; does not require a pre-existing selection.

## Options

- **Number of molecules**.
- **Seed (optional)**.
- **Skip structures already in the table**.
- **Fetch from ChEMBL** - when using ChEMBL-backed random retrieval.
- **Add to table** / **Cancel**.

## Workflow

1. Set how many molecules to add.
2. Optionally set seed and skip-existing.
3. Choose local random generation vs **Fetch from ChEMBL**.
4. **Add to table** and proceed with tools as usual.

## Use cases

- Demo clustering on a fresh random set.
- Pull ChEMBL samples for teaching fingerprints.
- Stress-test filters on diverse structures.

## Tips and limits

Random molecules are not project IP - do not confuse with your real series. ChEMBL fetch needs network access. Skipping existing structures helps avoid duplicates when re-running.
