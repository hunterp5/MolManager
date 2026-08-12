# Protonate

Protonate writes the dominant protomer at a chosen pH into an output structure column, optionally using pkasolver-backed predictions when available.

## Goal

Produce pH-relevant ionization states for permeability-minded prep, docking ligands, or consistent descriptor calculation.

## When to use

Use when default microstates are wrong for your assay pH, or before comparisons that depend on charge state.

## Inputs / scope

Input structures from the selected source; optional **Selected Rows Only**. pkasolver improves pKa-aware dominance when installed/enabled.

## Options

- **Structure source** - which structure column to read.
- **pH** - target pH for dominant protomer selection.
- **Output column** - destination structure column name.
- **Selected Rows Only** - limit to the selection.
- **Render 2D image in output column** - refresh depictions when writing.
- **Run** - start the job.

## Workflow

1. Set structure source, **pH**, and output column.
2. Choose full table or **Selected Rows Only**.
3. Run and wait for completion.
4. Point later tools at the output column as structure source.

## Use cases

- Protonate at pH 7.4 before medchem property plots.
- Prepare ligands at physiological pH for docking.
- Compare acidic vs basic series at a shared pH.

## Tips and limits

Results are approximate; tautomer/protomer ensembles may still matter. Without pkasolver, behavior may be more limited - install it for best results. Always keep the original column if you need neutral parents.
