# Prepare PDBQT

Prepare PDBQT builds receptor and/or ligand PDBQT files used by AutoDock-style engines such as Smina.

## Goal

Convert prepared proteins and ligands into the charge/torsion representation docking expects.

## When to use

Use after Prepare PDB (receptor) and when ligands are ready as SDF, SMILES, or selected table rows.

## Inputs / scope

Receptor from **Input PDB**; ligands from **SDF file**, **SMILES strings**, or **Selected rows** with a rows structure source.

## Options

- **Receptor:** **Input PDB**, **Output PDBQT**, **Browse...**.
- **Ligand:** **Input mode** (SDF / SMILES / Selected rows), **SDF**, **SMILES**, **Rows source**, **Output PDBQT**.
- **Generate .pdbqt** / **Close**.

## Workflow

1. Set receptor input PDB and PDBQT output.
2. Choose ligand input mode and paths/rows.
3. Generate PDBQT files.
4. Point **Smina** at the resulting receptor/ligand files.

## Use cases

- Prepare a receptor once, then batch ligands from selected rows.
- Convert an SDF hit list to ligand PDBQTs.
- Build files for a Smina exhaustiveness sweep.

## Tips and limits

Bad protonation or tautomers propagate into PDBQT - prep ligands chemically first. Selected-rows mode needs a valid structure source. Validate atom types if docking scores look nonsensical.
