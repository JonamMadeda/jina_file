import csv
import json
import os
import re
import shutil
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import Optional

import customtkinter as ctk


# ===================================================================
# Constants
# ===================================================================

STYLES = ["snake_case", "kebab-case", "camelCase", "PascalCase"]
INVALID_WIN_CHARS = set('\\/:*?"<>|')
MAX_AFFECTED_WARN = 500
BACKUP_MAX_DEPTH = 5
BACKUPS_DIR_NAME = ".jina_backups"
JOURNAL_NAME = "_journal.csv"
SNAPSHOT_NAME = "_snapshot.csv"

SYSTEM_FILE_PATTERNS: list[re.Pattern] = [
    re.compile(r'^desktop\.ini$', re.I),
    re.compile(r'^Thumbs\.db$', re.I),
    re.compile(r'^\$RECYCLE\.BIN$', re.I),
    re.compile(r'^System Volume Information$', re.I),
    re.compile(r'^\.jina_backups?$'),
    re.compile(r'^\.jina_.*'),
]


# ===================================================================
# Word splitting & case-style conversion
# ===================================================================

def splitWords(name: str) -> list[str]:
    s = name
    s = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s)
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', s)
    s = re.sub(r'[\s_\-.]', ' ', s)
    return [w for w in s.split(' ') if w]


def joinSnake(words: list[str]) -> str:
    return '_'.join(w.lower() for w in words)


def joinKebab(words: list[str]) -> str:
    return '-'.join(w.lower() for w in words)


def joinCamel(words: list[str]) -> str:
    if not words:
        return ''
    return words[0].lower() + ''.join(w.capitalize() for w in words[1:])


def joinPascal(words: list[str]) -> str:
    return ''.join(w.capitalize() for w in words)


def toCase(words: list[str], style: str) -> str:
    if style == "snake_case":
        return joinSnake(words)
    elif style == "kebab-case":
        return joinKebab(words)
    elif style == "camelCase":
        return joinCamel(words)
    elif style == "PascalCase":
        return joinPascal(words)
    return joinSnake(words)


def cleanSuffixStem(stem: str) -> str:
    return re.sub(r'_\d+$', '', stem)


def applyPrefixSuffix(stem: str, prefix: str, suffix: str) -> str:
    return f"{prefix}{stem}{suffix}"


# ===================================================================
# Mode application
# ===================================================================

def applyCaseConvert(original: str, style: str, prefix: str, suffix: str) -> str:
    p = Path(original)
    words = splitWords(p.stem)
    newStem = toCase(words, style)
    newStem = applyPrefixSuffix(newStem, prefix, suffix)
    return f"{newStem}{p.suffix}"


def applyCleanSuffix(original: str, prefix: str, suffix: str) -> str:
    p = Path(original)
    stem = cleanSuffixStem(p.stem)
    stem = applyPrefixSuffix(stem, prefix, suffix)
    return f"{stem}{p.suffix}"


def applyRegex(original: str, pattern: str, replacement: str, prefix: str, suffix: str) -> str:
    p = Path(original)
    try:
        newStem = re.sub(pattern, replacement, p.stem)
    except re.error:
        newStem = p.stem
    newStem = applyPrefixSuffix(newStem, prefix, suffix)
    return f"{newStem}{p.suffix}"


def proposedName(original: str, mode: str, style: str,
                 pattern: str, replacement: str,
                 prefix: str, suffix: str) -> str:
    if mode == "case":
        return applyCaseConvert(original, style, prefix, suffix)
    elif mode == "clean":
        return applyCleanSuffix(original, prefix, suffix)
    elif mode == "regex":
        return applyRegex(original, pattern, replacement, prefix, suffix)
    return original


# ===================================================================
# Validation helpers
# ===================================================================

def hasInvalidChars(name: str) -> bool:
    return bool(set(name) & INVALID_WIN_CHARS)


def pathLengthWarning(fullPath: str) -> Optional[str]:
    if len(fullPath) > 260:
        return f"Path exceeds 260 chars ({len(fullPath)})"
    return None


def isFileLocked(p: Path) -> bool:
    if not p.is_file():
        return False
    try:
        with p.open("ab"):
            pass
        return False
    except PermissionError:
        return True
    except OSError:
        return True


def shouldExclude(p: Path) -> bool:
    name = p.name
    for pat in SYSTEM_FILE_PATTERNS:
        if pat.match(name):
            return True
    return False


# ===================================================================
# Collision dry-run
# ===================================================================

def detectAndResolveChains(plan: list[dict]) -> list[dict]:
    srcNames: set[str] = set()
    for e in plan:
        if e["checked"]:
            srcNames.add(e["src"].name)

    fixed: list[dict] = []
    for e in plan:
        if not e["checked"]:
            fixed.append(e)
            continue
        srcName = e["src"].name
        finalName = e["final"]
        if finalName != srcName and finalName in srcNames:
            tmpName = f".__jtmp_{int(time.time())}_{srcName}"
            e["final"] = tmpName
            e["_tempHop"] = finalName
        fixed.append(e)
    return fixed


def finalizeTempHops(plan: list[dict]) -> None:
    for e in plan:
        dst = e.get("_tempHop")
        if dst:
            src = e["src"].with_name(e["final"])
            if src.exists():
                src.rename(src.with_name(dst))


# ===================================================================
# System filter + scanning
# ===================================================================

