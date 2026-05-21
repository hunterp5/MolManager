"""In-app HTML user guides for MolManager menus, tools, and external integrations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAction,
    QDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from .qt_widget_utils import make_window_minimizable

if TYPE_CHECKING:
    from PyQt5.QtWidgets import QWidget


# (guide_id, short title for menus / list)
GUIDE_MENU: list[tuple[str, str]] = [
    ("overview", "Overview"),
    ("file_menu", "File — open, import, session, export"),
    ("edit_menu", "Edit — undo, clipboard, selection"),
    ("table", "Table — columns, context menu, structures"),
    ("tools_chem", "Tools — disconnect, 2D, descriptors, conformers"),
    ("tools_adv", "Tools — superpose, core/fragment tools, FP similarity"),
    ("tools_ion", "Tools — pKa, protomers"),
    ("tools_filter", "Tools — filters and search"),
    ("tools_viz", "Tools — browser, plot, calculator, sketcher"),
    ("data", "Data — Analyze Table and cluster"),
    ("processes", "Background jobs — Processes"),
    ("sql", "External — SQL database"),
    ("pubchem", "External — PubChem"),
    ("chembl", "External — ChEMBL"),
    ("patents", "External — Query Patents"),
    ("boltz2", "Tools — Boltz-2"),
    ("vina_dock", "Tools — Dock (Vina)"),
]

_GUIDE_HTML: dict[str, str] = {
    "overview": """
<h2>MolManager overview</h2>
<p>MolManager is a desktop compound table built on <b>PyQt5</b> and <b>RDKit</b>. Each row is a record;
the <b>Structure</b> column shows 2D depictions when a structure is available. Most heavy work runs in
the background so the window stays responsive.</p>
<p><b>Where to find things</b></p>
<ul>
<li><b>File</b> — open SD-type files, import data, save/load <b>sessions</b>, export the table, <b>Browser</b> for the current selection.</li>
<li><b>Edit</b> — undo/redo, copy and paste cells or SMILES, delete selected rows, clear the table.</li>
<li><b>Tools</b> — chemistry operations, filters, in-table search, plotter, calculator, sketcher, <b>Boltz-2</b> (<code>boltz predict</code>), and <b>Dock (Vina)</b> (<code>vina</code>); hover a menu item for a short description.</li>
<li><b>Data</b> — summarize columns and cluster by fingerprint.</li>
<li><b>External</b> — SQL, PubChem, ChEMBL, patents (SureChEMBL).</li>
<li><b>Help</b> — opens this guide (topics in the left list). <b>F1</b> does the same.</li>
<li><b>Processes</b> (top-right of the menu bar) — modeless list of queued/running background jobs, Render 2D,
an active Boltz-2 run, and an active Vina docking run.</li>
</ul>
<p>The <b>status line</b> at the bottom of the main window shows short messages (including debounced Boltz-2 log lines).</p>
""",
    "file_menu": """
<h2>File menu</h2>
<p><b>Open File</b> (<b>Ctrl+O</b>) — load a supported table file into the current session (structures are parsed with RDKit where possible).</p>
<p><b>Import Data</b> — append or merge from another file with column mapping options.</p>
<p><b>Sessions</b> — <b>Open Session</b> / <b>Save Session</b> store the working table and related state so you can resume later.
<b>New Session</b> clears and starts fresh (with confirmation if needed). <b>Duplicate Session</b> opens a second MolManager window with a copy of the data.</p>
<p><b>Export All</b> (<b>Ctrl+S</b>) writes every row; <b>Export Selected</b> writes only the current selection. Pick format and columns in the export dialog.</p>
<p><b>Browser</b> — review and operate on the current row selection in a dedicated dialog.</p>
""",
    "edit_menu": """
<h2>Edit menu</h2>
<p><b>Undo / Redo</b> — standard stack for many table edits.</p>
<p><b>Copy / Paste</b> — work on the current selection in the table (including SMILES-oriented paste where applicable).</p>
<p><b>Delete Selection</b> (<b>Delete</b>) — remove selected rows.</p>
<p><b>Invert Selection</b> — select every row that is not selected (whole table, including rows hidden by filters).</p>
<p><b>Clear Selection</b> (<b>Ctrl+Shift+D</b>) — clear the cell/row highlight without deleting data.</p>
<p><b>Clear Table</b> (<b>Ctrl+Shift+Backspace</b>) — remove all rows after confirmation.</p>
""",
    "table": """
