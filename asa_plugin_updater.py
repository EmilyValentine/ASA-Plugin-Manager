import tkinter as tk
from tkinter import filedialog, messagebox
import os
import re
import shutil
import threading
import json
import glob
import zipfile
import tempfile
import urllib.request
import urllib.error
import webbrowser
from datetime import datetime

APP_NAME = "ASA Plugin Updater"
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".asa_plugin_updater.json")
PRESERVE_FILES = {"config.json"}
PRESERVE_PATTERNS = ("*.db", "*.sqlite", "*.sqlite3", "*.dat")
GITHUB_API = "https://api.github.com/repos/{owner}/{repo}/releases/latest"

def matches_preserve(name):
    import fnmatch
    if name in PRESERVE_FILES:
        return True
    for p in PRESERVE_PATTERNS:
        if fnmatch.fnmatch(name, p):
            return True
    return False

def load_config():
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        cfg = {}
    cfg.setdefault("plugins_folder", "")
    cfg.setdefault("maps", [])
    cfg.setdefault("plugin_meta", {})   # per-plugin: notes, update_url, last_tag
    return cfg

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass  # non-fatal: settings just will not persist this session

def parse_github_repo(url):
    """Extract (owner, repo) from a GitHub URL, or None."""
    if not url:
        return None
    m = re.search(r"github\.com[/:]([\w.\-]+)/([\w.\-]+)", url.strip())
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    repo = re.sub(r"\.git$", "", repo)
    return owner, repo