def filterItems(items: list[Path], excludeSystem: bool, excludeHidden: bool) -> list[Path]:
    out = []
    for p in items:
        if excludeSystem and shouldExclude(p):
            continue
        if excludeHidden:
            try:
                if p.name.startswith("."):
                    continue
            except Exception:
                if p.name.startswith("."):
                    continue
        out.append(p)
    return out


# ===================================================================
# Rename planning
# ===================================================================

def buildPlan(paths: list[Path], *,
              mode: str, style: str,
              pattern: str, replacement: str,
              prefix: str, suffix: str,
              checked: set[str] | None = None,
              enableChains: bool = False) -> list[dict]:
    plan = []
    used: dict[str, int] = {}

    for p in paths:
        srcStr = str(p)
        if checked is not None and srcStr not in checked:
            plan.append({"src": p, "proposed": p.name, "final": p.name, "checked": False})
            continue
        prop = proposedName(p.name, mode, style, pattern, replacement, prefix, suffix)
        if prop not in used:
            used[prop] = 0
            final = prop
        else:
            used[prop] += 1
            stem = Path(prop).stem
            ext = Path(prop).suffix
            final = f"{stem}_{used[prop]}{ext}"
        plan.append({"src": p, "proposed": prop, "final": final, "checked": True})

    changed = True
    while changed:
        changed = False
        finals: dict[str, int] = {}
        for entry in plan:
            if not entry["checked"]:
                continue
            fn = entry["final"]
            if fn not in finals:
                finals[fn] = 0
            else:
                finals[fn] += 1
                stem = Path(fn).stem
                ext = Path(fn).suffix
                newFn = f"{stem}_{finals[fn]}{ext}"
                if newFn != fn:
                    entry["final"] = newFn
                    changed = True

    if enableChains:
        plan = detectAndResolveChains(plan)
    return plan


def countAffected(plan: list[dict]) -> int:
    return sum(1 for e in plan if e["checked"] and e["final"] != e["src"].name)


def getCheckedToRename(plan: list[dict], checked: set[str]) -> list[dict]:
    return [e for e in plan
            if e["checked"] and str(e["src"]) in checked
            and e["final"] != e["src"].name]


# ===================================================================
# Rename Journal
# ===================================================================

class RenameJournal:
    def __init__(self, rootDir: Path) -> None:
        self._path = rootDir / BACKUPS_DIR_NAME / JOURNAL_NAME

    def append(self, entries: list[dict], mode: str, style: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        exists = self._path.exists()
        try:
            with open(self._path, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if not exists:
                    w.writerow(["timestamp", "original_name", "new_name",
                                "type", "mode", "style"])
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for e in entries:
                    w.writerow([ts, e["src"].name, e["final"],
                                "folder" if e["src"].is_dir() else "file", mode, style])
        except Exception:
            pass


# ===================================================================
# Snapshot
# ===================================================================

def writeSnapshot(backupDir: Path, entries: list[dict]) -> None:
    snapPath = backupDir / SNAPSHOT_NAME
    try:
        with open(snapPath, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["path", "relative_path", "is_dir", "size", "modified"])
            for e in entries:
                p = e["src"]
                try:
                    stat = p.stat()
                    w.writerow([str(p), str(p.relative_to(p.anchor)),
                                "yes" if p.is_dir() else "no",
                                stat.st_size,
                                datetime.fromtimestamp(stat.st_mtime).isoformat()])
                except Exception:
                    w.writerow([str(p), "", "unknown", 0, ""])
    except Exception:
        pass


# ===================================================================
# Backup & Undo (multi-step)
# ===================================================================

class UndoStack:
    def __init__(self, rootDir: Path, maxDepth: int = BACKUP_MAX_DEPTH) -> None:
        self._rootDir = rootDir
        self._backupsDir = rootDir / BACKUPS_DIR_NAME
        self._indexPath = self._backupsDir / "_index.json"
        self._maxDepth = maxDepth
        self._stack: list[dict] = []
        self._load()

    def _load(self) -> None:
        if self._indexPath.exists():
            try:
                data = json.loads(self._indexPath.read_text(encoding="utf-8"))
                self._stack = data.get("stack", [])
                self._maxDepth = data.get("max_depth", BACKUP_MAX_DEPTH)
            except Exception:
                self._stack = []

    def _save(self) -> None:
        self._backupsDir.mkdir(parents=True, exist_ok=True)
        try:
            self._indexPath.write_text(
                json.dumps({"max_depth": self._maxDepth, "stack": self._stack}, indent=2),
                encoding="utf-8")
        except Exception:
            pass

    @property
    def canUndo(self) -> bool:
        return len(self._stack) > 0

    @property
    def depth(self) -> int:
        return len(self._stack)

    def push(self, entries: list[dict], mode: str, style: str) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backupDir = self._backupsDir / ts
        backupDir.mkdir(parents=True, exist_ok=True)
        _setHidden(self._backupsDir)
        for e in entries:
            src = e["src"]
            try:
                rel = src.relative_to(self._rootDir)
            except ValueError:
                rel = Path(src.name)
            dst = backupDir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
        writeSnapshot(backupDir, entries)
        self._stack.append({"ts": ts, "count": len(entries), "mode": mode, "style": style})
        self._trim()
        self._save()

    def pop(self) -> Optional[dict]:
        if not self._stack:
            return None
        top = self._stack.pop()
        self._save()
        return top

    def getBackupDir(self, ts: str) -> Path:
        return self._backupsDir / ts

    def restore(self, ts: str) -> int:
        backupDir = self.getBackupDir(ts)
        if not backupDir.is_dir():
            return 0
        restored = 0
        for entryPath in backupDir.rglob("*"):
            if entryPath.is_file() and entryPath.name != SNAPSHOT_NAME:
                rel = entryPath.relative_to(backupDir)
                dst = self._rootDir / rel
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(entryPath, dst)
                    restored += 1
                except Exception:
                    pass
        return restored

    def clear(self) -> None:
        if self._backupsDir.is_dir():
            shutil.rmtree(self._backupsDir)
        self._stack = []

    def _trim(self) -> None:
        while len(self._stack) > self._maxDepth:
            old = self._stack.pop(0)
            oldDir = self.getBackupDir(old["ts"])
            if oldDir.is_dir():
                shutil.rmtree(oldDir, ignore_errors=True)


def _setHidden(p: Path) -> None:
    try:
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(p), 2)
    except Exception:
        pass


# ===================================================================
# Confirmation summary dialog
# ===================================================================

class ConfirmDialog(ctk.CTkToplevel):
    def __init__(self, parent, entries: list[dict], title: str = "Confirm Rename") -> None:
        super().__init__(parent)
        self._result = False
        self.title(title)
        self.geometry("700x500")
        self.minsize(500, 300)
        self.transient(parent)
        self.grab_set()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        textBox = ctk.CTkTextbox(self, wrap="none", font=ctk.CTkFont(size=12, family="Consolas"))
        textBox.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 4))
        hdr = f"{'Type':<8} {'Original':<40} \u2192 {'Proposed'}\n"
        hdr += "\u2500" * 90 + "\n"
        textBox.insert("end", hdr)
        for e in entries:
            tp = "folder" if e["src"].is_dir() else "file"
            textBox.insert("end", f"{tp:<8} {e['src'].name:<40} \u2192 {e['final']}\n")
            if e.get("_tempHop"):
                textBox.insert("end", f"{'':8} {'':40}   \u21aa {e['_tempHop']} (temp hop)\n")
        textBox.configure(state="disabled")

        btnRow = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        btnRow.grid(row=1, column=0, pady=(4, 12))
        btnRow.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(btnRow, text="Cancel", width=110, command=self._cancel).grid(row=0, column=0, padx=(0, 10))
        ctk.CTkButton(btnRow, text="Confirm", width=110, command=self._confirm).grid(row=0, column=1, padx=(10, 0))
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.wait_window()

    def _confirm(self) -> None:
        self._result = True
        self.destroy()

    def _cancel(self) -> None:
        self.destroy()

    @property
    def result(self) -> bool:
        return self._result


