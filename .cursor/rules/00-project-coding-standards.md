## ChemManager / MolManager coding standards

These rules apply to all new or modified code in this repository.

### Python style and formatting

- Use **PEP 8** and keep code clean and maintainable.
- Format Python code with **Ruff**.
- Prefer **type hints** for public functions and non-trivial internal helpers.
- Avoid redundant comments; only comment on non-obvious intent or trade-offs.

### Naming conventions

- Use **snake_case** for variables and functions.
- Use **PascalCase** for classes.
- Use descriptive names; avoid single-letter names except for conventional loop indices.

### Quality and correctness

- Fix problems at the **cause**, not at the symptom.
- Keep UI responsive by offloading heavy work off the GUI thread.
- Keep changes cohesive; avoid drive-by refactors unless required.

### Git commits

- Anytime a git commit is made, write **detailed messages for every file that was altered** (brief per-file bullets).

