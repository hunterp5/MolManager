"""Modeless UI to run Boltz-2 (``boltz`` CLI) for protein–ligand cofolding predictions."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import tempfile
import time
from pathlib import Path

from PyQt5.QtCore import QProcess, QProcessEnvironment, QTimer
from PyQt5.QtGui import QCloseEvent, QKeySequence
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QShortcut,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...bundled_paths import default_external_executable
from ..qt_widget_utils import apply_monospace_to_text_edit, make_window_minimizable


def _one_line_sequence(seq: str) -> str:
    return re.sub(r"\s+", "", (seq or "").strip())


def _parse_fasta_records(text: str) -> list[tuple[str, str]]:
    """
    Parse FASTA text into (header, sequence) pairs.

    Header is the text after ``>`` on a record line (may be empty). Sequence lines are
    concatenated until the next ``>``. Whitespace within lines is removed from sequences.
    """
    records: list[tuple[str, str]] = []
    cur_hdr = ""
    cur_chunks: list[str] = []
    for line in (text or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        if raw.startswith(">"):
            if cur_hdr or cur_chunks:
                seq = _one_line_sequence("".join(cur_chunks))
                if seq:
                    records.append((cur_hdr, seq))
            cur_hdr = raw[1:].strip() or "sequence"
            cur_chunks = []
        else:
            cur_chunks.append(raw)
    if cur_hdr or cur_chunks:
        seq = _one_line_sequence("".join(cur_chunks))
        if seq:
            records.append((cur_hdr, seq))
    return records


class Boltz2Dialog(QDialog):
    """
    Front-end for the Boltz-2 ``boltz predict`` command (YAML input).

    See: https://github.com/jwohlwend/boltz and ``boltz predict --help`` after installation.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._main_window = parent
        self.setWindowTitle("Boltz-2 — protein–ligand prediction")
        self.resize(920, 680)
        self._proc = QProcess(self)
        self._proc.finished.connect(self._on_proc_finished)
        self._proc.errorOccurred.connect(self._on_proc_error)
        self._proc.readyReadStandardOutput.connect(self._append_stdout)
        self._proc.readyReadStandardError.connect(self._append_stderr)
        self._proc.started.connect(self._on_proc_started)
        # True from Run until finished/error — keeps Processes accurate even if QProcess is
        # briefly NotRunning right after start() or stuck before Running on slow disks / AV.
        self._predict_session_active = False
        self._stdout_buf = ""
        self._stderr_buf = ""
        self._run_t0 = 0.0
        self._last_progress_snippet = ""
        self._pending_main_status = ""
        self._status_debounce = QTimer(self)
        self._status_debounce.setSingleShot(True)
        self._status_debounce.timeout.connect(self._flush_status_to_main)
        self._heartbeat = QTimer(self)
        self._heartbeat.setInterval(3000)
        self._heartbeat.timeout.connect(self._on_heartbeat)

        root = QVBoxLayout(self)

        exe_row = QHBoxLayout()
        exe_row.addWidget(QLabel("boltz executable:"))
        self.edit_exe = QLineEdit(default_external_executable("boltz"))
        self.edit_exe.setToolTip(
            "Name on PATH or a full path to the boltz launcher. "
            "Windows tip: if boltz works in Anaconda Prompt but not here, browse to "
            "…\\envs\\YOUR_ENV\\Scripts\\boltz.exe — Explorer-launched apps often do not see conda PATH."
        )
        exe_row.addWidget(self.edit_exe, 1)
        btn_exe = QPushButton("Browse…")
        btn_exe.setToolTip("Select boltz.exe (Windows: under your conda/venv Scripts folder).")
        btn_exe.clicked.connect(self._browse_boltz_executable)
        exe_row.addWidget(btn_exe)
        root.addLayout(exe_row)

        tabs = QTabWidget()
        root.addWidget(tabs, 1)

        # --- Tab: existing YAML ---
        tab_yaml = QWidget()
        y_layout = QVBoxLayout(tab_yaml)
        grp = QGroupBox("Prediction from YAML file")
        grp.setToolTip(
            "Point to a Boltz input YAML and output directory, set run flags and predict tuning, then run boltz predict. "
            "Progress and logs appear below."
        )
        form = QFormLayout(grp)
        self.edit_yaml = QLineEdit()
        self.edit_yaml.setPlaceholderText("Path to input.yaml (sequences, constraints, …)")
        btn_y = QPushButton("Browse…")
        btn_y.clicked.connect(self._browse_yaml)
        yh = QHBoxLayout()
        yh.addWidget(self.edit_yaml, 1)
        yh.addWidget(btn_y)
        yw = QWidget()
        yw.setLayout(yh)
        form.addRow("Input YAML:", yw)

        self.edit_out = QLineEdit()
        self.edit_out.setPlaceholderText("Output directory (predictions, processed, …)")
        btn_o = QPushButton("Browse…")
        btn_o.clicked.connect(self._browse_out)
        oh = QHBoxLayout()
        oh.addWidget(self.edit_out, 1)
        oh.addWidget(btn_o)
        ow = QWidget()
        ow.setLayout(oh)
        form.addRow("Output dir:", ow)

        self.chk_msa = QCheckBox("Use MSA server (--use_msa_server)")
        self.chk_msa.setChecked(True)
        self.chk_msa.setToolTip("Let Boltz request an MSA from the remote server (omit custom MSA in YAML).")
        form.addRow(self.chk_msa)

        self.chk_override = QCheckBox("Override cached runs (--override)")
        form.addRow(self.chk_override)

        self.chk_potentials = QCheckBox("Use inference potentials (--use_potentials)")
        form.addRow(self.chk_potentials)

        self.combo_acc = QComboBox()
        self.combo_acc.addItems(["gpu", "cpu", "tpu"])
        self.spin_devices = QSpinBox()
        self.spin_devices.setRange(1, 16)
        self.spin_devices.setValue(1)
        adv = QHBoxLayout()
        adv.addWidget(QLabel("Accelerator:"))
        adv.addWidget(self.combo_acc)
        adv.addWidget(QLabel("Devices:"))
        adv.addWidget(self.spin_devices)
        adv.addStretch()
        form.addRow(adv)

        scroll_tune = QScrollArea()
        scroll_tune.setWidgetResizable(True)
        scroll_tune.setFrameShape(QFrame.NoFrame)
        tune_page = QWidget()
        tform = QFormLayout(tune_page)

        self.spin_recycling = QSpinBox()
        self.spin_recycling.setRange(1, 50)
        self.spin_recycling.setValue(3)
        self.spin_recycling.setToolTip("Number of structure recycling steps (Boltz default: 3).")
        tform.addRow("Recycling steps:", self.spin_recycling)

        self.spin_sampling = QSpinBox()
        self.spin_sampling.setRange(1, 9999)
        self.spin_sampling.setValue(200)
        self.spin_sampling.setToolTip("Diffusion sampling steps for the structure model (default: 200).")
        tform.addRow("Sampling steps:", self.spin_sampling)

        self.spin_diffusion = QSpinBox()
        self.spin_diffusion.setRange(1, 64)
        self.spin_diffusion.setValue(1)
        self.spin_diffusion.setToolTip("Number of diffusion samples per input (default: 1).")
        tform.addRow("Diffusion samples:", self.spin_diffusion)

        self.spin_max_parallel = QSpinBox()
        self.spin_max_parallel.setRange(1, 128)
        self.spin_max_parallel.setValue(5)
        self.spin_max_parallel.setToolTip("Maximum diffusion samples to run in parallel (Boltz default: 5).")
        tform.addRow("Max parallel samples:", self.spin_max_parallel)

        self.edit_step_scale = QLineEdit()
        self.edit_step_scale.setPlaceholderText("omit = model default (~1.5 boltz2, ~1.638 boltz1)")
        self.edit_step_scale.setToolTip(
            "Optional diffusion step scale (temperature). Empty lets Boltz use its built-in default."
        )
        tform.addRow("Step scale:", self.edit_step_scale)

        self.combo_output = QComboBox()
        self.combo_output.addItems(["mmcif", "pdb"])
        self.combo_output.setToolTip("Structure output format (default: mmcif).")
        tform.addRow("Output format:", self.combo_output)

        self.combo_model = QComboBox()
        self.combo_model.addItems(["boltz2", "boltz1"])
        self.combo_model.setToolTip("Which checkpoint family to use (default: boltz2).")
        tform.addRow("Model:", self.combo_model)

        self.spin_num_workers = QSpinBox()
        self.spin_num_workers.setRange(1, 32)
        self.spin_num_workers.setValue(2)
        self.spin_num_workers.setToolTip("DataLoader worker processes (default: 2).")
        tform.addRow("Num workers:", self.spin_num_workers)

        _prep_default = min(32, max(1, (os.cpu_count() or 4)))
        self.spin_prep_threads = QSpinBox()
        self.spin_prep_threads.setRange(1, 64)
        self.spin_prep_threads.setValue(_prep_default)
        self.spin_prep_threads.setToolTip("CPU threads for preprocessing (Boltz default: CPU count).")
        tform.addRow("Preprocessing threads:", self.spin_prep_threads)

        self.spin_seed = QSpinBox()
        self.spin_seed.setRange(-1, 2_147_483_647)
        self.spin_seed.setSpecialValueText("Random")
        self.spin_seed.setValue(-1)
        self.spin_seed.setToolTip("RNG seed; 'Random' passes no --seed (Boltz default).")
        tform.addRow("Random seed:", self.spin_seed)

        self.spin_sampling_aff = QSpinBox()
        self.spin_sampling_aff.setRange(1, 9999)
        self.spin_sampling_aff.setValue(200)
        self.spin_sampling_aff.setToolTip("Sampling steps when predicting affinity (default: 200).")
        tform.addRow("Affinity sampling steps:", self.spin_sampling_aff)

        self.spin_diffusion_aff = QSpinBox()
        self.spin_diffusion_aff.setRange(1, 64)
        self.spin_diffusion_aff.setValue(5)
        self.spin_diffusion_aff.setToolTip("Diffusion samples for affinity head (Boltz CLI default: 5).")
        tform.addRow("Affinity diffusion samples:", self.spin_diffusion_aff)

        self.chk_aff_mw = QCheckBox("Molecular-weight correction on affinity")
        self.chk_aff_mw.setToolTip("Passes --affinity_mw_correction when checked.")
        tform.addRow(self.chk_aff_mw)

        self.spin_max_msa = QSpinBox()
        self.spin_max_msa.setRange(64, 100_000)
        self.spin_max_msa.setValue(8192)
        self.spin_max_msa.setToolTip("Cap on MSA depth fed to the model (default: 8192).")
        tform.addRow("Max MSA sequences:", self.spin_max_msa)

        self.chk_subsample_msa = QCheckBox("Subsample MSA (--subsample_msa)")
        self.chk_subsample_msa.setChecked(True)
        self.chk_subsample_msa.setToolTip("When checked, passes Boltz's subsample flag (recommended).")
        tform.addRow(self.chk_subsample_msa)

        self.spin_num_sub_msa = QSpinBox()
        self.spin_num_sub_msa.setRange(64, 50_000)
        self.spin_num_sub_msa.setValue(1024)
        self.spin_num_sub_msa.setToolTip("Target MSA size after subsampling (default: 1024).")
        tform.addRow("Subsampled MSA size:", self.spin_num_sub_msa)

        self.edit_msa_url = QLineEdit()
        self.edit_msa_url.setPlaceholderText("default: https://api.colabfold.com")
        self.edit_msa_url.setToolTip('Only used when "Use MSA server" is checked.')
        tform.addRow("MSA server URL:", self.edit_msa_url)

        self.edit_msa_pairing = QLineEdit("greedy")
        self.edit_msa_pairing.setPlaceholderText("greedy or complete")
        self.edit_msa_pairing.setToolTip("MSA pairing strategy when using the remote MSA server.")
        tform.addRow("MSA pairing strategy:", self.edit_msa_pairing)

        self.edit_checkpoint = QLineEdit()
        self.edit_checkpoint.setPlaceholderText("Optional main model checkpoint file")
        self.edit_checkpoint.setToolTip("Passes --checkpoint when non-empty.")
        ck_h = QHBoxLayout()
        ck_h.addWidget(self.edit_checkpoint, 1)
        btn_ck = QPushButton("Browse…")
        btn_ck.clicked.connect(self._browse_checkpoint)
        ck_h.addWidget(btn_ck)
        ck_w = QWidget()
        ck_w.setLayout(ck_h)
        tform.addRow("Checkpoint:", ck_w)

        self.edit_affinity_ckpt = QLineEdit()
        self.edit_affinity_ckpt.setPlaceholderText("Optional affinity-head checkpoint file")
        self.edit_affinity_ckpt.setToolTip("Passes --affinity_checkpoint when non-empty.")
        ack_h = QHBoxLayout()
        ack_h.addWidget(self.edit_affinity_ckpt, 1)
        btn_ack = QPushButton("Browse…")
        btn_ack.clicked.connect(self._browse_affinity_checkpoint)
        ack_h.addWidget(btn_ack)
        ack_w = QWidget()
        ack_w.setLayout(ack_h)
        tform.addRow("Affinity checkpoint:", ack_w)

        self.edit_cache = QLineEdit()
        self.edit_cache.setPlaceholderText("omit = ~/.boltz or $BOLTZ_CACHE")
        self.edit_cache.setToolTip("Override download/model cache directory (--cache).")
        ca_h = QHBoxLayout()
        ca_h.addWidget(self.edit_cache, 1)
        btn_ca = QPushButton("Browse…")
        btn_ca.clicked.connect(self._browse_cache)
        ca_h.addWidget(btn_ca)
        ca_w = QWidget()
        ca_w.setLayout(ca_h)
        tform.addRow("Cache directory:", ca_w)

        self.edit_method = QLineEdit()
        self.edit_method.setPlaceholderText("Optional --method string")
        self.edit_method.setToolTip("Boltz experimental / alternate run method, if your build supports it.")
        tform.addRow("Method:", self.edit_method)

        self.chk_write_pae = QCheckBox("Write full PAE (npz) — --write_full_pae")
        tform.addRow(self.chk_write_pae)

        self.chk_write_pde = QCheckBox("Write full PDE (npz) — --write_full_pde")
        tform.addRow(self.chk_write_pde)

        self.chk_write_emb = QCheckBox("Write s/z embeddings (npz) — --write_embeddings")
        tform.addRow(self.chk_write_emb)

        self.chk_no_kernels = QCheckBox("Disable kernels — --no_kernels")
        tform.addRow(self.chk_no_kernels)

        scroll_tune.setWidget(tune_page)
        scroll_tune.setMinimumHeight(260)
        form.addRow("Predict tuning:", scroll_tune)

        self.edit_extra = QLineEdit()
        self.edit_extra.setPlaceholderText(
            "Extra CLI args (appended; use quotes for paths with spaces — see boltz predict --help)"
        )
        form.addRow("Extra arguments:", self.edit_extra)

        y_layout.addWidget(grp)
        self.btn_run_yaml = QPushButton("Run boltz predict")
        self.btn_run_yaml.setToolTip("Run boltz predict using the YAML path and options from this tab.")
        self.btn_run_yaml.clicked.connect(self._run_from_yaml)
        y_layout.addWidget(self.btn_run_yaml)
        tabs.addTab(tab_yaml, "YAML file")
        tabs.setTabToolTip(
            0,
            "Run boltz predict on your own YAML: set input path, output directory, run flags, and predict tuning. "
            "The command log below streams stdout/stderr while the job runs.",
        )

        # --- Tab: quick cofold ---
        tab_quick = QWidget()
        q_layout = QVBoxLayout(tab_quick)
        qg = QGroupBox("Minimal protein + ligand YAML (affinity optional)")
        qg.setToolTip(
            "Builds a small Boltz YAML with one protein chain and one ligand. "
            "Predict options and output directory come from the YAML file tab."
        )
        qf = QFormLayout(qg)
        self.edit_pid = QLineEdit("A")
        self.edit_pid.setToolTip("Chain id for the protein block in the generated YAML.")
        self.edit_lid = QLineEdit("B")
        self.edit_lid.setToolTip("Chain id for the ligand block in the generated YAML.")
        qf.addRow("Protein chain id:", self.edit_pid)
        qf.addRow("Ligand chain id:", self.edit_lid)
        self.edit_seq = QPlainTextEdit()
        self.edit_seq.setPlaceholderText("Amino-acid one-letter sequence (whitespace is stripped)")
        self.edit_seq.setMinimumHeight(120)
        self.edit_seq.setToolTip(
            "Paste a one-letter protein sequence, or use Load FASTA to read the first sequence from a .fa/.fasta file."
        )
        btn_fasta = QPushButton("Load FASTA…")
        btn_fasta.setToolTip("Read a protein FASTA file; the first sequence replaces the text below (see log if multiple records).")
        btn_fasta.clicked.connect(self._browse_fasta_protein)
        seq_row = QHBoxLayout()
        seq_row.addWidget(self.edit_seq, 1)
        seq_row.addWidget(btn_fasta)
        seq_wrap = QWidget()
        seq_wrap.setLayout(seq_row)
        qf.addRow("Protein sequence:", seq_wrap)
        self.edit_ligsmi = QLineEdit()
        self.edit_ligsmi.setPlaceholderText("Ligand SMILES (quoted safely in YAML)")
        self.edit_ligsmi.setToolTip("Ligand SMILES for the quick YAML; embedded safely as JSON in YAML.")
        qf.addRow("Ligand SMILES:", self.edit_ligsmi)
        self.chk_affinity = QCheckBox("Request binding affinity (properties.affinity)")
        self.chk_affinity.setChecked(True)
        self.chk_affinity.setToolTip("Adds a properties block; binder must be the ligand chain id.")
        qf.addRow(self.chk_affinity)
        q_layout.addWidget(qg)
        self.btn_preview = QPushButton("Preview generated YAML")
        self.btn_preview.setToolTip("Show the YAML text that would be written for the quick cofold (no run).")
        self.btn_preview.clicked.connect(self._preview_quick_yaml)
        q_layout.addWidget(self.btn_preview)
        self.btn_run_quick = QPushButton("Write temp YAML and run boltz predict")
        self.btn_run_quick.setToolTip("Write a temporary YAML from the fields above and start boltz predict.")
        self.btn_run_quick.clicked.connect(self._run_quick)
        q_layout.addWidget(self.btn_run_quick)
        tabs.addTab(tab_quick, "Quick cofold")
        tabs.setTabToolTip(
            1,
            "Enter protein sequence (paste or Load FASTA) and ligand SMILES; optional affinity. "
            "Uses the same output directory and predict tuning as the YAML file tab.",
        )

        self.progress_label = QLabel("Idle — no prediction running.")
        self.progress_label.setWordWrap(True)
        self.progress_label.setStyleSheet("color: palette(mid); padding: 2px 0;")
        root.addWidget(self.progress_label)

        log_gb = QGroupBox("Command log (verbose, timestamped)")
        log_v = QVBoxLayout(log_gb)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        apply_monospace_to_text_edit(self.log)
        log_v.addWidget(self.log)
        root.addWidget(log_gb, 1)

        bottom = QHBoxLayout()
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_proc)
        bottom.addWidget(self.btn_stop)
        bottom.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._run_from_yaml)
        make_window_minimizable(self)

    def is_predict_running(self) -> bool:
        if getattr(self, "_predict_session_active", False):
            return True
        try:
            return self._proc.state() != QProcess.NotRunning
        except RuntimeError:
            return False

    def cancel_predict(self) -> bool:
        """Kill the subprocess if running (e.g. from the Processes dialog)."""
        if not self.is_predict_running():
            return False
        self._stop_proc()
        return True

    def _notify_activity_hub(self) -> None:
        w = self._main_window
        hub = getattr(w, "background_activity", None) if w is not None else None
        if hub is not None:
            hub.notify_changed()

    def _set_main_status_now(self, text: str) -> None:
        w = self._main_window
        if w is not None and hasattr(w, "status_label"):
            w.status_label.setText(text)

    def _schedule_main_status(self, text: str) -> None:
        t = (text or "").strip()
        if len(t) > 160:
            t = t[:157] + "…"
        self._pending_main_status = t
        self._status_debounce.start(400)

    def _flush_status_to_main(self) -> None:
        if self._pending_main_status:
            self._set_main_status_now(f"Boltz-2: {self._pending_main_status}")

    def _on_proc_started(self) -> None:
        try:
            pid = int(self._proc.pid())
        except Exception:
            pid = -1
        stamp = time.strftime("%H:%M:%S")
        self.log.append(f"[{stamp}][system] boltz subprocess started (PID {pid}).")
        self.progress_label.setText(f"Running (PID {pid})… capturing stdout/stderr.")
        self._notify_activity_hub()

    def _browse_boltz_executable(self) -> None:
        filt = "Programs (*.exe);;All files (*.*)" if os.name == "nt" else "All files (*.*)"
        path, _ = QFileDialog.getOpenFileName(self, "Boltz executable", "", filt)
        if path:
            self.edit_exe.setText(path)

    def _append_failed_to_start_diagnostics(self) -> None:
        """Explain FailedToStart: GUI PATH vs terminal, and log what we tried to run."""
        stamp = time.strftime("%H:%M:%S")
        try:
            prog = (self._proc.program() or "").strip()
        except Exception:
            prog = ""
        if not prog:
            prog = (self.edit_exe.text() or "").strip() or "boltz"
        which = shutil.which("boltz")
        which_msg = repr(which) if which else "not found on PATH for this ChemManager process"
        lines = [
            f"[{stamp}][system] --- FailedToStart: what we tried ---",
            f"[{stamp}][system] Program: {prog!r}",
            f"[{stamp}][system] shutil.which('boltz') in this app: {which_msg}",
            f"[{stamp}][system] --- Typical fixes ---",
            (
                f"[{stamp}][system] 1) Set \"boltz executable\" to the full path of boltz.exe "
                f"(from an activated env, run: where boltz  →  paste that path here)."
                if os.name == "nt"
                else f"[{stamp}][system] 1) Set \"boltz executable\" to the full path (from a shell: which boltz)."
            ),
            f"[{stamp}][system] 2) Conda/venv: use …/envs/ENV_NAME/Scripts/boltz.exe (Windows) or …/envs/ENV_NAME/bin/boltz (macOS/Linux).",
            f"[{stamp}][system] 3) If the file exists but still fails: antivirus / SmartScreen may block child processes — try an exclusion or run ChemManager from that same terminal once.",
            f"[{stamp}][system] 4) Advanced: set executable to your python.exe and add in Extra args: -m boltz predict … (build the rest of the CLI yourself).",
        ]
        self.log.append("\n".join(lines) + "\n")

    def _on_proc_error(self, error: int) -> None:
        err_names = {
            QProcess.FailedToStart: "FailedToStart — program missing, not executable, or blocked",
            QProcess.Crashed: "Crashed",
            QProcess.Timedout: "Timed out",
            QProcess.ReadError: "Read error",
            QProcess.WriteError: "Write error",
            QProcess.UnknownError: "Unknown error",
        }
        msg = err_names.get(int(error), f"error {error}")
        stamp = time.strftime("%H:%M:%S")
        self._flush_stream_buffers_tail()
        self.log.append(f"\n[{stamp}][system] QProcess reported: {msg}\n")
        if int(error) == QProcess.FailedToStart:
            self._append_failed_to_start_diagnostics()
            QMessageBox.information(
                self,
                "Boltz-2 — could not start",
                "The boltz program could not be launched from ChemManager.\n\n"
                "This usually means the app does not see the same PATH as your terminal "
                "(conda/venv). Open the log below for the exact program we tried and how to fix it; "
                "use Browse… next to \"boltz executable\" to pick boltz.exe from your environment's Scripts folder.",
            )
        self._predict_session_active = False
        self._heartbeat.stop()
        self.btn_run_yaml.setEnabled(True)
        self.btn_run_quick.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._status_debounce.stop()
        self._set_main_status_now(f"Boltz-2: {msg}")
        self.progress_label.setText(f"Error — {msg}")
        self._notify_activity_hub()

    def _on_heartbeat(self) -> None:
        if not self._predict_session_active:
            self._heartbeat.stop()
            return
        elapsed = int(time.monotonic() - self._run_t0)
        try:
            st = self._proc.state()
        except RuntimeError:
            self._heartbeat.stop()
            return
        if st == QProcess.NotRunning:
            self.progress_label.setText(
                f"Starting subprocess… {elapsed}s — waiting for boltz to launch "
                "(if this never advances, check the executable path, PATH, and antivirus blocking)."
            )
            self._set_main_status_now(f"Boltz-2: launching… {elapsed}s (no child process yet)")
            return
        hint = self._last_progress_snippet[:160]
        if len(self._last_progress_snippet) > 160:
            hint += "…"
        self.progress_label.setText(
            f"Running — {elapsed}s elapsed. Latest log line: {hint or '(none yet — long init is normal)'}"
        )
        tail = hint or "no new log lines yet (long init, MSA, or buffered output is common)"
        self._set_main_status_now(f"Boltz-2: still running — {elapsed}s · {tail}")

    def _flush_stream_buffers_tail(self) -> None:
        stamp = time.strftime("%H:%M:%S")
        for ch, attr in (("stdout", "_stdout_buf"), ("stderr", "_stderr_buf")):
            tail = (getattr(self, attr, "") or "").strip("\r\n")
            setattr(self, attr, "")
            if tail.strip():
                self.log.append(f"[{stamp}][{ch}] {tail}")
                self._last_progress_snippet = tail.strip()
                self._schedule_main_status(self._last_progress_snippet)

    def _append_stream_chunk(self, channel: str, chunk: bytes) -> None:
        if not chunk:
            return
        buf_attr = "_stdout_buf" if channel == "stdout" else "_stderr_buf"
        buf = getattr(self, buf_attr) + chunk.decode("utf-8", errors="replace")
        lines = buf.split("\n")
        setattr(self, buf_attr, lines[-1])
        for line in lines[:-1]:
            line = line.rstrip("\r")
            if not line.strip():
                continue
            stamp = time.strftime("%H:%M:%S")
            tagged = f"[{stamp}][{channel}] {line}"
            self.log.append(tagged)
            self._last_progress_snippet = line.strip()
            self._schedule_main_status(self._last_progress_snippet)

    def _browse_yaml(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Boltz-2 input YAML", "", "YAML (*.yaml *.yml);;All files (*.*)"
        )
        if path:
            self.edit_yaml.setText(path)

    def _browse_out(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Output directory")
        if path:
            self.edit_out.setText(path)

    def _browse_fasta_protein(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Protein FASTA",
            "",
            "FASTA (*.fa *.fas *.fasta *.faa *.txt);;All files (*.*)",
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            QMessageBox.warning(self, "Boltz-2", f"Could not read FASTA file:\n{e}")
            return
        records = _parse_fasta_records(text)
        if not records:
            QMessageBox.information(self, "Boltz-2", "No sequences found in that FASTA file.")
            return
        if len(records) > 1:
            h0 = records[0][0]
            if len(h0) > 100:
                h0 = h0[:100] + "…"
            QMessageBox.information(
                self,
                "Boltz-2",
                f"FASTA contains {len(records)} sequences; using the first only ({h0}).\n"
                "For multiple chains, use a YAML file on the YAML file tab.",
            )
        _hdr, seq = records[0]
        seq = _one_line_sequence(seq)
        if len(seq) < 5:
            QMessageBox.warning(self, "Boltz-2", "The first FASTA sequence looks too short for a protein.")
            return
        self.edit_seq.setPlainText(seq)
        stamp = time.strftime("%H:%M:%S")
        self.log.append(f"[{stamp}][system] Loaded protein sequence from FASTA ({path}): {len(seq)} residues.")

    def _browse_checkpoint(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Boltz checkpoint",
            "",
            "Checkpoint (*.ckpt *.pt *.pth *.safetensors);;All files (*.*)",
        )
        if path:
            self.edit_checkpoint.setText(path)

    def _browse_affinity_checkpoint(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Boltz affinity checkpoint",
            "",
            "Checkpoint (*.ckpt *.pt *.pth *.safetensors);;All files (*.*)",
        )
        if path:
            self.edit_affinity_ckpt.setText(path)

    def _browse_cache(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Boltz cache directory")
        if path:
            self.edit_cache.setText(path)

    def _append_stdout(self) -> None:
        self._append_stream_chunk("stdout", bytes(self._proc.readAllStandardOutput()))

    def _append_stderr(self) -> None:
        self._append_stream_chunk("stderr", bytes(self._proc.readAllStandardError()))

    def _boltz_executable(self) -> str:
        exe = (self.edit_exe.text() or "").strip() or "boltz"
        return exe

    def _predict_cli_args(self, out_dir: str) -> list[str]:
        """Arguments after ``boltz`` for a ``predict`` run (matches upstream ``boltz predict``)."""
        args: list[str] = [
            "predict",
            "--out_dir",
            out_dir,
            "--accelerator",
            self.combo_acc.currentText(),
            "--devices",
            str(int(self.spin_devices.value())),
            "--recycling_steps",
            str(int(self.spin_recycling.value())),
            "--sampling_steps",
            str(int(self.spin_sampling.value())),
            "--diffusion_samples",
            str(int(self.spin_diffusion.value())),
            "--max_parallel_samples",
            str(int(self.spin_max_parallel.value())),
            "--sampling_steps_affinity",
            str(int(self.spin_sampling_aff.value())),
            "--diffusion_samples_affinity",
            str(int(self.spin_diffusion_aff.value())),
            "--output_format",
            self.combo_output.currentText(),
            "--num_workers",
            str(int(self.spin_num_workers.value())),
            "--model",
            self.combo_model.currentText(),
            "--max_msa_seqs",
            str(int(self.spin_max_msa.value())),
            "--num_subsampled_msa",
            str(int(self.spin_num_sub_msa.value())),
            "--preprocessing-threads",
            str(int(self.spin_prep_threads.value())),
        ]
        step = (self.edit_step_scale.text() or "").strip()
        if step:
            try:
                float(step)
            except ValueError:
                pass
            else:
                args.extend(["--step_scale", step])
        seed = int(self.spin_seed.value())
        if seed >= 0:
            args.extend(["--seed", str(seed)])
        if self.chk_msa.isChecked():
            args.append("--use_msa_server")
            url = (self.edit_msa_url.text() or "").strip()
            if url:
                args.extend(["--msa_server_url", url])
            pairing = (self.edit_msa_pairing.text() or "").strip() or "greedy"
            args.extend(["--msa_pairing_strategy", pairing])
        if self.chk_override.isChecked():
            args.append("--override")
        if self.chk_potentials.isChecked():
            args.append("--use_potentials")
        if self.chk_subsample_msa.isChecked():
            args.append("--subsample_msa")
        if self.chk_aff_mw.isChecked():
            args.append("--affinity_mw_correction")
        if self.chk_write_pae.isChecked():
            args.append("--write_full_pae")
        if self.chk_write_pde.isChecked():
            args.append("--write_full_pde")
        if self.chk_write_emb.isChecked():
            args.append("--write_embeddings")
        if self.chk_no_kernels.isChecked():
            args.append("--no_kernels")
        ck = (self.edit_checkpoint.text() or "").strip()
        if ck:
            args.extend(["--checkpoint", ck])
        aff_ck = (self.edit_affinity_ckpt.text() or "").strip()
        if aff_ck:
            args.extend(["--affinity_checkpoint", aff_ck])
        cache = (self.edit_cache.text() or "").strip()
        if cache:
            args.extend(["--cache", cache])
        method = (self.edit_method.text() or "").strip()
        if method:
            args.extend(["--method", method])
        extra = (self.edit_extra.text() or "").strip()
        if extra:
            try:
                args.extend(shlex.split(extra, posix=False))
            except ValueError:
                args.extend(extra.split())
        return args

    def _run_from_yaml(self) -> None:
        if self._proc.state() != QProcess.NotRunning:
            QMessageBox.information(self, "Boltz-2", "A prediction is already running.")
            return
        yaml_path = (self.edit_yaml.text() or "").strip()
        if not yaml_path:
            QMessageBox.warning(self, "Boltz-2", "Choose an input YAML file.")
            return
        if not Path(yaml_path).is_file():
            QMessageBox.warning(self, "Boltz-2", "Input YAML path is not a file.")
            return
        out_dir = (self.edit_out.text() or "").strip()
        if not out_dir:
            QMessageBox.warning(self, "Boltz-2", "Set an output directory.")
            return
        exe = self._boltz_executable()
        if shutil.which(exe) is None and not Path(exe).is_file():
            self.log.append(
                f"Note: '{exe}' not found on PATH — start may fail unless the path is correct.\n"
            )
        cmd = [exe] + self._predict_cli_args(out_dir) + [yaml_path]
        self._start_process(cmd)

    def _build_quick_yaml(self) -> str:
        seq = _one_line_sequence(self.edit_seq.toPlainText())
        if len(seq) < 5:
            raise ValueError("Protein sequence looks too short.")
        smi = (self.edit_ligsmi.text() or "").strip()
        if not smi:
            raise ValueError("Enter a ligand SMILES.")
        pid = (self.edit_pid.text() or "A").strip() or "A"
        lid = (self.edit_lid.text() or "B").strip() or "B"
        smi_json = json.dumps(smi)
        lines = [
            "version: 1",
            "sequences:",
            "  - protein:",
            f"      id: {pid}",
            f"      sequence: {seq}",
            "  - ligand:",
            f"      id: {lid}",
            f"      smiles: {smi_json}",
        ]
        if self.chk_affinity.isChecked():
            lines.extend(["properties:", "  - affinity:", f"      binder: {lid}"])
        return "\n".join(lines) + "\n"

    def _preview_quick_yaml(self) -> None:
        try:
            txt = self._build_quick_yaml()
        except ValueError as e:
            QMessageBox.warning(self, "Boltz-2", str(e))
            return
        self.log.setPlainText(txt)

    def _run_quick(self) -> None:
        if self._proc.state() != QProcess.NotRunning:
            QMessageBox.information(self, "Boltz-2", "A prediction is already running.")
            return
        out_dir = (self.edit_out.text() or "").strip()
        if not out_dir:
            QMessageBox.warning(self, "Boltz-2", "Set an output directory (YAML file tab).")
            return
        try:
            yaml_text = self._build_quick_yaml()
        except ValueError as e:
            QMessageBox.warning(self, "Boltz-2", str(e))
            return
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            delete=False,
            encoding="utf-8",
            newline="\n",
        )
        try:
            tmp.write(yaml_text)
            tmp.flush()
            yaml_path = tmp.name
        finally:
            tmp.close()
        self.log.append(f"Wrote temporary YAML: {yaml_path}\n")
        exe = self._boltz_executable()
        if shutil.which(exe) is None and not Path(exe).is_file():
            self.log.append(
                f"Note: '{exe}' not found on PATH — start may fail unless the path is correct.\n"
            )
        cmd = [exe] + self._predict_cli_args(out_dir) + [yaml_path]
        self._start_process(cmd)

    def _start_process(self, cmd: list[str]) -> None:
        self._stdout_buf = ""
        self._stderr_buf = ""
        self._last_progress_snippet = ""
        self._run_t0 = time.monotonic()
        self._predict_session_active = True
        self._notify_activity_hub()
        stamp = time.strftime("%H:%M:%S")
        self.log.append(f"[{stamp}][system] Full command:\n$ " + " ".join(cmd) + "\n")
        self.log.append(f"[{stamp}][system] Environment: PYTHONUNBUFFERED=1 (line-buffered Python output when applicable).")
        self.btn_run_yaml.setEnabled(False)
        self.btn_run_quick.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._status_debounce.stop()
        self.progress_label.setText("Starting subprocess…")
        self._set_main_status_now("Boltz-2: starting predict… (see Boltz-2 window for full log)")
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        self._proc.setProcessEnvironment(env)
        self._proc.setProgram(cmd[0])
        self._proc.setArguments(cmd[1:])
        self._proc.start()
        self._heartbeat.start()
        self._notify_activity_hub()

    def _on_proc_finished(self, code: int = 0, _status: int = 0) -> None:
        self._predict_session_active = False
        self._heartbeat.stop()
        self._flush_stream_buffers_tail()
        self.btn_run_yaml.setEnabled(True)
        self.btn_run_quick.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._status_debounce.stop()
        self._set_main_status_now(f"Boltz-2: finished (exit code {code}).")
        stamp = time.strftime("%H:%M:%S")
        self.log.append(f"\n[{stamp}][system] Process finished with exit code {code}.\n")
        self.progress_label.setText(f"Finished — exit code {code}. (Idle)")
        self._notify_activity_hub()

    def _stop_proc(self) -> None:
        if self._proc.state() != QProcess.NotRunning:
            self._proc.kill()
            self._heartbeat.stop()
            self._flush_stream_buffers_tail()
            stamp = time.strftime("%H:%M:%S")
            self.log.append(f"\n[{stamp}][system] Stopped by user.\n")
            self._status_debounce.stop()
            self._set_main_status_now("Boltz-2: stopped.")
            self.progress_label.setText("Stopped by user. (Idle)")
        self._notify_activity_hub()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._heartbeat.stop()
        if self._proc.state() != QProcess.NotRunning:
            self._proc.kill()
            self._proc.waitForFinished(3000)
        self._status_debounce.stop()
        if self._main_window is not None and hasattr(self._main_window, "status_label"):
            msg = self._main_window.status_label.text() or ""
            if msg.startswith("Boltz-2:"):
                self._main_window.status_label.setText("Ready")
        self._predict_session_active = False
        self._notify_activity_hub()
        super().closeEvent(event)
