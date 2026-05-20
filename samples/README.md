## Samples

This folder contains small example datasets you can load into **molmanager**.

### Files
- **`example_structures.sdf`**: a tiny SDF you can open via **File → Open…**
- **`example_chem.db`**: an example SQLite database with a `chemicals` table.

### Load the SQLite sample
In molmanager:
- Go to **External → Connect to SQL database…**
- Click **SQLite…** and select `samples/example_chem.db`
- Choose **Table name** and enter `chemicals`
- Click **Load into main table**

The sample includes a `SMILES` column, so molmanager will render structures automatically.

