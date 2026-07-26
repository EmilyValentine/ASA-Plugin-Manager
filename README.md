# ASA Plugin Updater

A free tool for ARK: Survival Ascended server admins that deploys plugin updates across all your maps in one click, shows you what version is installed where, flags anything out of date, and helps you keep track of where each plugin's updates get posted, all without ever touching your config files.

---

## The problem it solves

When a plugin releases an update you normally have to:
- Download the new version and extract it
- Manually copy the files into every map's plugin folder
- Do this again for every server you run
- Remember which version is installed where, and where each plugin's author posts new releases

If you have 10 maps and 8 plugins that is a lot of tedious, error-prone work, and one mistake can wipe a map's configuration or leave a plugin half-updated. This tool automates the copying, shows you what is installed at a glance, and protects your maps from partial updates if a server happens to be running at the time.

---

## What it does

**Deploys updates safely**
- Scans your server(s) for every map that already has a given plugin installed and copies the new files there
- Never overwrites `config.json`, so per-map shop items, kits, and settings are always preserved
- Skips any map that does not already have the plugin installed, so map-specific plugins stay map-specific
- If a map is running and any file is locked, the whole map is left completely untouched. Updates are all-or-nothing per map, so a locked `.dll` can never end up out of sync with an already-updated `PluginInfo.json` or any other file
- Dry Run mode previews exactly what would happen, including which maps would be skipped for having locked files, before anything is changed

**Shows you what you have**
- Lists every plugin in your PLUGINS folder along with its version and when it was last updated
- Reads the version from `PluginInfo.json`, including plugins that use a separate hotfix tag field (for example `Version: 1.8` plus `Tag: A`, shown as `1.8A`)
- Each map can be expanded to show exactly which plugins are installed on it, with their own version and last-updated date
- If a map's installed version is behind what is sitting in your PLUGINS folder, it is flagged in amber as "Newer version available", so an out-of-date map never goes unnoticed
- Works with GameServerApp installs (a wildcard path finds every container automatically) and with any other server setup (a folder scan finds every map by its `ShooterGame\Binaries\Win64\ArkApi\Plugins` path, wherever it lives, with a progress spinner while it works)
- Tick or untick individual maps to include or skip them for a given run, useful for testing an update on one map first

**Handles plugin zips for you**
- Drop a zip straight into your PLUGINS folder and click Refresh: it is extracted automatically and matched against the plugin it belongs to, even when the zip's filename or internal folder name does not match the plugin's actual name
- Every zip extraction, whether from a manual drop or the built-in GitHub downloader, is checked for unsafe file paths before anything is written, so a corrupted or malicious zip cannot write files outside the intended folder

**Helps you keep track of updates**
- Each plugin has an expandable panel where you can save a GitHub repository URL, a website or download page link, a Discord release channel link, and free-text notes
- For plugins hosted on GitHub, click Check to query the latest release and Download to fetch, extract, and install it straight into your PLUGINS folder automatically
- For plugins distributed elsewhere, such as a website or a Discord channel, a one-click Open button takes you straight there to check manually

---

## What it does NOT do

- It does not modify, read, or upload any of your config files or server data
- It does not install a brand new plugin from scratch. The plugin must already exist on a map for that map to be updated
- It is not affiliated with Studio Wildcard, AsaApi, ArkApi, GameServerApp, or any plugin author
- Its GitHub update checker only works for plugins actually hosted on GitHub. Other sources are handled through the manual link fields

---

## Requirements

- Windows 10 or 11
- Your plugin update files downloaded (as a zip or already extracted), or use the built-in GitHub downloader for plugins hosted there

---

## How to use it

### 1. Set up your PLUGINS folder

Create a folder anywhere (for example `Desktop\PLUGINS`). When a plugin updates, either:
- Drop the downloaded zip straight in and click Refresh, it will be extracted and matched automatically, or
- Extract it yourself into a subfolder named exactly like the plugin

Example structure once extracted:
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

> If you extract manually, make sure the folder name matches the plugin folder name on your server exactly, including capitalisation.

### 2. Add your maps

Two ways to find your maps:

- **GameServerApp users**: paste this path into the wildcard box and click Add all maps. The `*` finds every container automatically.
  ```
  C:\GameServerApp\containers\*\serverfiles\ShooterGame\Binaries\Win64\ArkApi\Plugins
  ```
- **Any other setup**: click Browse and scan for maps, then pick the folder where your servers are installed. The tool searches for every folder ending in `ShooterGame\Binaries\Win64\ArkApi\Plugins`, however deep it is nested, with a spinner while it works.

You can also add a map manually and name it whatever makes sense to you. Untick any map you want to skip for a particular run, for example while testing an update on one server first.

### 3. Check what is installed

Expand any plugin in the Plugins list to see its version and last-updated date. Expand any map to see exactly which plugins are installed there, with their own version and date. If a map is running an older version than what is in your PLUGINS folder, it is flagged in amber automatically.

### 4. Set up update sources (optional)

Expand a plugin and fill in whichever of these apply:
- **Update source (GitHub URL)**: enables the Check and Download buttons for plugins hosted on GitHub
- **Website / download page**: a one-click Open button for plugins distributed elsewhere, such as ark-server-api.com
- **Discord release channel**: a one-click Open button straight to the channel where an author posts new releases
- **Notes**: anything else worth remembering, such as dependencies or where you first found the plugin

### 5. Dry Run first

Always click Dry Run before updating. The log shows exactly which maps would be updated, which would be skipped for already having the plugin, and which would be skipped for having locked files. Nothing is written until you click Update Plugins.

### 6. Update

Click Update Plugins, confirm, and watch the log. If a map is running, its files will be locked and that whole map is skipped with nothing changed, so it is safe to run this at any time. Stop the map in your server manager, run the tool again, then start the map back up.

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

- **Locked files**: if a map is running, the whole map is skipped untouched rather than partially updated. Stop the map, run the tool again, then restart it.
- **Folder name must match**: when extracting manually, the plugin subfolder in your PLUGINS folder must be named identically to the plugin folder on your server. A mismatch means that plugin is skipped, and the log will say so.
- **GitHub checking only works for GitHub-hosted plugins**: other sources rely on the manual Website and Discord links.
- **Network servers**: this version works with local paths and UNC network paths (`\\SERVER\share\...`). It is intended for servers you run the tool on directly, not for remote or hosted servers on separate networks.
- **Does not install brand new plugins**: if a map does not already have a plugin's folder, the tool will not create one. Install the plugin manually the first time, then use this tool for all future updates.
- **Version display depends on the author**: the version shown is only as accurate as what the plugin author writes into `PluginInfo.json`. If an author updates a plugin without bumping the version number, this tool has no way to know a hotfix has actually happened.

---

## Contributing and feedback

Found a bug or have a feature request? Open an issue on GitHub. Pull requests are welcome.

---

## Disclaimer

This tool is provided free of charge and as is, with no warranty. Always keep a backup of your plugin folders before running any update tool. The author is not responsible for any data loss or server issues.

This project is not affiliated with, endorsed by, or connected to Studio Wildcard, Snail Games, AsaApi, ArkApi, GameServerApp, or any plugin author.