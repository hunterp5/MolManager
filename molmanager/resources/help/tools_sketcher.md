# Sketcher

The Sketcher is an interactive molecule editor for drawing, editing, and inserting structures into the MolManager workflow.

## Goal

Create or modify a molecule visually when SMILES editing is awkward, then use the result in the table or as a query.

## When to use

Use to prototype analogs, fix a bad import, or draw a query core for R-group or reaction work.

## Inputs / scope

Starts from a blank canvas or an existing molecule when launched in an edit context. Output is a structure you can apply/insert per the dialog actions.

## Options

- **Canvas** - interactive drawing surface with bond/atom tools.
- **Element** and **bond order** controls.
- **Edit tools** - select, delete, and ring templates as provided.
- **Apply / accept** - push the drawn molecule back to the caller or table.

## Workflow

1. Open **Sketcher** from the tools entry point.
2. Draw or edit the molecule.
3. Validate valence/stereo visually.
4. Apply/insert the structure into the table or query field.

## Use cases

- Sketch a core SMARTS precursor for R-group decomposition.
- Correct a mis-imported structure.
- Design a quick analog and add it as a new row.

## Tips and limits

Sketcher output still must be chemically valid for RDKit tools. Complex stereo may need careful bond markup. For bulk enumeration prefer Reaction Enumeration rather than manual drawing.