def http_get_json(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": APP_NAME,
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

def http_download(url, dest_path, progress_cb=None):
    req = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest_path, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress_cb and total:
                progress_cb(done, total)

class UnsafeZipError(Exception):
    """Raised when a zip contains an entry that would extract outside
    the intended destination folder (a "zip slip" path traversal)."""
    pass

def safe_extract_zip(zip_path, extract_dir):
    """Extract a zip file, rejecting any entry whose resolved path would
    land outside extract_dir. Raises zipfile.BadZipFile for a corrupt
    zip, or UnsafeZipError if a path-traversal entry is found. Nothing
    is extracted if any entry is unsafe; the check runs before writing
    any files."""
    os.makedirs(extract_dir, exist_ok=True)
    base = os.path.normpath(extract_dir)
    with zipfile.ZipFile(zip_path) as z:
        for member in z.namelist():
            resolved = os.path.normpath(os.path.join(base, member))
            if resolved != base and not resolved.startswith(base + os.sep):
                raise UnsafeZipError(member)
        z.extractall(extract_dir)

def find_plugin_root(extract_dir, plugin_name):
    """Locate the folder inside an extracted zip that is the actual plugin.
    Handles zips where the correctly named folder is nested inside a
    differently named zip or wrapper folder."""
    target_dll = plugin_name.lower() + ".dll"
    name_lower = plugin_name.lower()

    # 1. A directory named exactly like the plugin, anywhere in the tree
    for root, dirs, files in os.walk(extract_dir):
        for d in dirs:
            if d.lower() == name_lower:
                return os.path.join(root, d)

    # 2. Any directory containing <PluginName>.dll
    for root, dirs, files in os.walk(extract_dir):
        for f in files:
            if f.lower() == target_dll:
                return root

    # 3. Single top-level directory: assume it is the plugin
    entries = [e for e in os.scandir(extract_dir)]
    dirs = [e for e in entries if e.is_dir()]
    files = [e for e in entries if e.is_file()]
    if len(dirs) == 1 and not files:
        return dirs[0].path

    # 4. Files at the root containing any .dll: root is the plugin
    if any(f.name.lower().endswith(".dll") for f in files):
        return extract_dir

    return None

def find_plugin_root_strict(extract_dir, plugin_name):
    """Like find_plugin_root but only accepts a real match against the
    given plugin_name (an identically named folder, or a matching
    <plugin_name>.dll). Used when testing a zip against a list of
    already-known plugin names, where the generic single-folder /
    any-dll fallbacks in find_plugin_root would wrongly claim a match
    for the first name tried regardless of the zip's actual contents."""
    name_lower = plugin_name.lower()
    target_dll = name_lower + ".dll"
    for root, dirs, files in os.walk(extract_dir):
        for d in dirs:
            if d.lower() == name_lower:
                return os.path.join(root, d)
    for root, dirs, files in os.walk(extract_dir):
        for f in files:
            if f.lower() == target_dll:
                return root
    return None

def guess_plugin_name_from_zip(zip_filename):
    """Derive a likely plugin name from a zip's filename by stripping a
    trailing version-like suffix, e.g. 'ArkShopUI1.8A.zip' -> 'ArkShopUI'."""
    base = os.path.splitext(os.path.basename(zip_filename))[0]
    stripped = re.sub(r"[-_ ]*v?[0-9]+(?:[.\-][0-9]+)*\s*[a-zA-Z]?$", "", base).strip()
    return stripped or base

def extract_zip_into_plugins_folder(zip_path, plugins_folder):
    """Extract a zip sitting directly in the PLUGINS folder into its own
    correctly named subfolder, matching it against plugins already
    tracked where possible so the destination folder name and casing
    stays consistent with what is deployed to the maps. Handles zips
    whose internal folder name does not match the zip's own filename.
    Deletes the zip after a successful extraction.
    Returns (plugin_name, message) on success, or (None, message) on
    failure; nothing is deleted on failure."""
    existing_names = [d.name for d in os.scandir(plugins_folder) if d.is_dir()]
    tmp = tempfile.mkdtemp(prefix="asa_zip_")
    try:
        try:
            safe_extract_zip(zip_path, tmp)
        except zipfile.BadZipFile:
            return None, f"{os.path.basename(zip_path)}: not a valid zip file."
        except UnsafeZipError:
            return None, (f"{os.path.basename(zip_path)}: this zip contains unsafe file paths "
                          f"and was not extracted. Skipped for safety.")

        # Prefer matching a plugin name we already have a folder for.
        # Strict matching only here: the generic fallbacks in
        # find_plugin_root would otherwise wrongly claim a match for
        # whichever existing name happens to be checked first.
        target_name, target_root = None, None
        for name in existing_names:
            root = find_plugin_root_strict(tmp, name)
            if root:
                target_name, target_root = name, root
                break

        # Otherwise guess a name from the zip's own filename. Full
        # fallback logic is fine here since there is no existing
        # folder to mismatch against.
        if not target_root:
            guess = guess_plugin_name_from_zip(zip_path)
            root = find_plugin_root(tmp, guess)
            if root:
                target_name, target_root = guess, root

        if not target_root:
            return None, (f"{os.path.basename(zip_path)}: could not find a plugin folder "
                          f"inside this zip. Extract it manually and rename the folder "
                          f"to match the plugin name.")

        dest = os.path.join(plugins_folder, target_name)
        os.makedirs(dest, exist_ok=True)
        for root_dir, _, files in os.walk(target_root):
            for f in files:
                src_file = os.path.join(root_dir, f)
                rel = os.path.relpath(src_file, target_root)
                out = os.path.join(dest, rel)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                shutil.copy2(src_file, out)

        os.remove(zip_path)
        return target_name, f"Extracted {os.path.basename(zip_path)} into {target_name}."
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

TARGET_SUFFIX_PARTS = ["shootergame", "binaries", "win64", "arkapi", "plugins"]
SKIP_SCAN_DIR_NAMES = {"content", "saved", "mods", ".git", "steamapps", "redist", "logs"}

def find_plugins_folders(root, max_results=500):
    """Recursively search under root for any folder whose path ends with
    .../ShooterGame/Binaries/Win64/ArkApi/Plugins (case-insensitive),
    regardless of the server manager or folder naming above it. Prunes
    large, irrelevant subtrees (game content, mods, etc.) for speed."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_SCAN_DIR_NAMES]
        norm = dirpath.replace("\\", "/").lower()
        parts = [seg for seg in norm.split("/") if seg]
        if (len(parts) >= len(TARGET_SUFFIX_PARTS)
                and parts[-len(TARGET_SUFFIX_PARTS):] == TARGET_SUFFIX_PARTS):
            found.append(os.path.normpath(dirpath))
            dirnames[:] = []  # do not descend further once matched
            if len(found) >= max_results:
                break
    return found

def read_plugin_version(plugin_path):
    """Look for PluginInfo.json in the plugin folder and pull out a
    version string. Different authors use different key names, so try
    the common ones in order. Some authors also ship a separate 'Tag'
    field for hotfix letters (e.g. Version 1.8 + Tag 'A' meaning '1.8A')
    rather than folding it into the version string itself, so that is
    combined in here too when present."""
    info_path = os.path.join(plugin_path, "PluginInfo.json")
    if not os.path.isfile(info_path):
        return None
    try:
        with open(info_path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    version = None
    for key in ("Version", "PluginVersion", "version", "pluginVersion", "VERSION"):
        if key in data and data[key] not in (None, ""):
            version = str(data[key])
            break
    if version is None:
        return None

    for key in ("Tag", "VersionTag", "tag"):
        if key in data and data[key] not in (None, ""):
            tag = str(data[key]).strip()
            if tag:
                version = f"{version}{tag}"
            break

    return version

def parse_version(v):
    """Parse a version string into (numeric_tuple, letter_suffix) so that
    '1.39' -> ((1, 39), '') and '1.8a' -> ((1, 8), 'a'). Handles an
    optional leading 'v' and optional space before a trailing letter
    suffix (e.g. '1.8 A'). Falls back to any digit groups found if the
    string does not match the expected shape. Returns None if nothing
    numeric is found at all."""
    if v is None:
        return None
    s = str(v).strip()
    m = re.match(r"^\s*v?([0-9]+(?:\.[0-9]+)*)\s*([a-zA-Z]*)\s*$", s)
    if m:
        nums = tuple(int(p) for p in m.group(1).split("."))
        suffix = m.group(2).lower()
        return (nums, suffix)
    parts = re.findall(r"\d+", s)
    if not parts:
        return None
    return (tuple(int(p) for p in parts), "")

def is_newer_version(candidate_version, installed_version):
    """True if candidate_version is a strictly higher version than
    installed_version. Numeric components are compared as integers
    rather than decimals (so '1.39' correctly beats '1.4'), and a
    letter suffix such as '1.8a' is treated as newer than the plain
    '1.8' it patches, with suffixes compared alphabetically."""
    a = parse_version(candidate_version)
    b = parse_version(installed_version)
    if a is None or b is None:
        return False
    (a_nums, a_suffix), (b_nums, b_suffix) = a, b
    length = max(len(a_nums), len(b_nums))
    a_nums = a_nums + (0,) * (length - len(a_nums))
    b_nums = b_nums + (0,) * (length - len(b_nums))
    if a_nums != b_nums:
        return a_nums > b_nums
    return a_suffix > b_suffix

def get_last_updated_text(folder_path):
    """Newest file modified time under folder_path, formatted for display."""
    try:
        newest = 0
        for root, _, files in os.walk(folder_path):
            for f in files:
                mt = os.path.getmtime(os.path.join(root, f))
                newest = max(newest, mt)
        if newest:
            return "updated " + datetime.fromtimestamp(newest).strftime("%d/%m/%Y %H:%M")
    except OSError:
        pass
    return "no files"

# ── Colour tokens (grey/black palette) ──────────────────────
BG          = "#0a0a0a"
BG2         = "#111111"
GLASS       = "#1a1a1a"
GLASS_EDGE  = "#2a2a2a"
GLASS_HIGH  = "#333333"
NEU_BASE    = "#161616"
NEU_DARK    = "#050505"
ACCENT_DIM  = "#2a2a2a"
TEAL        = "#e0e0e0"
TEAL_DIM    = "#1e1e1e"
TEXT        = "#f0f0f0"
TEXT2       = "#888888"
TEXT3       = "#444444"
DANGER      = "#cc4444"
WARN        = "#c8993a"
GOOD        = "#5aa66a"
FIELD_BG    = "#0f0f0f"

def glass_frame(parent, **kw):
    outer = tk.Frame(parent, bg=GLASS_EDGE, **kw)
    tk.Frame(outer, bg=GLASS_HIGH, height=1).pack(fill="x")
    inner = tk.Frame(outer, bg=GLASS)
    inner.pack(fill="both", expand=True, padx=1, pady=(0, 1))
    return outer, inner

def neu_entry(parent, textvariable=None, width=None, font=None):
    shell = tk.Frame(parent, bg=NEU_DARK, padx=1, pady=1)
    inner = tk.Frame(shell, bg=FIELD_BG, padx=1, pady=1)
    inner.pack(fill="both", expand=True)
    kw = dict(bg=FIELD_BG, fg=TEXT, insertbackground=TEXT,
              relief="flat", bd=0, font=font or ("Segoe UI", 10),
              highlightthickness=0)
    if textvariable:
        kw["textvariable"] = textvariable
    if width:
        kw["width"] = width
    e = tk.Entry(inner, **kw)
    e.pack(fill="both", expand=True, ipady=5)
    return shell, e

def pill_button(parent, text, bg, fg, command, font=None, padx=14, pady=7):
    shell = tk.Frame(parent, bg=bg, padx=1, pady=1)
    btn = tk.Button(shell, text=text, bg=bg, fg=fg, activebackground=bg,
                    activeforeground=fg, relief="flat", bd=0, cursor="hand2",
                    font=font or ("Segoe UI", 9, "bold"),
                    padx=padx, pady=pady, command=command)
    btn.pack()
    def on_enter(e): btn.configure(bg=_lighten(bg))
    def on_leave(e): btn.configure(bg=bg)
    btn.bind("<Enter>", on_enter)
    btn.bind("<Leave>", on_leave)
    return shell, btn

def _lighten(hex_col):
    hex_col = hex_col.lstrip("#")
    r, g, b = int(hex_col[0:2], 16), int(hex_col[2:4], 16), int(hex_col[4:6], 16)
    r, g, b = min(255, r + 30), min(255, g + 30), min(255, b + 30)
    return f"#{r:02x}{g:02x}{b:02x}"

def section_label(parent, text):
    row = tk.Frame(parent, bg=GLASS)
    row.pack(fill="x", pady=(12, 6))
    tk.Frame(row, bg="#555555", width=3).pack(side="left", fill="y", padx=(0, 8))
    tk.Label(row, text=text, bg=GLASS, fg=TEXT,
             font=("Segoe UI", 9, "bold")).pack(side="left")
    return row


class BigCheck(tk.Frame):
    def __init__(self, parent, variable, command=None, bg=NEU_BASE):
        super().__init__(parent, bg=bg)
        self.variable = variable
        self.command = command
        self.box = tk.Label(self, width=2, font=("Segoe UI", 12, "bold"),
                            bg=FIELD_BG, fg=TEAL, cursor="hand2",
                            relief="flat", bd=0)
        self.box.pack(ipadx=1, ipady=1)
        self.box.bind("<Button-1>", self._toggle)
        self._draw()

    def _toggle(self, event=None):
        self.variable.set(not self.variable.get())
        self._draw()
        if self.command:
            self.command()

    def _draw(self):
        if self.variable.get():
            self.box.config(text="✔", fg=TEAL, bg="#222222")
        else:
            self.box.config(text=" ", bg=FIELD_BG)


class PluginRow:
    """One detected plugin: name, last updated, expandable notes and
    GitHub update source with check and download."""
    def __init__(self, parent, app, plugin_name, plugin_path):
        self.app = app
        self.name = plugin_name
        self.path = plugin_path
        meta = app.cfg["plugin_meta"].get(plugin_name, {})
        self.expanded = False

        self.frame = tk.Frame(parent, bg=NEU_BASE, padx=8, pady=6)
        self.frame.pack(fill="x", pady=(0, 5))

        head = tk.Frame(self.frame, bg=NEU_BASE)
        head.pack(fill="x")

        self.toggle_btn = tk.Label(head, text="+", width=2, bg=FIELD_BG, fg=TEXT,
                                   font=("Segoe UI", 11, "bold"), cursor="hand2")
        self.toggle_btn.pack(side="left", padx=(0, 8))
        self.toggle_btn.bind("<Button-1>", self._toggle)

        tk.Label(head, text=plugin_name, bg=NEU_BASE, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")

        version = read_plugin_version(plugin_path)
        if version:
            tk.Label(head, text=f"v{version}", bg=NEU_BASE, fg=TEXT2,
                     font=("Segoe UI", 8)).pack(side="left", padx=(6, 0))

        self.status_lbl = tk.Label(head, text="", bg=NEU_BASE, fg=TEXT2,
                                   font=("Segoe UI", 8))
        self.status_lbl.pack(side="right", padx=(6, 0))

        tk.Label(head, text=self._last_updated_text(), bg=NEU_BASE, fg=TEXT3,
                 font=("Segoe UI", 8)).pack(side="right")

        # expandable details
        self.details = tk.Frame(self.frame, bg=NEU_BASE)

        # update source url
        u_row = tk.Frame(self.details, bg=NEU_BASE)
        u_row.pack(fill="x", pady=(8, 4))
        tk.Label(u_row, text="Update source (GitHub URL):", bg=NEU_BASE, fg=TEXT2,
                 font=("Segoe UI", 8)).pack(anchor="w")
        u_inner = tk.Frame(self.details, bg=NEU_BASE)
        u_inner.pack(fill="x", pady=(0, 4))
        self.url_var = tk.StringVar(value=meta.get("update_url", ""))
        url_shell, url_e = neu_entry(u_inner, textvariable=self.url_var, font=("Segoe UI", 8))
        url_shell.pack(side="left", fill="x", expand=True)
        url_e.bind("<FocusOut>", lambda e: self._save_meta())

        _, chk_btn = pill_button(u_inner, "Check", ACCENT_DIM, TEXT,
                                 self._check_update, font=("Segoe UI", 8), padx=10, pady=3)
        chk_btn.master.pack(side="left", padx=(6, 0))

        self.dl_holder = tk.Frame(self.details, bg=NEU_BASE)
        self.dl_holder.pack(fill="x")

        # website / download page link
        w_row = tk.Frame(self.details, bg=NEU_BASE)
        w_row.pack(fill="x", pady=(6, 4))
        tk.Label(w_row, text="Website / download page:", bg=NEU_BASE, fg=TEXT2,
                 font=("Segoe UI", 8)).pack(anchor="w")
        w_inner = tk.Frame(self.details, bg=NEU_BASE)
        w_inner.pack(fill="x", pady=(0, 4))
        self.website_var = tk.StringVar(value=meta.get("website_url", ""))
        web_shell, web_e = neu_entry(w_inner, textvariable=self.website_var, font=("Segoe UI", 8))
        web_shell.pack(side="left", fill="x", expand=True)
        web_e.bind("<FocusOut>", lambda e: self._save_meta())
        _, web_btn = pill_button(w_inner, "Open", ACCENT_DIM, TEXT,
                                 lambda: self._open_link(self.website_var.get()),
                                 font=("Segoe UI", 8), padx=10, pady=3)
        web_btn.master.pack(side="left", padx=(6, 0))

        # discord channel link
        d_row = tk.Frame(self.details, bg=NEU_BASE)
        d_row.pack(fill="x", pady=(6, 4))
        tk.Label(d_row, text="Discord release channel:", bg=NEU_BASE, fg=TEXT2,
                 font=("Segoe UI", 8)).pack(anchor="w")
        d_inner = tk.Frame(self.details, bg=NEU_BASE)
        d_inner.pack(fill="x", pady=(0, 4))
        self.discord_var = tk.StringVar(value=meta.get("discord_url", ""))
        disc_shell, disc_e = neu_entry(d_inner, textvariable=self.discord_var, font=("Segoe UI", 8))
        disc_shell.pack(side="left", fill="x", expand=True)
        disc_e.bind("<FocusOut>", lambda e: self._save_meta())
        _, disc_btn = pill_button(d_inner, "Open", ACCENT_DIM, TEXT,
                                  lambda: self._open_link(self.discord_var.get()),
                                  font=("Segoe UI", 8), padx=10, pady=3)
        disc_btn.master.pack(side="left", padx=(6, 0))

        # notes
        tk.Label(self.details, text="Notes:", bg=NEU_BASE, fg=TEXT2,
                 font=("Segoe UI", 8)).pack(anchor="w", pady=(6, 2))
        n_shell = tk.Frame(self.details, bg=NEU_DARK, padx=1, pady=1)
        n_shell.pack(fill="x", pady=(0, 4))
        self.notes = tk.Text(n_shell, bg=FIELD_BG, fg=TEXT, height=3,
                             relief="flat", bd=0, font=("Segoe UI", 9),
                             insertbackground=TEXT, wrap="word",
                             highlightthickness=0, padx=6, pady=4)
        self.notes.pack(fill="x")
        self.notes.insert("1.0", meta.get("notes", ""))
        self.notes.bind("<FocusOut>", lambda e: self._save_meta())

        if meta.get("last_tag"):
            self.status_lbl.config(text=f"installed: {meta['last_tag']}")

    def _last_updated_text(self):
        return get_last_updated_text(self.path)

    def _toggle(self, event=None):
        self.expanded = not self.expanded
        if self.expanded:
            self.details.pack(fill="x")
            self.toggle_btn.config(text="−")
        else:
            self.details.pack_forget()
            self.toggle_btn.config(text="+")

    def _save_meta(self):
        self.app.cfg["plugin_meta"].setdefault(self.name, {})
        entry = self.app.cfg["plugin_meta"][self.name]
        entry["update_url"] = self.url_var.get().strip()
        entry["website_url"] = self.website_var.get().strip()
        entry["discord_url"] = self.discord_var.get().strip()
        entry["notes"] = self.notes.get("1.0", "end").strip()
        save_config(self.app.cfg)

    def _open_link(self, url):
        url = (url or "").strip()
        if not url:
            messagebox.showinfo(APP_NAME, "No link set for this plugin yet.")
            return
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        webbrowser.open(url)

    def _set_status(self, text, colour=TEXT2):
        self.app.after(0, lambda: self.status_lbl.config(text=text, fg=colour))

    # ── GitHub update check ──────────────────────────────────
    def _check_update(self):
        self._save_meta()
        repo = parse_github_repo(self.url_var.get())
        if not repo:
            messagebox.showwarning(APP_NAME,
                "Enter a GitHub repository URL first, e.g.\n"
                "https://github.com/Author/PluginName")
            return
        self._set_status("checking...", TEXT2)
        threading.Thread(target=self._check_worker, args=(repo,), daemon=True).start()

    def _check_worker(self, repo):
        owner, name = repo
        try:
            data = http_get_json(GITHUB_API.format(owner=owner, repo=name))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                self._set_status("no releases found", WARN)
            elif e.code == 403:
                self._set_status("rate limited, try later", WARN)
            else:
                self._set_status(f"error {e.code}", DANGER)
            return
        except (urllib.error.URLError, TimeoutError):
            self._set_status("network error", DANGER)
            return

        tag = data.get("tag_name") or data.get("name") or "unknown"
        published = (data.get("published_at") or "")[:10]
        assets = data.get("assets") or []
        zip_assets = [a for a in assets if a.get("name", "").lower().endswith(".zip")]
        dl_url = zip_assets[0]["browser_download_url"] if zip_assets else data.get("zipball_url")
        dl_name = zip_assets[0]["name"] if zip_assets else f"{name}-{tag}.zip"

        installed = self.app.cfg["plugin_meta"].get(self.name, {}).get("last_tag")
        if installed == tag:
            self._set_status(f"up to date ({tag})", GOOD)
            self.app.after(0, self._clear_dl_button)
            return

        self._set_status(f"update available: {tag} ({published})", WARN)
        self.app.after(0, lambda: self._show_dl_button(tag, dl_url, dl_name))

    def _clear_dl_button(self):
        for w in self.dl_holder.winfo_children():
            w.destroy()

    def _show_dl_button(self, tag, url, dl_name):
        self._clear_dl_button()
        _, btn = pill_button(self.dl_holder, f"⭳  Download and install {tag}",
                             TEAL_DIM, TEAL,
                             lambda: self._download(tag, url, dl_name),
                             font=("Segoe UI", 8, "bold"), padx=10, pady=4)
        btn.master.pack(anchor="w", pady=(2, 4))

    def _download(self, tag, url, dl_name):
        if not url:
            messagebox.showwarning(APP_NAME, "No downloadable zip found on this release.")
            return
        self._set_status(f"downloading {tag}...", TEXT2)
        threading.Thread(target=self._download_worker,
                         args=(tag, url, dl_name), daemon=True).start()

    def _download_worker(self, tag, url, dl_name):
        tmp = tempfile.mkdtemp(prefix="asa_dl_")
        try:
            zip_path = os.path.join(tmp, dl_name)
            def prog(done, total):
                pct = int(done * 100 / total)
                self._set_status(f"downloading {tag}... {pct}%", TEXT2)
            try:
                http_download(url, zip_path, prog)
            except (urllib.error.URLError, TimeoutError):
                self._set_status("download failed", DANGER)
                return

            extract_dir = os.path.join(tmp, "x")
            try:
                safe_extract_zip(zip_path, extract_dir)
            except zipfile.BadZipFile:
                self._set_status("bad zip file", DANGER)
                return
            except UnsafeZipError:
                self._set_status("unsafe zip contents, not extracted", DANGER)
                self.app._log(f"{self.name}: the downloaded zip contained unsafe file paths "
                              f"and was not extracted.", "err")
                return

            src = find_plugin_root(extract_dir, self.name)
            if not src:
                self._set_status("could not find plugin folder in zip", DANGER)
                self.app._log(f"{self.name}: no folder containing {self.name}.dll "
                              f"found inside the downloaded zip.", "err")
                return

            # copy into the PLUGINS drop folder (overwrite existing files)
            for root, _, files in os.walk(src):
                for f in files:
                    rel = os.path.relpath(os.path.join(root, f), src)
                    out = os.path.join(self.path, rel)
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    shutil.copy2(os.path.join(root, f), out)

            self.app.cfg["plugin_meta"].setdefault(self.name, {})["last_tag"] = tag
            save_config(self.app.cfg)
            self._set_status(f"installed {tag} into PLUGINS", GOOD)
            self.app._log(f"{self.name}: downloaded {tag} into the PLUGINS folder. "
                          f"Run Dry Run then Update Plugins to deploy it to your maps.", "ok")
            self.app.after(0, self.app.refresh_plugins)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class MapRow:
    def __init__(self, parent, app, name="", path="", enabled=True, on_remove=None):
        self.app = app
        self.on_remove = on_remove
        self.expanded = False

        self.frame = tk.Frame(parent, bg=NEU_BASE, pady=6, padx=8)
        self.frame.pack(fill="x", pady=(0, 5))

        top = tk.Frame(self.frame, bg=NEU_BASE)
        top.pack(fill="x")

        self.toggle_btn = tk.Label(top, text="+", width=2, bg=FIELD_BG, fg=TEXT,
                                   font=("Segoe UI", 11, "bold"), cursor="hand2")
        self.toggle_btn.pack(side="left", padx=(0, 6))
        self.toggle_btn.bind("<Button-1>", self._toggle)

        self.enabled_var = tk.BooleanVar(value=enabled)
        self.check = BigCheck(top, self.enabled_var, command=app._save)
        self.check.pack(side="left", padx=(0, 6))

        self.name_var = tk.StringVar(value=name)
        name_shell, name_e = neu_entry(top, textvariable=self.name_var,
                                       width=20, font=("Segoe UI", 9))
        name_shell.pack(side="left", padx=(0, 6))
        name_e.bind("<FocusOut>", lambda e: app._save())

        if not name:
            name_e.insert(0, "Map name...")
            name_e.config(fg=TEXT3)
            def _focus_in(e):
                if name_e.get() == "Map name...":
                    name_e.delete(0, "end")
                    name_e.config(fg=TEXT)
            def _focus_out(e):
                if not name_e.get():
                    name_e.insert(0, "Map name...")
                    name_e.config(fg=TEXT3)
                app._save()
            name_e.bind("<FocusIn>", _focus_in)
            name_e.bind("<FocusOut>", _focus_out)

        _, del_btn = pill_button(top, "✕", "#2a1a1a", DANGER,
                                 self._remove, font=("Segoe UI", 8), padx=8, pady=3)
        del_btn.master.pack(side="right")

        bot = tk.Frame(self.frame, bg=NEU_BASE)
        bot.pack(fill="x", pady=(5, 0))

        self.path_var = tk.StringVar(value=path)
        path_shell, path_e = neu_entry(bot, textvariable=self.path_var,
                                       font=("Segoe UI", 9))
        path_shell.pack(side="left", fill="x", expand=True)
        path_e.bind("<FocusOut>", lambda e: app._save())

        _, br_btn = pill_button(bot, "Browse...", ACCENT_DIM, TEXT,
                                self._browse, font=("Segoe UI", 8), padx=10, pady=4)
        br_btn.master.pack(side="left", padx=(6, 0))

        # expandable: installed plugins on this map
        self.details = tk.Frame(self.frame, bg=NEU_BASE)

        d_head = tk.Frame(self.details, bg=NEU_BASE)
        d_head.pack(fill="x", pady=(8, 4))
        tk.Label(d_head, text="INSTALLED PLUGINS", bg=NEU_BASE, fg=TEXT3,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        _, ref_btn = pill_button(d_head, "Refresh", ACCENT_DIM, TEXT,
                                 self._refresh_installed_plugins,
                                 font=("Segoe UI", 7), padx=8, pady=2)
        ref_btn.master.pack(side="right")

        self._installed_container = tk.Frame(self.details, bg=NEU_BASE)
        self._installed_container.pack(fill="x")

    def _toggle(self, event=None):
        self.expanded = not self.expanded
        if self.expanded:
            self.details.pack(fill="x")
            self.toggle_btn.config(text="−")
            self._refresh_installed_plugins()
        else:
            self.details.pack_forget()
            self.toggle_btn.config(text="+")

    def _refresh_installed_plugins(self):
        for w in self._installed_container.winfo_children():
            w.destroy()
        path = self.path_var.get().strip()
        if not path or not os.path.isdir(path):
            tk.Label(self._installed_container,
                     text="Set a valid Plugins folder path above to see what is installed.",
                     bg=NEU_BASE, fg=TEXT3, font=("Segoe UI", 8),
                     wraplength=280, justify="left").pack(anchor="w")
            return
        subdirs = sorted([d for d in os.scandir(path) if d.is_dir()],
                         key=lambda d: d.name.lower())
        if not subdirs:
            tk.Label(self._installed_container, text="No plugins found in this folder.",
                     bg=NEU_BASE, fg=TEXT3, font=("Segoe UI", 8)).pack(anchor="w")
            return
        plugins_folder = self.app.plugins_var.get().strip()
        for d in subdirs:
            row = tk.Frame(self._installed_container, bg=NEU_BASE)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=d.name, bg=NEU_BASE, fg=TEXT,
                     font=("Segoe UI", 8, "bold")).pack(side="left")
            version = read_plugin_version(d.path)
            if version:
                tk.Label(row, text=f"v{version}", bg=NEU_BASE, fg=TEXT2,
                         font=("Segoe UI", 8)).pack(side="left", padx=(6, 0))

            # compare against the version sitting in the PLUGINS drop folder
            if plugins_folder:
                source_path = os.path.join(plugins_folder, d.name)
                if os.path.isdir(source_path):
                    source_version = read_plugin_version(source_path)
                    if is_newer_version(source_version, version):
                        tk.Label(row, text="Newer version available", bg=NEU_BASE,
                                 fg=WARN, font=("Segoe UI", 8, "bold")).pack(side="left", padx=(8, 0))

            tk.Label(row, text=get_last_updated_text(d.path), bg=NEU_BASE, fg=TEXT3,
                     font=("Segoe UI", 8)).pack(side="right")

    def _browse(self):
        d = filedialog.askdirectory(title="Select the Plugins folder for this map")
        if d:
            self.path_var.set(d)
            self.app._save()

    def _remove(self):
        if self.on_remove:
            self.on_remove(self)

    def get_data(self):
        name = self.name_var.get().strip()
        if name == "Map name...":
            name = ""
        return {"name": name, "path": self.path_var.get().strip(),
                "enabled": self.enabled_var.get()}


class ASAPluginUpdater(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1200x760")
        self.minsize(950, 600)
        self.configure(bg=BG)
        self.resizable(True, True)
        self._set_icon()
        self.cfg = load_config()
        self._map_rows = []
        self._plugin_rows = []
        self._build_ui()
        self._load_saved()
        self.refresh_plugins()

    def _set_icon(self):
        import sys
        try:
            base_path = sys._MEIPASS
        except AttributeError:
            base_path = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(base_path, "icon.ico")
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
            except Exception:
                pass

    def _build_ui(self):
        hdr = tk.Frame(self, bg=BG2, pady=0)
        hdr.pack(fill="x")
        tk.Frame(hdr, bg="#404040", height=1).pack(fill="x")
        inner_hdr = tk.Frame(hdr, bg=BG2, pady=12)
        inner_hdr.pack(fill="x", padx=20)
        tk.Label(inner_hdr, text="⬡", bg=BG2, fg="#888888",
                 font=("Segoe UI", 18, "bold")).pack(side="left", padx=(0, 8))
        tk.Label(inner_hdr, text="ASA Plugin Updater", bg=BG2, fg=TEXT,
                 font=("Segoe UI", 14, "bold")).pack(side="left")
        tk.Label(inner_hdr,
                 text="Deploy plugin updates across your maps. Configs always preserved.",
                 bg=BG2, fg=TEXT3, font=("Segoe UI", 9)).pack(side="left", padx=16)

        paned = tk.PanedWindow(self, orient="horizontal", bg=BG,
                               sashwidth=8, sashrelief="flat",
                               bd=0, opaqueresize=True)
        paned.pack(fill="both", expand=True, padx=12, pady=12)

        left_outer = tk.Frame(paned, bg=BG)
        paned.add(left_outer, minsize=400, stretch="always")

        canvas = tk.Canvas(left_outer, bg=BG, highlightthickness=0)
        vsb = tk.Scrollbar(left_outer, orient="vertical", command=canvas.yview,
                           bg=BG, troughcolor=BG2, activebackground="#555555")
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self._left = tk.Frame(canvas, bg=BG)
        win_id = canvas.create_window((0, 0), window=self._left, anchor="nw")

        self._left.bind("<Configure>",
                        lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(win_id, width=e.width))
        canvas.bind("<Enter>",
                    lambda e: canvas.bind_all("<MouseWheel>",
                        lambda ev: canvas.yview_scroll(int(-1 * (ev.delta / 120)), "units")))
        canvas.bind("<Leave>",
                    lambda e: canvas.unbind_all("<MouseWheel>"))

        right_outer, right = glass_frame(paned)
        paned.add(right_outer, minsize=300, stretch="always")

        tk.Label(right, text="LOG", bg=GLASS, fg=TEXT2,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
        tk.Frame(right, bg=GLASS_EDGE, height=1).pack(fill="x", padx=12, pady=(4, 8))

        self.log = tk.Text(right, bg="#0d0d0d", fg=TEXT,
                           font=("Consolas", 9), wrap="word",
                           state="disabled", relief="flat", bd=0,
                           padx=10, pady=8,
                           insertbackground=TEXT,
                           selectbackground="#333333")
        log_vsb = tk.Scrollbar(right, command=self.log.yview,
                               bg=GLASS, troughcolor="#0d0d0d")
        self.log.configure(yscrollcommand=log_vsb.set)
        log_vsb.pack(side="right", fill="y", padx=(0, 4), pady=(0, 8))
        self.log.pack(fill="both", expand=True, padx=(8, 0), pady=(0, 8))

        self.log.tag_config("head", foreground="#f0f0f0", font=("Consolas", 9, "bold"))
        self.log.tag_config("ok",   foreground="#aaaaaa")
        self.log.tag_config("keep", foreground="#444444")
        self.log.tag_config("warn", foreground=WARN)
        self.log.tag_config("err",  foreground=DANGER, font=("Consolas", 9, "bold"))
        self.log.tag_config("copy", foreground="#888888")
        self.log.tag_config("info", foreground="#cccccc")
        self.log.tag_config("dim",  foreground="#444444")

        self._build_left()

    def _build_left(self):
        p = self._left

        # ── 1. PLUGINS FOLDER ────────────────────────────────
        _, c1 = glass_frame(p)
        c1.master.pack(fill="x", pady=(0, 10))
        section_label(c1, "1  PLUGINS FOLDER")
        tk.Label(c1, text="Folder containing your extracted plugin updates.\n"
                 "Each subfolder must match the plugin name exactly (e.g. ArkShop).",
                 bg=GLASS, fg=TEXT2, font=("Segoe UI", 8),
                 justify="left").pack(anchor="w", padx=12, pady=(0, 8))

        prow = tk.Frame(c1, bg=GLASS)
        prow.pack(fill="x", padx=12, pady=(0, 12))
        self.plugins_var = tk.StringVar()
        pf_shell, _ = neu_entry(prow, textvariable=self.plugins_var)
        pf_shell.pack(side="left", fill="x", expand=True)
        _, br = pill_button(prow, "Browse...", ACCENT_DIM, TEXT,
                            self._browse_plugins, padx=10, pady=5)
        br.master.pack(side="left", padx=(8, 0))

        # ── 2. DETECTED PLUGINS ──────────────────────────────
        _, c2 = glass_frame(p)
        c2.master.pack(fill="x", pady=(0, 10))
        head_row = tk.Frame(c2, bg=GLASS)
        head_row.pack(fill="x")
        sl = section_label(head_row, "2  PLUGINS")
        _, refresh_btn = pill_button(head_row, "⟳ Refresh", ACCENT_DIM, TEXT,
                                     self.refresh_plugins,
                                     font=("Segoe UI", 8), padx=8, pady=3)
        refresh_btn.master.pack(side="right", padx=(0, 12))
        tk.Label(c2, text="Plugins found in your PLUGINS folder. Any zip dropped in here is\n"
                 "extracted automatically on Refresh. Click + on a plugin to add a GitHub\n"
                 "source, website, or Discord link, plus your own notes.",
                 bg=GLASS, fg=TEXT2, font=("Segoe UI", 8),
                 justify="left").pack(anchor="w", padx=12, pady=(0, 8))
        self._plugins_container = tk.Frame(c2, bg=GLASS)
        self._plugins_container.pack(fill="x", padx=12, pady=(0, 12))

        # ── 3. MAPS ──────────────────────────────────────────
        _, c3 = glass_frame(p)
        c3.master.pack(fill="x", pady=(0, 10))
        section_label(c3, "3  MAPS")
        tk.Label(c3, text="Name each map and set its Plugins folder path.\n"
                 "Tick or untick to include or skip maps for this run.",
                 bg=GLASS, fg=TEXT2, font=("Segoe UI", 8),
                 justify="left").pack(anchor="w", padx=12, pady=(0, 8))

        self._maps_container = tk.Frame(c3, bg=GLASS)
        self._maps_container.pack(fill="x", padx=12)

        wc_outer, wc = glass_frame(c3)
        wc_outer.pack(fill="x", padx=12, pady=(8, 8))
        tk.Label(wc, text="GSA WILDCARD (finds all maps automatically)",
                 bg=GLASS, fg=TEXT3, font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=8, pady=(6, 2))
        wc_row = tk.Frame(wc, bg=GLASS)
        wc_row.pack(fill="x", padx=8, pady=(0, 8))
        self._wc_var = tk.StringVar(
            value=r"C:\GameServerApp\containers\*\serverfiles\ShooterGame\Binaries\Win64\ArkApi\Plugins")
        wc_shell, _ = neu_entry(wc_row, textvariable=self._wc_var, font=("Segoe UI", 8))
        wc_shell.pack(side="left", fill="x", expand=True)
        _, wc_btn = pill_button(wc_row, "Add all maps", ACCENT_DIM, TEXT,
                                self._add_wildcard, font=("Segoe UI", 8), padx=10, pady=4)
        wc_btn.master.pack(side="left", padx=(6, 0))

        # generic scan: works for any server manager, not just GSA
        scan_outer, scan = glass_frame(c3)
        scan_outer.pack(fill="x", padx=12, pady=(0, 8))
        tk.Label(scan, text="OR SCAN ANY FOLDER (finds maps for any server setup)",
                 bg=GLASS, fg=TEXT3, font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=8, pady=(6, 2))
        tk.Label(scan, text="Pick the folder where your servers are installed. Searches for any\n"
                 "...ShooterGame\\Binaries\\Win64\\ArkApi\\Plugins folder inside it, however deep.",
                 bg=GLASS, fg=TEXT2, font=("Segoe UI", 8), justify="left").pack(anchor="w", padx=8, pady=(0, 6))
        scan_btn_row = tk.Frame(scan, bg=GLASS)
        scan_btn_row.pack(anchor="w", padx=8, pady=(0, 8))
        self._scan_btn_shell, self._scan_btn = pill_button(
            scan_btn_row, "Browse and scan for maps", ACCENT_DIM, TEXT,
            self._scan_for_maps, font=("Segoe UI", 8), padx=10, pady=4)
        self._scan_btn_shell.pack(side="left")
        self._scan_spinner_lbl = tk.Label(scan_btn_row, text="", bg=GLASS, fg=WARN,
                                          font=("Segoe UI", 9, "bold"))
        self._scan_spinner_lbl.pack(side="left", padx=(10, 0))
        self._scanning = False
        self._scan_spinner_frames = ["|", "/", "-", "\\"]
        self._scan_spinner_i = 0

        _, add_btn = pill_button(c3, "+ Add map manually", GLASS_EDGE, TEXT2,
                                 lambda: self._add_map_row(),
                                 font=("Segoe UI", 9), padx=12, pady=6)
        add_btn.master.pack(anchor="w", padx=12, pady=(0, 12))

        # ── 4. RUN ───────────────────────────────────────────
        _, c4 = glass_frame(p)
        c4.master.pack(fill="x", pady=(0, 10))
        section_label(c4, "4  RUN")
        tk.Label(c4, text="Always Dry Run first. It previews what will happen without changing anything.",
                 bg=GLASS, fg=TEXT2, font=("Segoe UI", 8)).pack(anchor="w", padx=12, pady=(0, 10))

        btn_row = tk.Frame(c4, bg=GLASS)
        btn_row.pack(fill="x", padx=12, pady=(0, 14))

        _, dry_btn = pill_button(btn_row, "⟳  Dry Run",
                                 "#2d1f00", WARN, self._run_dry,
                                 font=("Segoe UI", 10, "bold"), padx=20, pady=10)
        dry_btn.master.pack(side="left", padx=(0, 10))

        _, go_btn = pill_button(btn_row, "✔  Update Plugins",
                                TEAL_DIM, TEAL, self._run_real,
                                font=("Segoe UI", 10, "bold"), padx=20, pady=10)
        go_btn.master.pack(side="left")

    # ── Plugin list ──────────────────────────────────────────
    def refresh_plugins(self):
        for r in self._plugin_rows:
            r.frame.destroy()
        self._plugin_rows = []
        folder = self.plugins_var.get().strip()
        if not folder or not os.path.isdir(folder):
            tk.Label(self._plugins_container,
                     text="Set your PLUGINS folder above, then click Refresh.",
                     bg=GLASS, fg=TEXT3, font=("Segoe UI", 8)).pack(anchor="w")
            return

        # auto-extract any zip files dropped directly into the PLUGINS folder
        zips = [f for f in os.scandir(folder) if f.is_file() and f.name.lower().endswith(".zip")]
        for zf in zips:
            name, msg = extract_zip_into_plugins_folder(zf.path, folder)
            self._log(msg, "ok" if name else "warn")

        # clear any placeholder labels
        for w in self._plugins_container.winfo_children():
            if isinstance(w, tk.Label):
                w.destroy()
        subdirs = sorted([d for d in os.scandir(folder) if d.is_dir()],
                         key=lambda d: d.name.lower())
        if not subdirs:
            tk.Label(self._plugins_container,
                     text="No plugin folders found here yet.",
                     bg=GLASS, fg=TEXT3, font=("Segoe UI", 8)).pack(anchor="w")
            return
        for d in subdirs:
            row = PluginRow(self._plugins_container, self, d.name, d.path)
            self._plugin_rows.append(row)

    # ── Map rows ─────────────────────────────────────────────
    def _add_map_row(self, name="", path="", enabled=True):
        def remove(row):
            row.frame.destroy()
            self._map_rows.remove(row)
            self._save()
        row = MapRow(self._maps_container, self,
                     name=name, path=path, enabled=enabled, on_remove=remove)
        self._map_rows.append(row)
        self._save()
        return row

    def _add_wildcard(self):
        pattern = self._wc_var.get().strip()
        if not pattern:
            return
        if "*" in pattern:
            matches = sorted(glob.glob(pattern))
            if not matches:
                messagebox.showwarning(APP_NAME,
                    "No folders found matching that path.\n"
                    "Check the path is correct and GameServerApp has created containers.")
                return
            existing_paths = {os.path.normpath(r.path_var.get()) for r in self._map_rows}
            added = 0
            for path in matches:
                norm = os.path.normpath(path)
                if norm in existing_paths:
                    continue
                parts = path.replace("\\", "/").split("/")
                try:
                    idx = [s.lower() for s in parts].index("containers")
                    cid = parts[idx + 1]
                except (ValueError, IndexError):
                    cid = os.path.basename(path)
                self._add_map_row(name=cid, path=path)
                existing_paths.add(norm)
                added += 1
            msg = f"Added {added} map(s) from wildcard."
            if added < len(matches):
                msg += f" ({len(matches) - added} already present, skipped.)"
            self._log(msg, "ok")
        elif os.path.isdir(pattern):
            self._add_map_row(path=pattern)
        else:
            messagebox.showwarning(APP_NAME, f"Path not found:\n{pattern}")

    def _scan_for_maps(self):
        if self._scanning:
            return  # already running, ignore extra clicks
        root = filedialog.askdirectory(
            title="Select the folder where your servers are installed")
        if not root:
            return
        self._log(f"Scanning {root} for Plugins folders. This can take a while on a large drive...", "dim")
        self._scanning = True
        self._scan_btn.config(state="disabled")
        self.config(cursor="watch")
        self._animate_scan_spinner()
        threading.Thread(target=self._scan_worker, args=(root,), daemon=True).start()

    def _animate_scan_spinner(self):
        if not self._scanning:
            self._scan_spinner_lbl.config(text="")
            return
        frame = self._scan_spinner_frames[self._scan_spinner_i % len(self._scan_spinner_frames)]
        self._scan_spinner_lbl.config(text=f"{frame} scanning...")
        self._scan_spinner_i += 1
        self.after(150, self._animate_scan_spinner)

    def _scan_worker(self, root):
        try:
            matches = find_plugins_folders(root)
        except OSError as e:
            self.after(0, self._scan_failed, str(e))
            return
        self.after(0, self._scan_done, matches)

    def _scan_failed(self, error_text):
        self._scanning = False
        self._scan_btn.config(state="normal")
        self.config(cursor="")
        self._log(f"Scan failed: {error_text}", "err")
        messagebox.showerror(APP_NAME, f"Scan failed:\n{error_text}")

    def _scan_done(self, matches):
        self._scanning = False
        self._scan_btn.config(state="normal")
        self.config(cursor="")
        if not matches:
            self._log("Scan finished: no ArkApi\\Plugins folders were found in that location.", "warn")
            messagebox.showinfo(APP_NAME,
                "No ArkApi\\Plugins folders were found in that location.\n"
                "Make sure you picked a folder that actually contains your server install(s), "
                "for example the drive or folder above where ShooterGame lives.")
            return
        existing_paths = {os.path.normpath(r.path_var.get()) for r in self._map_rows}
        added = 0
        for path in matches:
            norm = os.path.normpath(path)
            if norm in existing_paths:
                continue
            # guess a map name from the folder structure above ShooterGame
            parts = path.replace("\\", "/").split("/")
            try:
                idx = [s.lower() for s in parts].index("shootergame")
                guess = parts[idx - 1] if idx >= 1 else os.path.basename(path)
            except ValueError:
                guess = os.path.basename(path)
            self._add_map_row(name=guess, path=path)
            existing_paths.add(norm)
            added += 1
        msg = f"Scan complete: added {added} map(s)."
        if added < len(matches):
            msg += f" ({len(matches) - added} already present, skipped.)"
        self._log(msg, "ok")

    # ── Persistence ──────────────────────────────────────────
    def _load_saved(self):
        self.plugins_var.set(self.cfg.get("plugins_folder", ""))
        for m in self.cfg.get("maps", []):
            self._add_map_row(name=m.get("name", ""),
                              path=m.get("path", ""),
                              enabled=m.get("enabled", True))

    def _save(self):
        self.cfg["plugins_folder"] = self.plugins_var.get()
        self.cfg["maps"] = [r.get_data() for r in self._map_rows]
        save_config(self.cfg)

    def _browse_plugins(self):
        d = filedialog.askdirectory(title="Select your PLUGINS folder")
        if d:
            self.plugins_var.set(d)
            self._save()
            self.refresh_plugins()

    # ── Log (thread-safe) ────────────────────────────────────
    def _log(self, msg, tag="info"):
        self.after(0, self._log_main, msg, tag)

    def _log_main(self, msg, tag):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _log_clear(self):
        self.after(0, self._log_clear_main)

    def _log_clear_main(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # ── Run ──────────────────────────────────────────────────
    def _run_dry(self):
        self._run(dry=True)

    def _run_real(self):
        enabled = [r for r in self._map_rows if r.enabled_var.get()]
        if not enabled:
            messagebox.showwarning(APP_NAME, "No maps are ticked. Tick at least one map to update.")
            return
        names = "\n".join(f"  - {r.get_data()['name'] or r.get_data()['path']}" for r in enabled)
        if not messagebox.askyesno(APP_NAME,
                f"Update plugins on {len(enabled)} map(s)?\n\n{names}\n\n"
                "config.json will NOT be changed on any map."):
            return
        self._run(dry=False)

    def _run(self, dry):
        self._save()
        plugins_folder = self.plugins_var.get().strip()
        if not plugins_folder or not os.path.isdir(plugins_folder):
            messagebox.showerror(APP_NAME, "Please set a valid PLUGINS folder.")
            return
        enabled_maps = [r.get_data() for r in self._map_rows if r.get_data()["enabled"]]
        if not enabled_maps:
            messagebox.showwarning(APP_NAME, "No maps are ticked.")
            return
        threading.Thread(target=self._worker,
                         args=(plugins_folder, enabled_maps, dry), daemon=True).start()

    def _worker(self, plugins_folder, maps, dry):
        self._log_clear()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mode = "DRY RUN" if dry else "LIVE UPDATE"
        self._log(f"== {APP_NAME}  |  {ts}  |  {mode} ==\n", "head")

        plugin_dirs = [d for d in os.scandir(plugins_folder) if d.is_dir()]
        if not plugin_dirs:
            self._log("No plugin subfolders found in the PLUGINS folder.", "err")
            return
        self._log(f"Plugins  : {', '.join(d.name for d in plugin_dirs)}", "dim")
        self._log(f"Maps     : {', '.join(m['name'] or m['path'] for m in maps)}\n", "dim")

        resolved = []
        for m in maps:
            p = m["path"]
            if "*" in p:
                for match in sorted(glob.glob(p)):
                    resolved.append({**m, "path": os.path.normpath(match)})
            elif os.path.isdir(p):
                resolved.append({**m, "path": os.path.normpath(p)})
            else:
                self._log(f"WARNING: path not found: {p}", "warn")

        if not resolved:
            self._log("No valid map paths found.", "err")
            return

        total_updated = total_skipped = 0
        locked_any = False

        for pe in plugin_dirs:
            source_files = []
            for root, _, files in os.walk(pe.path):
                for f in files:
                    source_files.append(os.path.join(root, f))

            self._log("-" * 50, "dim")
            self._log(f"PLUGIN: {pe.name}", "head")
            if not source_files:
                self._log("  Empty source folder, skipping.", "warn")
                continue

            updated = skipped = 0
            for m in resolved:
                label = m["name"] or m["path"]
                dest = os.path.join(m["path"], pe.name)
                if not os.path.isdir(dest):
                    self._log(f"  skip  {label}  (not installed)", "dim")
                    skipped += 1
                    continue
                self._log(f"\n  > {label}", "ok")
                map_had_error = False
                for src_path in source_files:
                    rel = os.path.relpath(src_path, pe.path)
                    out = os.path.join(dest, rel)
                    if matches_preserve(os.path.basename(rel)) and os.path.exists(out):
                        self._log(f"    keep  {rel}", "keep")
                        continue
                    self._log(f"    copy  {rel}", "copy")
                    if not dry:
                        os.makedirs(os.path.dirname(out), exist_ok=True)
                        try:
                            shutil.copy2(src_path, out)
                            now = datetime.now().timestamp()
                            os.utime(out, (now, now))
                        except PermissionError:
                            self._log(f"    LOCKED  {rel}  (stop the map and run again)", "err")
                            locked_any = True
                            map_had_error = True
                if not map_had_error:
                    updated += 1
                else:
                    self._log(f"    {label}: not counted as updated (locked files)", "warn")
            total_updated += updated
            total_skipped += skipped
            self._log(f"\n  {pe.name}: {updated} updated, {skipped} skipped", "info")

        self._log("\n" + "=" * 50, "dim")
        self._log(f"Done. {total_updated} update(s) applied.", "ok")
        if total_skipped:
            self._log(f"{total_skipped} map/plugin combination(s) skipped.", "dim")
        if locked_any:
            self._log("Some files were LOCKED. Stop those maps and run again.", "err")
        elif not dry:
            self._log("Restart the affected maps to load the new dlls.", "ok")
        if dry:
            self._log("\nDRY RUN complete. Click Update Plugins to apply.", "warn")


if __name__ == "__main__":
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            from ctypes import windll
            windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    app = ASAPluginUpdater()
    app.mainloop()