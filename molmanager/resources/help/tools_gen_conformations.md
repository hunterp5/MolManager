# Generate Conformations

Generate Conformations builds 3D conformer ensembles per molecule with energy window, force field, and pruning controls, optionally writing to the table or SDF.

## Goal

Sample low-energy 3D shapes for inspection, strain-aware review, or export to external modeling tools.

## When to use

Use when you need multiple conformers (not just one pose), especially for flexible ligands or ensemble exports.

## Inputs / scope

Scoped rows with valid structures; **Selected Rows Only** when checked. Output may go to table and/or SDF file.

## Options

- **Conformers** - number to request.
- **Energy window** - keep conformers within this window.
- **Force field** - MMFF / UFF.
- **Seed** - reproducibility control.
- **RMS prune** - drop near-duplicates.
- **Max iterations** - minimizer budget.
- **Selected Rows Only** - scope.
- **Add to table** / **Save to SDF** (+ **Browse...**).

## Workflow

1. Select molecules and set ensemble size / energy window.
2. Choose force field, seed, and RMS pruning.
3. Run generation (watch **Processes**).
4. Add results to the table and/or save SDF.

## Use cases

- Build ensembles for flexible linkers before manual review.
- Export multi-conformer SDF to an external docking suite.
- Compare conformer counts across a congeneric series.

## Tips and limits

Cost scales with atoms × conformers × rows. Force-field minima are not protein-aware. Failed embeddings skip or partially fill - check logs/status.
