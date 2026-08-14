# MMP Transform Ledger

Matched Molecular Pair (MMP) analysis finds pairs related by small transformations, with controls for cuts, variable heavy atoms, and optional activity differences. Open from **Tools → MMP → Transform Ledger**.

## Goal

Learn how small structural changes correlate with property/activity shifts in your table, then rank those changes as reusable transform rules.

## When to use

Use on cleaned series with a meaningful activity column when you want transformation rules rather than global models.

## Inputs / scope

Structures from **Molecules from**; optional **Activity column**. Scope via **Selected Rows Only**.

## Options

- **Molecules from** - structure source.
- **Activity column** - property for deltas.
- **Max cuts** - fragmentation aggressiveness.
- **Max variable heavy atoms** - size of the changing fragment.
- **Minimum activity difference** - filter weak deltas (0 = no floor).
- **Maximum activity difference** - exclude large deltas (0 = no ceiling).
- **Selected Rows Only**.
- **Write MMP annotations to the main table**.
- **OK** / **Cancel**.

## Workflow

1. Select or filter to a relevant chemical space.
2. Set cuts, variable-atom cap, and activity column.
3. Run MMP. Results open in the **transform ledger**, which groups pairs by chemically canonical `from>>to` fragment swap.
4. Sort or filter the ledger by support (**n**), median/mean Δ, or win rate.
5. Select a transform to preview the fragments; **Browse pairs** opens the pair stepper for that rule's evidence (or **Browse all pairs** for the full set).
6. Optionally click **Selected as Reference** with exactly one table molecule selected to show only pairs involving that compound; transforms and Δ are then oriented as reference → partner. **Clear Reference** restores the full view.
7. **Activity Cliffs** opens a scatter of structural-change size vs |Δactivity| for the pairs currently shown (respects reference filter).
8. **Pair Network** opens the neighborhood graph for the pairs currently shown (respects reference filter).
9. **Apply to seed** applies the selected transform to the current table selection (or the reference if nothing is selected) and adds product molecule(s) to the table with `MMP_Transform` / `MMP_Seed_ID` provenance.
10. Optionally **Write to table** for plotting or further filtering.

## Use cases

- Find potency-increasing halogen swaps with enough supporting pairs.
- Mine solubility cliffs between matched pairs.
- Restrict to selected series to avoid cross-chemotype noise.
- Design analogues by applying a trusted transform to a new seed (including scaffolds that were not in the original pairs).

## Tips and limits

MMP depends on fragmentation parameters - too-loose cuts explode pairs. Activity noise produces spurious cliffs; set a minimum and/or maximum difference. Ledger transforms are oriented with lexicographically ordered sidechains so the same chemical swap shares one row (Δ is flipped when sides are swapped). **Apply to seed** requires the seed to contain the transform's from-fragment under the same cut semantics; multi-site matches can yield multiple products. Not a substitute for full QSAR on diverse libraries.
