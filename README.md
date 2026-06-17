# jina_file

Batch rename files and folders in a directory — snake_case, kebab-case, camelCase, PascalCase, regex, clean suffixes, prefix/suffix injection, and more.

## Features

- **Case Styles** — snake_case, kebab-case, camelCase, PascalCase
- **Clean Suffixes** — strip trailing `_v2`, `-copy`, `(1)` etc.
- **Regex Rename** — find & replace with full regex support
- **Prefix / Suffix** — inject text before or after the filename
- **Preview Table** — see every proposed name before executing; sortable, filterable columns
- **Select / Deselect** — per-row checkboxes or Select All
- **Recursive** — optionally include subfolders
- **Undo** — multi-step undo via timestamped snapshots (`.jina_backups/`)
- **Pre-rename journal** — CSV export of the rename plan
- **Collision & Error Warnings** — inline indicators for name collisions, invalid chars, path length
- **Dark / Light theme** — toggle from the menu bar
- **Standalone .exe** — packaged with PyInstaller; no Python required

## Download

Grab the latest installer (`jina_file_setup.exe`) from the [Releases page](https://github.com/JonamMadeda/jina_file/releases).

## Usage

1. **Select a folder** — Browse or paste a path
2. **Choose a mode** — Case Conversion, Clean Suffixes, or Regex Rename
3. **Configure options** — pick a style, enter prefix/suffix, set filter
4. **Scan** — preview all affected items
5. **Execute** — run the rename (a confirmation dialog shows the full plan)

## Building from source

```bash
pip install customtkinter pillow pyinstaller
python build_exe.bat
```

The script auto-generates the icon and produces `jina_file.exe` via PyInstaller.

## License

MIT
