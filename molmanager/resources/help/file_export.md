# Export

Export writes the table (all rows or the current selection) to common chemistry and spreadsheet formats for sharing or downstream tools.

## Goal

Produce a portable file of structures and properties without leaving MolManager's current analysis state behind.

## When to use

Use after filtering to a hit list, after adding scores/descriptors, or when handing molecules to docking or ELN systems.

## Inputs / scope

Current table columns and structures. Choose all rows or selected rows depending on the export dialog scope.

## Options

- Export **all** rows or **selected** rows.
- Format choice appropriate to the destination (e.g. SDF, SMILES, tabular).
- Destination path / file name.

## Workflow

1. Select rows if you only need a subset.
2. Open **Export** and pick format and scope.
3. Choose the output path and run the export (may appear under **Processes**).
4. Verify the file in the target application.

## Use cases

- Export a diverse subset for external vendor quoting.
- Send selected docking candidates as SDF.
- Dump scored QSAR predictions to CSV for a report.

## Tips and limits

Hidden or filter-excluded rows may or may not be included depending on whether you export the full table vs selection - check scope. Some formats drop depiction caches and keep connection tables only. Large exports can be slow; watch Processes.