<h2>Table, columns, and context menus</h2>
<p>Click column headers to sort (numeric vs alphabetic options appear from the header context menu where supported).
Right-click a <b>column name</b> for <b>Select</b> (first row per distinct value, or empty cells;
on <b>Structure</b>, empty means no chemical structure in the row), rename, duplicate, delete the column, search, or sort.</p>
<p>Right-click a <b>cell</b> for copy/paste, edit value, clear, row duplicate/delete, and (on structure-capable columns)
chemistry actions such as <b>Open in Sketcher</b>, <b>View Conformers</b>, <b>View in 3D</b>, <b>View in 2D</b>, and
<b>Render 2D</b> (draws into the column you clicked, using that column’s chemistry).</p>
<p>The <b>filter panel</b> on the right can be toggled from <b>Tools → Filter</b> or <b>Ctrl+Shift+L</b>. Filter cards
restrict which rows stay visible; combine cards as needed.</p>
""",
    "tools_chem": """
<h2>Tools — disconnect, 2D render, descriptors, conformers</h2>
<p><b>Tools → Prepare Structures</b> — <b>Disconnect Largest Fragments</b> (split salts/multi-component entries; keep
largest fragment) and <b>Render 2D</b> (batch 2D images for a chosen column; listed in <b>Processes</b> while active).</p>
<p><b>Calculate Descriptors</b> — pick a structure column and descriptor categories (RDKit-backed drug-likeness,
counts, etc.); optional <b>only selected rows</b>.</p>
<p><b>Tools → Conformations</b> — <b>Generate Conformations</b> (ensembles in <b>confs</b>), <b>Generate Single Conformation</b>
(one minimized geometry per row), and <b>Superpose Conformers</b> (align packed <b>confs</b> data).</p>
""",
    "tools_adv": """
<h2>Tools — superpose, core/fragment tools, fingerprint similarity</h2>
<p><b>Superpose Conformers</b> — align conformers already stored for rows (packed <code>confs</code> data); tune reference
conformer, heavy-atom-only, reflection, and optional SMARTS alignment.</p>
<p><b>Tools → R-Group Decomposition</b> — <b>Core-Based Decomposition</b> (labeled core SMARTS/SMILES and substituent
columns), <b>BRICS</b> / <b>RECAP Decomposition</b> (fragment SMILES columns with automatic 2D render), and
<b>BRICS</b> / <b>RECAP Recomposition</b> (combinatorial products appended as new rows).</p>
<p><b>Fingerprint Similarity</b> — pick a fingerprint type (several Morgan sizes, RDK path, MACCS, atom pair,
or topological torsion), a query from a table row or SMILES, and compare against the table (optionally
restricted to selected rows). Results are listed with <b>highest Tanimoto first</b>; added scores use the column <b>Tanimoto Similarity</b>. Add hits back to the main table from the results list.</p>
<p><b>QSAR</b> — quantitative structure–activity modeling: pick an activity column (Y), any mix of numeric descriptor
columns and/or 2D fingerprints (X), and a scikit-learn model (ridge, random forest, gradient boosting, logistic/SVM for
classification). Train on labeled rows with hold-out and cross-validation metrics, then add predictions for in-scope
rows as a new column.</p>
""",
    "tools_ion": """
<h2>Tools — pKa and protomers</h2>
<p><b>pKa Predictor</b> — microstate pKa estimates from a structure column or a single SMILES; optional filters for
most basic / most acidic pKa; table scope can be limited to selected rows.</p>
<p><b>Generate Protomers</b> — enumerate protonation states informed by the same engine, with approximate population
weights at a chosen pH; results can be appended to the main table.</p>
<p>Both tools run longer jobs through the <b>process queue</b>; watch progress in <b>Processes</b> and the status line.</p>
""",
    "tools_filter": """
<h2>Tools — filters and search</h2>
<p><b>Tools → Filter</b> — <b>Substructure Filter</b> (SMARTS), numeric <b>Slider</b>, <b>Text</b>, and <b>Category</b> cards.
Use <b>Toggle Panel</b> or <b>Ctrl+Shift+L</b> to show or hide the filter sidebar.</p>
<p><b>Search</b> (<b>Ctrl+F</b>) — in-table search on one or more columns (<b>Add</b> for another row;
<b>AND</b>/<b>OR</b> between rows). Within a row, combine terms with <code>&amp;</code> (AND),
<code>|</code> or comma (OR), e.g. <code>&gt;10 &amp; &lt;500</code> or <code>"eth*"|"prop*"</code>.
Put string text in double or single quotes (e.g. <code>"sodium chloride"</code>) so commas and
<code>&amp;</code>/<code>|</code> inside the text are not treated as operators. Unquoted terms are for
numeric comparisons (<code>&gt;5</code>), <code>empty</code> / <code>not empty</code>, and similar operators.
Use <code>NOT "text"</code>, <code>= "exact"</code>, and wildcards inside quotes (<code>"eth*"</code>).
Substructure mode matches SMILES/SMARTS (<code>NOT</code> excludes a pattern; use parentheses if a
SMARTS pattern contains <code>|</code>).</p>
""",
    "tools_viz": """
