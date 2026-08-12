# MMP

Matched Molecular Pair (MMP) analysis finds pairs related by small transformations, with controls for cuts, variable heavy atoms, and optional activity differences.

## Goal

Learn how small structural changes correlate with property/activity shifts in your table.

## When to use

Use on cleaned series with a meaningful activity column when you want transformation rules rather than global models.

## Inputs / scope

Structures from **Molecules from**; optional **Activity column**. Scope via **Selected Rows Only**.

## Options

- **Molecules from** - structure source.
- **Activity column** - property for deltas.
- **Max cuts** - fragmentation aggressiveness.
- **Max variable heavy atoms** - size of the changing fragment.
- **Minimum activity difference** - filter weak deltas.
- **Selected Rows Only**.
- **Write MMP annotations to the main table**.
- **OK** / **Cancel**.

## Workflow

1. Select or filter to a relevant chemical space.
2. Set cuts, variable-atom cap, and activity column.
3. Run MMP and review pair transformations.
4. Optionally write annotations back to the table for plotting.

## Use cases

- Find potency-increasing halogen swaps.
- Mine solubility cliffs between matched pairs.
- Restrict to selected series to avoid cross-chemotype noise.

## Tips and limits

MMP depends on fragmentation parameters - too-loose cuts explode pairs. Activity noise produces spurious cliffs; set a minimum difference. Not a substitute for full QSAR on diverse libraries.
