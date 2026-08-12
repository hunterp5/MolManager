# Smina

Smina runs the Smina docking engine with receptor/ligand paths, box center/size, and search settings (exhaustiveness, modes, threads, extra args).

## Goal

Dock prepared ligands into a receptor box and collect poses/scores from MolManager.

## When to use

Use when PDB/PDBQT prep is done and you want local Smina runs without leaving the app.

## Inputs / scope

Requires a **Smina executable**, receptor and ligand files, and an output path. Box defined by center and size.

## Options

- **Smina executable** - path to the binary.
- **Receptor**, **Ligand**, **Output** (+ **Browse...**).
- **Center X/Y/Z** and **Size X/Y/Z** - search box.
- **Exhaustiveness**, **Num modes**, **Energy range**.
- **CPU threads**, **Working dir**, **Extra args**.
- **Run Smina** / **Stop** / **Close**.

## Workflow

1. Set the Smina executable and working directory.
2. Choose receptor, ligand, and output paths.
3. Define the box center/size and search parameters.
4. **Run Smina**, monitor progress, **Stop** if needed, then inspect poses.

## Use cases

- Redock a crystallographic ligand to validate the box.
- Dock a small selected series with higher exhaustiveness.
- Sweep energy range / num modes for pose diversity.

## Tips and limits

Docking quality hinges on box placement and ligand/receptor prep. Smina must be installed and reachable. Long runs occupy CPU - adjust threads thoughtfully on shared machines.