<h2>Tools — plot, calculator, sketcher</h2>
<p><b>Plotter</b> — pick a <b>Plot type</b> at the top (default <b>Scatter/Histogram</b>: histogram when only X is set, 2D/3D scatter from axes).
Other types include line, box, and violin. Choose numeric columns on X, Y, and Z; leave Y or Z as <b>None</b> when unused. Plots use visible (filtered) rows.</p>
<p><b>Calculator</b> — keypad-style expression editor to build a new numeric column from existing columns (operators,
sqrt, log10, exp, and <code>[ColumnName]</code> references). Can be disabled via environment/config
(<code>MOLMANAGER_DISABLE_CUSTOM_CALC</code>).</p>
<p><b>Sketcher</b> — draw or edit a structure modelessly; export to the table or to a file. Wildcard atom element sets
are configurable from the sketcher tools.</p>
""",
    "data": """
<h2>Data — analyze table and cluster</h2>
<p><b>Analyze Table</b> — statistical summaries for numeric columns and overviews of categorical/text columns
for the rows currently passing filters (optionally <b>only selected rows</b>). Includes correlation matrices, percentiles,
<b>outlier detection</b> (IQR/Tukey, Z-score, or modified Z; then <b>Select outliers in table</b> to match the main selection), polynomial and simple non-linear curve fits,
and common <b>statistical tests</b> (SciPy).</p>
<p><b>Cluster</b> — cluster compounds by fingerprint (several algorithms and parameters). <b>Exploratory mode</b> samples
many parameter combinations; review metrics, then apply a trial to add a cluster assignment column. Large tables:
prefer K-means for very large <i>n</i>; Butina / Jarvis-Patrick paths scale quadratically in the number of compounds.</p>
""",
    "processes": """
<h2>Background jobs — Processes window</h2>
<p>Open <b>Processes</b> from the top-right of the menu bar. The table lists:</p>
<ul>
<li>The <b>process queue</b> job currently running (with cancel when the worker supports it) and any <b>queued</b> jobs.</li>
<li><b>Render 2D</b> while a structure batch is drawing.</li>
<li><b>Boltz-2</b> while <code>boltz predict</code> is running from <b>Tools → Boltz-2 prediction</b>.</li>
<li><b>Dock (Vina)</b> while <code>vina</code> is running from <b>Tools → Dock (Vina)</b>.</li>
</ul>
<p><b>Cancel</b> applies to the selected row (cooperative cancel for queue workers, stop Render 2D, kill Boltz-2 or Vina, or remove
a queued job). <b>Clear queue</b> removes all waiting jobs without stopping the one currently running.</p>
""",
    "sql": """
<h2>External — load SQL data</h2>
<p>Connect with a SQLAlchemy URL (SQLite, PostgreSQL, MySQL, SQL Server templates are provided).
Choose either a <b>SQL query</b> or a <b>table name</b>, set row limits if needed, then load.</p>
<p>Rows are easiest to map when the result includes a SMILES column; the app will try to detect
structures from common column names.</p>
""",
    "pubchem": """
<h2>External — PubChem</h2>
<p><b>Identity</b> looks up each SMILES (or selected table rows) in PubChem via PubChemPy.</p>
<p><b>Similarity (2D Tanimoto)</b> uses PubChem's fast 2D similarity search (one query SMILES, minimum
Tanimoto, max hits). Hits below your minimum are dropped using the app's Morgan Tanimoto vs your query in
<b>Tanimoto Similarity</b> (PubChem's server threshold can be looser than the value you set).</p>
<p>Under <b>Retrieve fields</b>, checkboxes start unchecked—pick what you need, or use <b>Retrieve all fields</b>
for the previous "everything on" behavior (slower / heavier requests).</p>
<p>Use <b>Add unique structures only</b> to skip duplicates vs the table (canonical SMILES).</p>
""",
    "chembl": """
<h2>External — ChEMBL</h2>
<p><b>Identity</b> resolves molecules by canonical SMILES through the ChEMBL web resource client.</p>
<p><b>Similarity</b> uses ChEMBL's cartridge search (threshold 70–100 on their scale; values below 0.70
are raised). Hits are ordered by <b>decreasing similarity</b>. Each hit can include <b>Tanimoto Similarity</b> from ChEMBL's similarity score.</p>
<p>Optional activities and targets are off by default; enable them when needed (large similarity batches can be slow—
reduce limits if needed). Under <b>Retrieve fields</b>, use <b>Retrieve all fields</b> to enable identity, structures,
every molecule property, activities, targets, and the pChEMBL-only filter at once.</p>
<p><b>Add unique structures only</b> avoids duplicate rows.</p>
""",
    "patents": """
