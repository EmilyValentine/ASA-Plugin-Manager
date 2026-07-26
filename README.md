# ASA Plugin Updater

A free tool for ARK: Survival Ascended server admins that deploys plugin updates across all your maps in one click, tracks what version is installed where, and helps you check for new releases, all without ever touching your config files.

---

## The problem it solves

When a plugin releases an update you normally have to:
- Download the new version
- Extract it
- Manually copy the files into every map's plugin folder
- Do this again for every server you run
- Keep track of which version is installed where, and remember where each plugin's updates get posted

If you have 10 maps and 8 plugins that is a lot of tedious, error-prone work, and one mistake can wipe a map's configuration. This tool automates the copying, shows you what is installed at a glance, and keeps a record of where to check for updates for each plugin.

---

## What it does

**Deploys updates safely**
- Scans your server(s) for every map that has a given plugin installed
- Copies the new plugin files (dll, pdb, PluginInfo.json, and so on) to each one
- Never overwrites `config.json`, so per-map shop items, kits, and settings are always preserved
- Skips any map that does not already have the plugin installed, so map-specific plugins stay map-specific
- Dry Run mode previews exactly what would happen before anything is changed

**Shows you what you have**
- Lists every plugin in your PLUGINS folder along with its version (read from `PluginInfo.json`) and when it was last updated
- Each map can be expanded to show exactly which plugins are installed on it, with version and last-updated date, so you can spot a map that has fallen behind
- Works with GameServerApp installs (a wildcard path finds every container automatically) and with any other server setup (a folder scan finds every map by its `ShooterGame\Binaries\Win64\ArkApi\Plugins` path, wherever it lives)
- Tick or untick individual maps to include or skip them for a given run, useful for testing an update on one map first

**Helps you keep track of updates**
- Each plugin has an expandable panel where you can save a GitHub repository URL, a website or download page link, a Discord release channel link, and free-text notes
- For plugins hosted on GitHub, click Check to query the latest release and Download to fetch, extract, and install it straight into your PLUGINS folder automatically, even when the zip's internal folder name does not match the plugin name
- For plugins distributed elsewhere (a website or a Discord channel), a one-click Open button takes you straight there to check manually

---

## What it does NOT do

- It does not modify, read, or upload any of your config files or server data
- It does not install a brand new plugin from scratch. The plugin must already exist on a map for that map to be updated
- It is not affiliated with Studio Wildcard, AsaApi, ArkApi, GameServerApp, or any plugin author
- Its GitHub update checker only works for plugins actually hosted on GitHub. Other sources are handled through the manual link fields

---

## Requirements

- Windows 10 or 11
- Your plugin update files downloaded and extracted (or use the built-in GitHub downloader for plugins hosted there)

---

## How to use it

### 1. Set up your PLUGINS folder

Create a folder anywhere (for example `Desktop\PLUGINS`). When a plugin updates:
- Download the new version and extract it, or use the in-app GitHub downloader
- Make sure the extracted folder is named exactly like the plugin

Example structure:
```
PLUGINS\
  ArkShop\
    ArkShop.dll
    ArkShop.pdb
    Commented.json
    PluginInfo.json
  Permissions\
    Permissions.dll
    ...
```

> Make sure the folder name matches the plugin folder name on your server exactly, including capitalisation. If an extracted folder has a version number in its name (for example `ArkShop-2.5`), rename it to just `ArkShop`.

### 2. Add your maps

Two ways to find your maps:

- **GameServerApp users**: paste this path into the wildcard box and click Add all maps. The `*` finds every container automatically.
  ```
  C:\GameServerApp\containers\*\serverfiles\ShooterGame\Binaries\Win64\ArkApi\Plugins
  ```
- **Any other setup**: click Browse and scan for maps, then pick the folder where your servers are installed. The tool searches for every folder ending in `ShooterGame\Binaries\Win64\ArkApi\Plugins`, however deep it is nested.

You can also add a map manually and name it whatever makes sense to you. Untick any map you want to skip for a particular run, for example while testing an update on one server first.

### 3. Check what is installed

Expand any plugin in the Plugins list to see its version and last-updated date. Expand any map to see exactly which plugins are installed there, with their own version and date, so you can compare what you are about to deploy against what is currently live.

### 4. Set up update sources (optional)

Expand a plugin and fill in whichever of these apply:
- **Update source (GitHub URL)**: enables the Check and Download buttons for plugins hosted on GitHub
- **Website / download page**: a one-click Open button for plugins distributed elsewhere, such as ark-server-api.com
- **Discord release channel**: a one-click Open button straight to the channel where an author posts new releases
- **Notes**: anything else worth remembering, such as dependencies or where you first found the plugin

### 5. Dry Run first

Always click Dry Run before updating. The log shows exactly which maps would be updated and which files would be copied or preserved. Nothing is written until you click Update Plugins.

### 6. Update

Click Update Plugins, confirm, and watch the log. If any files show as LOCKED, the map is running and holding the file: stop that map in your server manager, then run it again. Restart all affected maps when done.

Your settings, map list, and plugin notes are all saved automatically, so you only need to set things up once.

---

## Antivirus and SmartScreen warning

When you first run the exe, Windows SmartScreen may show a warning saying "Windows protected your PC." This is a standard warning for any exe downloaded from the internet that does not have an expensive code-signing certificate. It does not mean the file is harmful.

To run it anyway, click More info, then Run anyway.

Some antivirus tools may also flag PyInstaller-built executables as suspicious. This is a known false positive: PyInstaller is the standard tool for packaging Python apps into exe files. The full source code is in this repository so you can inspect exactly what the app does, or run `asa_plugin_updater.py` directly with Python if you prefer.

---

## Running from source

1. Install Python 3.x from https://python.org (tick "Add Python to PATH" during setup)
2. No extra libraries are required; only Python's built-in modules are used
3. Run:
   ```
   python asa_plugin_updater.py
   ```

---

## Limitations and known issues

- **Locked files**: Windows will not let the tool overwrite a dll while the map is running. Stop the map, run the tool again, then restart it. The log tells you which files were locked.
- **Folder name must match**: the plugin subfolder in your PLUGINS folder must be named identically to the plugin folder on your server. A mismatch means that plugin is skipped, and the log will say so.
- **GitHub checking only works for GitHub-hosted plugins**: other sources rely on the manual Website and Discord links.
- **Network servers**: this version works with local paths and UNC network paths (`\\SERVER\share\...`). It is intended for servers you run the tool on directly, not for remote or hosted servers on separate networks.
- **Does not install brand new plugins**: if a map does not already have a plugin's folder, the tool will not create one. Install the plugin manually the first time, then use this tool for all future updates.

---

## Contributing and feedback

Found a bug or have a feature request? Open an issue on GitHub. Pull requests are welcome.

---

## Disclaimer

This tool is provided free of charge and as is, with no warranty. Always keep a backup of your plugin folders before running any update tool. The author is not responsible for any data loss or server issues.

This project is not affiliated with, endorsed by, or connected to Studio Wildcard, Snail Games, AsaApi, ArkApi, GameServerApp, or any plugin author.