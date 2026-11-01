# Contributing

Use Python 3.10+ and a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test,build]'
QT_QPA_PLATFORM=offscreen python -m pytest -q
python -m activity_bub --demo
```

On Windows, activate `.venv\Scripts\activate` and use PowerShell's
`$env:QT_QPA_PLATFORM='offscreen'` before headless tests.

Keep discovery bounded, respect access restrictions and Retry-After, and never
request private-network URLs. New feed adapters should include public/private
visibility, empty-page, malformed-response, and cursor-persistence fixtures.
Preserve whole-image aspect ratios and compact-window tests when editing the UI.

Themes live in `activity_bub/themes.py`. The icon's pixel-grid source is
`tools/make_icons.py`; run it to regenerate PNG/ICO/SVG assets. Avoid adding
network services, accounts, or analytics to local rewards.

See [RELEASING.md](docs/RELEASING.md) for native builds and release verification.