# ===================================================================
# Helpers
# ===================================================================

def indicatorText(p: Path) -> str:
    return "FOLDER" if p.is_dir() else "FILE"


def indicatorColor() -> tuple[str, str]:
    if ctk.get_appearance_mode() == "Dark":
        return "#6fa8dc", "#8f8f8f"
    return "#1a6dc4", "#606060"


# ===================================================================
# Application
# ===================================================================

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class JinaFileApp(ctk.CTk):

    @staticmethod
    def _rowColors() -> tuple[str, str]:
        return ("#2d2d44", "#24243a") if ctk.get_appearance_mode() == "Dark" else ("#ffffff", "#f0f0f0")

    @staticmethod
    def _changedColor() -> str:
        return "#7fdb7f" if ctk.get_appearance_mode() == "Dark" else "#1b8a1b"

    @staticmethod
    def _warnColor() -> str:
        return "#f7d44a" if ctk.get_appearance_mode() == "Dark" else "#a06700"

    @staticmethod
    def _extColor() -> str:
        return "#6f6f6f" if ctk.get_appearance_mode() == "Dark" else "#999999"

    def __init__(self) -> None:
        super().__init__()
        self.title("jina_file")
        self.minsize(860, 640)
        self.after(50, lambda: self.state("zoomed"))

        self._targetDir: Path | None = None
        self._plan: list[dict] = []
        self._running = False
        self._abortEvent = threading.Event()
        self._undoStack: UndoStack | None = None

        self._mode: str = "case"
        self._style: str = "snake_case"
        self._pattern: str = ""
        self._replacement: str = ""
        self._prefix: str = ""
        self._suffix: str = ""

        self._sortCol: str | None = None
        self._sortAsc: bool = True
        self._filterText: str = ""
        self._checked: set[str] = set()
        self._allChecked: bool = True

        self._buildMenu()
        self._buildUi()
        self._setAppIcon()
        self.bind("<Control-v>", lambda e: self._pasteDir())

    # -- app icon --------------------------------------------------------

    def _setAppIcon(self) -> None:
        try:
            from PIL import Image, ImageDraw, ImageFont
            size = 64
            img = Image.new("RGBA", (size, size), (26, 26, 46, 255))
            draw = ImageDraw.Draw(img)
            font = None
            for n in ["segoeuib.ttf", "segoeui.ttf", "arialbd.ttf", "arial.ttf", "calibrib.ttf"]:
                try:
                    font = ImageFont.truetype(n, 28)
                    break
                except Exception:
                    continue
            if font is None:
                font = ImageFont.load_default()
            b = draw.textbbox((0, 0), "JF", font=font)
            x = (size - (b[2] - b[0])) // 2 - b[0]
            y = (size - (b[3] - b[1])) // 2 - b[1] - 1
            draw.text((x, y), "JF", fill=(200, 200, 210, 255), font=font)
            icoPath = Path(os.environ.get("TEMP", ".")) / ".jina_file_icon.ico"
            img.save(icoPath, format="ICO", sizes=[(size, size), (32, 32), (16, 16)])
            self.iconbitmap(default=str(icoPath))
        except Exception:
            pass

    # ================================================================
    # Menu bar
    # ================================================================

    def _buildMenu(self) -> None:
        menuKw = dict(bg="#2b2b2b", fg="white", activebackground="#404060",
                      activeforeground="white", relief="flat", bd=0,
                      font=("Segoe UI", 12))

        menubar = tk.Menu(self, **menuKw)

        # --- File ---
        fileM = tk.Menu(menubar, tearoff=0, **menuKw)
        fileM.add_command(label="Select Folder...", command=self._browseDir, accelerator="Ctrl+V")
        fileM.add_separator()
        fileM.add_command(label="Export CSV...", command=self._exportCsv)
        fileM.add_separator()
        fileM.add_command(label="Exit", command=self.quit)
        menubar.add_cascade(label="File", menu=fileM)

        # --- Mode ---
        modeM = tk.Menu(menubar, tearoff=0, **menuKw)
        self._modeVar = tk.StringVar(value="case")
        modeM.add_radiobutton(label="Case Conversion", value="case",
                              variable=self._modeVar, command=self._onModeMenu)

        styleM = tk.Menu(modeM, tearoff=0, **menuKw)
        self._styleVar = tk.StringVar(value="snake_case")
        for s in STYLES:
            styleM.add_radiobutton(label=s, value=s, variable=self._styleVar,
                                   command=self._onStyleMenu)
        modeM.add_cascade(label="Style", menu=styleM)

        modeM.add_separator()
        modeM.add_radiobutton(label="Clean Suffixes", value="clean",
                              variable=self._modeVar, command=self._onModeMenu)
        modeM.add_radiobutton(label="Regex Rename", value="regex",
                              variable=self._modeVar, command=self._onModeMenu)
        menubar.add_cascade(label="Mode", menu=modeM)

        # --- Theme ---
        themeM = tk.Menu(menubar, tearoff=0, **menuKw)
        self._themeVar = tk.StringVar(value="dark")
        themeM.add_radiobutton(label="Dark", value="dark",
                               variable=self._themeVar, command=self._onThemeMenu)
        themeM.add_radiobutton(label="Light", value="light",
                               variable=self._themeVar, command=self._onThemeMenu)
        menubar.add_cascade(label="Theme", menu=themeM)

        # --- Help ---
        helpM = tk.Menu(menubar, tearoff=0, **menuKw)
        helpM.add_command(label="About jina_file", command=self._showAbout)
        menubar.add_cascade(label="Help", menu=helpM)

        self.configure(menu=menubar)

    # ================================================================
    # UI construction
    # ================================================================

    def _buildUi(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._buildTopFrame()      # row 0
        self._buildOptionsBar()    # row 1
        self._buildRegexFrame()    # row 2 — hidden by default
        self._buildTable()         # row 3
        self._buildBottomFrame()   # row 4

    # -- row 0: path + browse ------------------------------------------

    def _buildTopFrame(self) -> None:
        f = ctk.CTkFrame(self, corner_radius=8, height=42)
        f.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 4))
        f.grid_columnconfigure(0, weight=1)
        f.grid_propagate(False)

        self._pathVar = ctk.StringVar(value="")
        entry = ctk.CTkEntry(f, textvariable=self._pathVar,
                             placeholder_text="Select a folder or paste path (Ctrl+V) ...",
                             height=28)
        entry.grid(row=0, column=0, sticky="ew", padx=(12, 8), pady=7)

        browseBtn = ctk.CTkButton(f, text="Browse", width=100, height=30,
                                  command=self._browseDir)
        browseBtn.grid(row=0, column=1, padx=(8, 12), pady=7)

    # -- row 1: options bar --------------------------------------------

    def _buildOptionsBar(self) -> None:
        bar = ctk.CTkFrame(self, corner_radius=8, height=36)
        bar.grid(row=1, column=0, sticky="ew", padx=14, pady=(4, 0))
        bar.grid_columnconfigure(4, weight=1)
        bar.grid_propagate(False)

        # mode label
        self._modeLabel = ctk.CTkLabel(bar, text=self._modeLabelText(),
                                       font=ctk.CTkFont(size=11, weight="bold"))
        self._modeLabel.grid(row=0, column=0, padx=(12, 10), pady=6, sticky="w")

        # separator
        ctk.CTkLabel(bar, text="|", font=ctk.CTkFont(size=11),
                     text_color="#555").grid(row=0, column=1, padx=0, pady=6)

        # prefix / suffix
        self._prefixEntry = ctk.CTkEntry(bar, width=90, height=24,
                                         placeholder_text="pre_")
        self._prefixEntry.grid(row=0, column=2, padx=(10, 4), pady=6, sticky="w")

        self._suffixEntry = ctk.CTkEntry(bar, width=90, height=24,
                                         placeholder_text="_suf")
        self._suffixEntry.grid(row=0, column=3, padx=(4, 12), pady=6, sticky="w")

        # recursive checkbox
        self._recursiveVar = ctk.BooleanVar(value=False)
        recCheck = ctk.CTkCheckBox(bar, text="Subfolders", height=22,
                                   variable=self._recursiveVar,
                                   font=ctk.CTkFont(size=10))
        recCheck.grid(row=0, column=4, padx=(0, 12), pady=6, sticky="w")

        # filter
        filterLabel = ctk.CTkLabel(bar, text="Filter:",
                                   font=ctk.CTkFont(size=10, weight="bold"))
        filterLabel.grid(row=0, column=5, padx=(0, 4), pady=6, sticky="w")

        self._filterEntry = ctk.CTkEntry(bar, width=140, height=24,
                                         placeholder_text="type to filter")
        self._filterEntry.grid(row=0, column=6, padx=(0, 6), pady=6, sticky="w")
        self._filterEntry.bind("<KeyRelease>", lambda e: self._applyFilter())

        # scan btn
        scanBtn = ctk.CTkButton(bar, text="Scan", width=55, height=24,
                                font=ctk.CTkFont(size=10),
                                command=self._scan)
        scanBtn.grid(row=0, column=7, padx=(6, 10), pady=6)

        # select all
        self._selectAllVar = ctk.BooleanVar(value=True)
        self._selectAllCb = ctk.CTkCheckBox(bar, text="All", height=22,
                                            variable=self._selectAllVar,
                                            command=self._onSelectAll,
                                            font=ctk.CTkFont(size=10))
        self._selectAllCb.grid(row=0, column=8, padx=(0, 8), pady=6)

        self._selCountLabel = ctk.CTkLabel(bar, text="",
                                           font=ctk.CTkFont(size=10))
        self._selCountLabel.grid(row=0, column=9, padx=(0, 12), pady=6, sticky="e")

    # -- row 2: regex frame --------------------------------------------

    def _buildRegexFrame(self) -> None:
        self._regexFrame = ctk.CTkFrame(self, corner_radius=8, height=32)
        self._regexFrame.grid(row=2, column=0, sticky="ew", padx=14, pady=(4, 0))
        self._regexFrame.grid_columnconfigure((1, 3), weight=1)
        self._regexFrame.grid_propagate(False)

        ctk.CTkLabel(self._regexFrame, text="Find:",
                     font=ctk.CTkFont(size=10)).grid(row=0, column=0, padx=(12, 4), pady=5, sticky="w")
        self._findEntry = ctk.CTkEntry(self._regexFrame, height=22,
                                       placeholder_text=r"(\d+)_old")
        self._findEntry.grid(row=0, column=1, padx=(0, 12), pady=5, sticky="ew")

        ctk.CTkLabel(self._regexFrame, text="Replace:",
                     font=ctk.CTkFont(size=10)).grid(row=0, column=2, padx=(6, 4), pady=5, sticky="w")
        self._replEntry = ctk.CTkEntry(self._regexFrame, height=22,
                                       placeholder_text=r"new_\1")
        self._replEntry.grid(row=0, column=3, padx=(0, 12), pady=5, sticky="ew")

        self._regexFrame.grid_remove()

    # -- row 3: preview table ------------------------------------------

    def _buildTable(self) -> None:
        container = ctk.CTkFrame(self, corner_radius=8)
        container.grid(row=3, column=0, sticky="nsew", padx=14, pady=(4, 4))
        container.grid_rowconfigure(1, weight=1)
        container.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(container, corner_radius=0, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 0))
        hdr.grid_columnconfigure((1, 2), weight=1)

        ctk.CTkLabel(hdr, text="Type", font=ctk.CTkFont(size=11, weight="bold"),
                     cursor="hand2").grid(row=0, column=0, padx=(4, 2), pady=2)
        hdrLabels = {
            "type": ctk.CTkLabel(hdr, text="Type", font=ctk.CTkFont(size=11, weight="bold"),
                                 cursor="hand2"),
            "original": ctk.CTkLabel(hdr, text="Original Name",
                                     font=ctk.CTkFont(size=12, weight="bold"), cursor="hand2"),
            "proposed": ctk.CTkLabel(hdr, text="Proposed Name",
                                     font=ctk.CTkFont(size=12, weight="bold"), cursor="hand2"),
        }
        hdrLabels["type"].grid(row=0, column=0, padx=(4, 2), pady=2)
        hdrLabels["type"].bind("<Button-1>", lambda e: self._sortBy("type"))
        hdrLabels["original"].grid(row=0, column=1, padx=4, pady=2, sticky="w")
        hdrLabels["original"].bind("<Button-1>", lambda e: self._sortBy("original"))
        hdrLabels["proposed"].grid(row=0, column=2, padx=4, pady=2, sticky="w")
        hdrLabels["proposed"].bind("<Button-1>", lambda e: self._sortBy("proposed"))

        self._tableBody = ctk.CTkScrollableFrame(container, corner_radius=4)
        self._tableBody.grid(row=1, column=0, sticky="nsew", padx=6, pady=(2, 6))
        self._tableBody.grid_columnconfigure((1, 2), weight=1)

    # -- row 3: bottom bar -------------------------------------------

    def _buildBottomFrame(self) -> None:
        f = ctk.CTkFrame(self, corner_radius=8, height=50)
        f.grid(row=4, column=0, sticky="ew", padx=14, pady=(6, 14))
        f.grid_columnconfigure(0, weight=1)
        f.grid_propagate(False)

        self._progress = ctk.CTkProgressBar(f, mode="determinate", height=8)
        self._progress.set(0)
        self._progress.grid(row=0, column=0, columnspan=5, sticky="ew",
                            padx=14, pady=(8, 4))

        row = ctk.CTkFrame(f, corner_radius=0, fg_color="transparent")
        row.grid(row=1, column=0, columnspan=5, sticky="ew", padx=14, pady=(0, 8))

        self._statusVar = ctk.StringVar(value="Ready")
        ctk.CTkLabel(row, textvariable=self._statusVar, anchor="w",
                     font=ctk.CTkFont(size=11)).grid(row=0, column=0, sticky="w")

        self._abortBtn = ctk.CTkButton(row, text="Abort", width=80, height=28,
                                       fg_color="#993333", hover_color="#cc4444",
                                       state="disabled", command=self._abortOp)
        self._abortBtn.grid(row=0, column=1, padx=(12, 6))

        self._undoBtn = ctk.CTkButton(row, text="Undo", width=80, height=28,
                                      state="disabled", command=self._undo)
        self._undoBtn.grid(row=0, column=2, padx=(6, 6))

        self._actionBtn = ctk.CTkButton(row, text="Execute", width=140, height=30,
                                        font=ctk.CTkFont(size=12, weight="bold"),
                                        command=self._runAction)
        self._actionBtn.grid(row=0, column=3, padx=(6, 0))

    # ================================================================
    # Menu callbacks
    # ================================================================

    def _modeOptLabel(self) -> str:
        return {
            "case": "Case Conversion",
            "clean": "Clean Suffixes",
            "regex": "Regex Rename",
        }.get(self._mode, "Case Conversion")

    def _modeLabelText(self) -> str:
        return f"{self._modeOptLabel()}  |  {self._style}"

    def _onModeMenu(self) -> None:
        self._mode = self._modeVar.get()
        if self._mode == "regex":
            self._regexFrame.grid()
        else:
            self._regexFrame.grid_remove()
        self._modeLabel.configure(text=self._modeLabelText())
        self._actionBtn.configure(text=f"Execute ({self._modeOptLabel()})")
        self._scan()

    def _onStyleMenu(self) -> None:
        self._style = self._styleVar.get()
        self._modeLabel.configure(text=self._modeLabelText())
        self._scan()

    def _onThemeMenu(self) -> None:
        val = self._themeVar.get()
        newMode = "Light" if val == "light" else "Dark"
        ctk.set_appearance_mode(newMode)
        self._rebuildTable()

    def _showAbout(self) -> None:
        messagebox.showinfo("About jina_file",
                            "jina_file v1.0.0\n\n"
                            "Batch rename files and folders\n"
                            "to snake_case and other naming styles.\n\n"
                            "Built with Python, CustomTkinter, and PyInstaller.")

    # ================================================================
    # Directory selection
    # ================================================================

    def _browseDir(self) -> None:
        d = filedialog.askdirectory(title="Select a folder")
        if d:
            self._setDir(Path(d))

    def _pasteDir(self) -> None:
        try:
            raw = self.clipboard_get().strip().strip('"')
            p = Path(raw)
            if p.is_dir():
                self._setDir(p)
        except Exception:
            pass

    def _setDir(self, p: Path) -> None:
        self._targetDir = p
        self._pathVar.set(str(p.resolve()))
        self._undoStack = UndoStack(p)
        self._syncUndo()
        self._scan()

    # ================================================================
    # Scan
    # ================================================================

    def _readOptions(self) -> tuple:
        return (self._prefixEntry.get().strip(), self._suffixEntry.get().strip(),
                self._findEntry.get() if self._mode == "regex" else "",
                self._replEntry.get() if self._mode == "regex" else "")

    def _scan(self) -> None:
        if self._targetDir is None:
            return
        self._statusVar.set("Scanning ...")
        self._progress.set(0)
        self._plan = []
        self._checked.clear()
        self._allChecked = True

        target = self._targetDir
        recursive = self._recursiveVar.get()
        pfx, sfx, pat, repl = self._readOptions()

        try:
            raw = sorted(target.rglob("*")) if recursive else sorted(target.iterdir())
            raw = [p for p in raw if p.parent.resolve() != p.resolve()]
            items = filterItems(raw, excludeSystem=True, excludeHidden=True)
        except PermissionError as e:
            messagebox.showerror("Error", f"Cannot read folder:\n{e}")
            self._statusVar.set("Scan failed")
            self._rebuildTable()
            return
        except Exception as e:
            messagebox.showerror("Error", str(e))
            self._rebuildTable()
            return

        if not items:
            self._statusVar.set("No items found.")
            self._rebuildTable()
            return

        plan = buildPlan(items, mode=self._mode, style=self._style,
                         pattern=pat, replacement=repl,
                         prefix=pfx, suffix=sfx, enableChains=True)
        self._plan = plan
        self._checked = {str(e["src"]) for e in plan if e["checked"]}
        self._allChecked = True
        affected = countAffected(plan)
        self._summaryVar = f"{len(plan)} item(s), {affected} will change"
        self._statusVar.set(f"Scanned {len(plan)} item(s)")
        self._sortCol = None
        self._applyFilter()

    _summaryVar: str = ""

    # ================================================================
    # Filter / sort
    # ================================================================

    def _applyFilter(self) -> None:
        self._filterText = self._filterEntry.get().strip().lower()
        self._rebuildTable()

    def _sortBy(self, col: str) -> None:
        if self._sortCol == col:
            self._sortAsc = not self._sortAsc
        else:
            self._sortCol = col
            self._sortAsc = True
        self._rebuildTable()

    def _getDisplayPlan(self) -> list[dict]:
        items = list(self._plan)
        if self._filterText:
            txt = self._filterText
            items = [e for e in items
                     if txt in e["src"].name.lower() or txt in e["final"].lower()]
        if self._sortCol == "original":
            items.sort(key=lambda e: e["src"].name.lower(), reverse=not self._sortAsc)
        elif self._sortCol == "proposed":
            items.sort(key=lambda e: e["final"].lower(), reverse=not self._sortAsc)
        elif self._sortCol == "type":
            items.sort(key=lambda e: (not e["src"].is_dir(), e["src"].name.lower()),
                       reverse=not self._sortAsc)
        return items

    # ================================================================
    # Table rendering
    # ================================================================

    def _rebuildTable(self) -> None:
        self._clearTable()
        display = self._getDisplayPlan()
        if not display:
            return

        colors = self._rowColors()
        fCol, _ = indicatorColor()
        changedColor = self._changedColor()
        warnColor = self._warnColor()
        greyExt = self._extColor()

        checkedCount = sum(1 for e in display if str(e["src"]) in self._checked)
        totalCount = len(display)
        self._selCountLabel.configure(text=f"{checkedCount}/{totalCount}")

        for idx, entry in enumerate(display):
            bg = colors[idx % 2]
            row = ctk.CTkFrame(self._tableBody, corner_radius=0, fg_color=bg)
            row.pack(fill="x")
            row.grid_columnconfigure((1, 2), weight=1)

            srcPath: Path = entry["src"]
            srcKey = str(srcPath)
            isChecked = srcKey in self._checked
            finalName = entry["final"]
            changed = isChecked and finalName != srcPath.name

            # checkbox
            cbVar = ctk.BooleanVar(value=isChecked)
            cb = ctk.CTkCheckBox(row, text="", width=22, height=18,
                                 variable=cbVar,
                                 command=lambda k=srcKey, v=cbVar: self._onCheck(k, v))
            cb.grid(row=0, column=0, padx=(6, 4), pady=3)

            # type
            tpText = indicatorText(srcPath)
            tpColor = fCol if srcPath.is_dir() else "gray"
            ctk.CTkLabel(row, text=tpText, anchor="w",
                         font=ctk.CTkFont(size=10),
                         text_color=tpColor).grid(row=0, column=1, padx=(4, 6), pady=3, sticky="w")

            # warnings
            warnings = []
            if hasInvalidChars(finalName):
                warnings.append("invalid chars")
            wl = pathLengthWarning(str(srcPath.with_name(finalName)))
            if wl:
                warnings.append("path too long")
            if isChecked and finalName != srcPath.name:
                if srcPath.with_name(finalName).exists():
                    warnings.append("collision")

            warnText = "; ".join(warnings)
            origCol = 1
            if warnText:
                ctk.CTkLabel(row, text="\u26a0", anchor="w",
                             font=ctk.CTkFont(size=10),
                             text_color=warnColor).grid(row=0, column=2, padx=(4, 0), pady=3, sticky="w")
                ctk.CTkLabel(row, text=warnText, anchor="w",
                             font=ctk.CTkFont(size=8),
                             text_color=warnColor).grid(row=0, column=2, padx=(16, 6), pady=3, sticky="w")
                origCol = 2

            # original name
            ctk.CTkLabel(row, text=srcPath.name, anchor="w",
                         font=ctk.CTkFont(size=12)).grid(
                row=0, column=origCol, padx=6, pady=3, sticky="w")
            propCol = origCol + 1

            # proposed name (stem + extension lock)
            pStem = Path(finalName).stem
            pExt = Path(finalName).suffix
            pf = ctk.CTkFrame(row, corner_radius=0, fg_color=bg)
            pf.grid(row=0, column=propCol, padx=6, pady=3, sticky="w")

            sc = changedColor if changed else None
            sf = ctk.CTkFont(size=12, weight="bold") if changed else ctk.CTkFont(size=12)
            ctk.CTkLabel(pf, text=pStem, anchor="w", font=sf,
                         text_color=sc).grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(pf, text=f"{pExt} \U0001f512", anchor="w",
                         font=ctk.CTkFont(size=12),
                         text_color=greyExt).grid(row=0, column=1, padx=(0, 4), sticky="w")

    def _clearTable(self) -> None:
        for w in self._tableBody.winfo_children():
            w.destroy()

    def _onCheck(self, key: str, var: ctk.BooleanVar) -> None:
        if var.get():
            self._checked.add(key)
        else:
            self._checked.discard(key)
        self._rebuildTable()

    def _onSelectAll(self) -> None:
        display = self._getDisplayPlan()
        if self._selectAllVar.get():
            for e in display:
                self._checked.add(str(e["src"]))
        else:
            for e in display:
                self._checked.discard(str(e["src"]))
        self._rebuildTable()

    # ================================================================
    # Export CSV/JSON
    # ================================================================

    def _exportCsv(self) -> None:
        if not self._plan:
            messagebox.showinfo("Export", "Nothing to export. Scan a folder first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("JSON files", "*.json")])
        if not path:
            return
        display = self._getDisplayPlan()

        if path.lower().endswith(".json"):
            data = [{"original": e["src"].name, "proposed": e["final"],
                     "type": "folder" if e["src"].is_dir() else "file",
                     "willChange": e["final"] != e["src"].name} for e in display]
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=2)
            except Exception as e:
                messagebox.showerror("Export Error", str(e))
            return

        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["Type", "Original Name", "Proposed Name", "Will Change"])
                for e in display:
                    w.writerow(["folder" if e["src"].is_dir() else "file",
                                e["src"].name, e["final"],
                                "yes" if e["final"] != e["src"].name else "no"])
        except Exception as e:
            messagebox.showerror("Export Error", str(e))
            return
        self._statusVar.set(f"Exported to {path.name}")

    # ================================================================
    # Undo
    # ================================================================

    def _syncUndo(self) -> None:
        if self._undoStack and self._undoStack.canUndo:
            self._undoBtn.configure(state="normal")
        else:
            self._undoBtn.configure(state="disabled")

    def _undo(self) -> None:
        if self._undoStack is None or not self._undoStack.canUndo:
            return
        top = self._undoStack.pop()
        if top is None:
            return
        ts = top["ts"]
        ok = messagebox.askyesno("Undo",
                                 f"Restore backup from {ts} ({top['count']} items)?")
        if not ok:
            self._undoStack.push([], top["mode"], top["style"])
            self._syncUndo()
            return
        self._statusVar.set("Restoring backup ...")
        self._progress.set(0)
        self._actionBtn.configure(state="disabled")
        self._undoBtn.configure(state="disabled")
        Thread(target=self._doUndo, args=(ts,), daemon=True).start()

    def _doUndo(self, ts: str) -> None:
        assert self._undoStack is not None
        restored = self._undoStack.restore(ts)
        bdir = self._undoStack.getBackupDir(ts)
        if bdir.is_dir():
            shutil.rmtree(bdir, ignore_errors=True)
        self._actionBtn.configure(state="normal")
        self._syncUndo()
        self._progress.set(1)
        self._statusVar.set(f"Undone \u2014 {restored} item(s) restored")
        self.after(0, self._scan)
        self.after(300, lambda: messagebox.showinfo("Undo", f"Restored {restored} item(s)."))

    # ================================================================
    # Execute rename
    # ================================================================

    def _runAction(self) -> None:
        if self._running:
            return
        toRename = getCheckedToRename(self._plan, self._checked)
        if not toRename:
            display = self._getDisplayPlan()
            checkedCount = sum(1 for e in display if str(e["src"]) in self._checked)
            if checkedCount == 0:
                messagebox.showinfo("Nothing Selected", "No items are selected.")
            else:
                messagebox.showinfo("Nothing to Rename",
                                    "Selected items already have the correct naming.")
            return

        if len(toRename) > MAX_AFFECTED_WARN:
            if not messagebox.askyesno("Large Rename",
                                       f"Rename {len(toRename)} items?\nThis may take a while."):
                return

        dlg = ConfirmDialog(self, toRename)
        if not dlg.result:
            return

        locked = [e for e in toRename if isFileLocked(e["src"])]
        if locked:
            msg = f"{len(locked)} file(s) in use, will be skipped:\n"
            msg += "\n".join(f"  - {e['src'].name}" for e in locked[:10])
            if len(locked) > 10:
                msg += f"\n  ... and {len(locked) - 10} more"
            messagebox.showwarning("Files in Use", msg)

        self._running = True
        self._abortEvent.clear()
        self._actionBtn.configure(state="disabled")
        self._abortBtn.configure(state="normal")
        self._progress.set(0)
        self._statusVar.set("Backing up ...")
        Thread(target=self._doRename, args=(toRename, locked), daemon=True).start()

    def _doRename(self, toRename: list[dict], locked: list[dict]) -> None:
        assert self._targetDir is not None and self._undoStack is not None
        backupEntries = [e for e in toRename if e not in locked]
        if backupEntries:
            try:
                self._undoStack.push(backupEntries, self._mode, self._style)
            except Exception as e:
                self._statusVar.set(f"Backup failed: {e}")
                self._running = False
                self._actionBtn.configure(state="normal")
                self._abortBtn.configure(state="disabled")
                return

        self._statusVar.set("Renaming ...")
        total = len(toRename)
        errors = 0
        skipped = len(locked)

        for i, entry in enumerate(toRename):
            if self._abortEvent.is_set():
                self._statusVar.set("Aborted by user")
                break
            src = entry["src"]
            if entry in locked:
                skipped += 1
                self._updateProgress((i + 1) / total)
                continue
            dstName = entry.get("_tempHop") or entry["final"]
            dst = src.with_name(dstName)
            counter = 1
            while dst.exists() and dst != src:
                stem = Path(dstName).stem
                ext = Path(dstName).suffix
                dst = src.with_name(f"{stem}_{counter}{ext}")
                counter += 1
            try:
                src.rename(dst)
            except Exception as e:
                errors += 1
                self._statusVar.set(f"Error: {e}")
            self._updateProgress((i + 1) / total)

        if not self._abortEvent.is_set():
            finalizeTempHops(toRename)

        done = [e for e in toRename if e not in locked and e["src"].name != e["final"]]
        if done:
            try:
                RenameJournal(self._targetDir).append(done, self._mode, self._style)
            except Exception:
                pass

        self._running = False
        self._actionBtn.configure(state="normal")
        self._abortBtn.configure(state="disabled")
        self._syncUndo()

        if self._abortEvent.is_set():
            msg = f"Aborted \u2014 renamed {total - errors - skipped} item(s)."
        elif errors:
            msg = f"Renamed {total - errors - skipped} item(s) with {errors} error(s)."
        else:
            msg = f"All {total - skipped} item(s) renamed successfully!"
        self._progress.set(1)
        self._statusVar.set(msg)
        self.after(0, self._scan)
        if not self._abortEvent.is_set() and errors == 0 and skipped == 0:
            self.after(300, lambda: messagebox.showinfo("jina_file", msg))

    def _abortOp(self) -> None:
        self._abortEvent.set()
        self._statusVar.set("Aborting ...")

    def _updateProgress(self, value: float) -> None:
        self.after(0, lambda: self._progress.set(value))


# ===================================================================
# Entry point
# ===================================================================

if __name__ == "__main__":
    app = JinaFileApp()
    app.mainloop()