<h2>External — Query Patents (SureChEMBL)</h2>
<p>Similarity search against <b>SureChEMBL</b> (EMBL-EBI): chemistry linked to patents and documents.
Uses their server-side Tanimoto on RDKit Morgan fingerprints (see SureChEMBL documentation).</p>
<p>Each hit includes <b>Tanimoto Similarity</b> from the service. Use <b>Add unique structures only</b> to
avoid duplicates.</p>
""",
    "boltz2": """
<h2>Tools — Boltz-2 prediction</h2>
<p>Runs the <code>boltz predict</code> command (install: <code>pip install boltz</code>; see upstream
docs for GPU). Two modes:</p>
<ul>
<li><b>YAML file</b> — input/output paths; MSA server, override, potentials, accelerator/devices; a scrollable
<b>Predict tuning</b> section (recycling/sampling/diffusion steps, parallelism, model, MSA limits, affinity diffusion,
optional checkpoints/cache, seeds, and diagnostic flags); plus optional extra CLI tokens appended as on the shell
(use <code>boltz predict --help</code> for the full list).</li>
<li><b>Quick cofold</b> — minimal protein sequence + ligand SMILES (sequence can be pasted or loaded from a
<b>protein FASTA</b> file; first sequence is used if the file has several); optional affinity block; writes a
temporary YAML and runs predict using the same tuning options from the YAML tab.</li>
</ul>
<p>While a job runs, the <b>command log</b> timestamps each stdout/stderr line, sets <code>PYTHONUNBUFFERED=1</code> for the child process,
and shows a short <b>progress</b> line above the log. The <b>main window status line</b> still shows debounced snippets from Boltz output
so you can see progress without keeping the Boltz-2 window focused. The same run appears in <b>Processes</b>
as <b>(boltz2)</b>; you can stop it from there or with <b>Stop</b> in the Boltz-2 window. When the job ends, the status line
reports the exit code.</p>
<p>Official format and options: <a href="https://github.com/jwohlwend/boltz">github.com/jwohlwend/boltz</a></p>
""",
    "vina_dock": """
<h2>Tools — Dock (AutoDock Vina)</h2>
<p>Runs the <code>vina</code> command-line program for <b>rigid</b> receptor–ligand docking in a rectangular search box.
Install Vina from the <a href="https://vina.scripps.edu">Scripps Vina</a> site (or your package manager) and ensure the binary is on <code>PATH</code>,
or set the full path in the dialog.</p>
<p>Provide <b>receptor</b> and <b>ligand</b> as PDBQT files (typically prepared with MGLTools / Open Babel). Set the box <b>center</b> and <b>size</b> (Å),
output PDBQT path, and optional exhaustiveness, number of modes, energy range, and CPU count. <b>Run Vina</b> streams stdout/stderr into the log;
<b>Stop</b> terminates the subprocess. The same run appears in <b>Processes</b> as <b>(vina)</b> while active.</p>
""",
}


def guide_html(guide_id: str) -> str:
    body = _GUIDE_HTML.get(guide_id) or _GUIDE_HTML["overview"]
    return f"<html><body style='font-size:13px'>{body}</body></html>"


def open_user_guide_dialog(parent: QWidget | None, guide_id: str = "overview") -> None:
    """Modal window with a topic list and HTML body."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("MolManager — user guides")
    dlg.resize(760, 520)
    outer = QVBoxLayout(dlg)

    content = QHBoxLayout()
    lst = QListWidget()
    lst.setMinimumWidth(220)
    for gid, title in GUIDE_MENU:
        it = QListWidgetItem(title)
        it.setData(Qt.UserRole, gid)
        lst.addItem(it)
    content.addWidget(lst)

    browser = QTextBrowser()
    browser.setOpenExternalLinks(True)
    browser.setHtml(guide_html(guide_id))
    content.addWidget(browser, 1)
    outer.addLayout(content)

    def on_pick(current: QListWidgetItem | None, _previous: QListWidgetItem | None = None) -> None:
        if current is None:
            return
        gid = current.data(Qt.UserRole)
        if isinstance(gid, str):
            browser.setHtml(guide_html(gid))

    lst.currentItemChanged.connect(on_pick)
    for i in range(lst.count()):
        it = lst.item(i)
        if it is not None and it.data(Qt.UserRole) == guide_id:
            lst.setCurrentItem(it)
            break
    else:
        lst.setCurrentRow(0)

    close_btn = QPushButton("Close")
    close_btn.clicked.connect(dlg.accept)
    outer.addWidget(close_btn)
    make_window_minimizable(dlg)
    dlg.exec_()


def add_user_guide_menu_entries(menu: QMenu, parent: QWidget) -> None:
    """Append one menu action per guide topic (opens the browser on that page)."""
    for gid, title in GUIDE_MENU:
        menu.addAction(
            QAction(title, parent, triggered=lambda checked=False, g=gid: open_user_guide_dialog(parent, g))
        )
