---
name: playwright
description: >-
  Playwright browser setup and troubleshooting. Use when Playwright tests fail
  with "Executable doesn't exist", "browserType.launch", "Please run
  playwright install", or any missing-browser error; when a package install was
  assumed to have installed a browser; or when adding Playwright to a project
  without changing its dependency manifests. Covers installing the package
  without touching package.json / lockfiles, verifying the package actually
  resolves, installing the browser binary a test needs, confirming the browser
  executable really exists on disk (a printed path is not proof), and retrying
  the launch.
license: MIT
metadata:
  last_reviewed: '2026-11-13'
---

# Playwright setup and browser-binary verification

## The core misconception

Installing the **Playwright package** and installing the **browser binaries**
are two separate steps. `npm install playwright` / `pip install playwright`
installs only the JavaScript/Python library. It does **not** download the
Chromium / Firefox / WebKit binaries that the library drives. A test can
therefore resolve the package perfectly and still fail at launch with
`Executable doesn't exist at ...`. Treat "package installed" and "browser
installed" as independent facts and verify each one.

A printed executable path is **not** proof the binary exists. Playwright
computes the path it *would* use whether or not the file is there. Always
check the file on disk before trusting a launch.

## 1. Install the package without changing project manifests

The goal is to add Playwright to the environment without editing
`package.json`, `package-lock.json`, `requirements.txt`, or `pyproject.toml`.

Node / npm:

```bash
# --no-save keeps package.json and the lockfile untouched
npm install --no-save playwright
# pin the version the test expects, if known
npm install --no-save playwright@1.48.0
```

Python:

```bash
# installs into the active environment; does not edit any project file
pip install playwright
```

Do **not** run `npm install playwright` without `--no-save` if the project
must keep its manifest unchanged — that rewrites `package.json` and the
lockfile. Do not add Playwright to `requirements.txt` just to use it once.

## 2. Verify the package actually resolves

Resolution proves the library is importable from where the test runs. It says
nothing about browsers.

Node:

```bash
node -e "console.log(require.resolve('playwright'))"
```

Python:

```bash
python -c "import playwright; print(playwright.__version__)"
python -c "from playwright.sync_api import sync_playwright; print('ok')"
```

If this fails, the package is not installed in the interpreter/runtime the
test uses (wrong venv, wrong Node version, wrong working directory). Fix that
before going further.

## 3. Install the browser binary the test needs

Install only the browser the test launches. `chromium` is the common default;
match the test's `browserType`.

Node:

```bash
npx playwright install chromium
# or all three, larger download:
npx playwright install
```

Python:

```bash
python -m playwright install chromium
```

On Linux the browser also needs system libraries. If launch fails with a
missing shared library (`libnss3`, `libatk`, etc.), install them:

```bash
npx playwright install --with-deps chromium
# or
sudo npx playwright install-deps chromium
```

## 4. Verify the browser executable really exists

This is the step that catches the "printed path is not proof" trap. After
install, confirm the actual file is present and runnable.

Playwright stores browsers under a per-OS cache:

- Linux: `~/.cache/ms-playwright/`
- macOS: `~/Library/Caches/ms-playwright/`
- Windows: `%USERPROFILE%\AppData\Local\ms-playwright\`

Check the binary exists and runs:

```bash
# Linux example — list what landed and test the executable directly
ls -la ~/.cache/ms-playwright/
~/.cache/ms-playwright/chromium-*/chrome-linux/chrome --version
```

```powershell
# Windows example
Get-ChildItem "$env:LOCALAPPDATA\ms-playwright"
& "$env:LOCALAPPDATA\ms-playwright\chromium-*\chrome-win\chrome.exe" --version
```

A version string from the binary is real proof. A path printed by Playwright
that you have not `ls`-checked is not. If the file is missing, the install
did not complete (network, disk, or it installed a different browser than
the test wants).

## 5. Retry the launch

After installing the browser, re-run the test. If it still reports a missing
executable, the install targeted the wrong browser or the wrong cache
location — re-run `playwright install <browser>` for the exact browser the
error names, then re-verify the file before launching again.

```bash
# minimal launch probe, independent of the test suite
node -e "const {chromium}=require('playwright'); chromium.launch().then(b=>{console.log('launch ok'); return b.close();})"
```

## Common failure signatures

| Error | Real cause | Fix |
|---|---|---|
| `Executable doesn't exist at .../chrome` | Browser binary never installed | `playwright install chromium` |
| `browserType.launch: ... cannot open shared object file` | Missing OS libraries | `playwright install --with-deps chromium` |
| `Cannot find module 'playwright'` | Package not in this runtime | `npm install --no-save playwright` in the right dir/runtime |
| `ModuleNotFoundError: playwright` | Wrong/missing venv | Activate the venv the test uses, then `pip install playwright` |
| Launch works in shell but not in test | Different user / HOME / cache path | Ensure the test runs as the same user that installed the browser |

## Verification checklist

1. `require.resolve('playwright')` / `import playwright` succeeds — package resolves.
2. `playwright install <browser>` ran for the exact browser the test launches.
3. The browser executable file exists on disk and `--version` runs.
4. System libraries installed (`--with-deps`) if a shared-object error appeared.
5. The test launches after the browser install.
6. No project manifest or lockfile was modified to achieve the above.

## What this skill deliberately avoids

- It does **not** treat `npm install` / `pip install` as browser installation.
- It does **not** modify `package.json`, lockfiles, or `requirements.txt`.
- It does **not** accept a printed executable path as evidence the binary is present.
