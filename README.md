# changelog.py

Generate a changelog summary from changes in Python dependencies.

## Installation

Make sure you have [`pipx`](https://pipx.pypa.io/stable/) installed.

```bash
# Clone this repo (or directly download `changelog.py`)
git clone git@github.com:slint/changelog.py.git
cd changelog.py

# Make sure `changelog.py` is in your PATH (e.g. via symlinking)
ln -s "$(realpath changelog.py)" "$HOME/.local/bin/changelog.py"

# In the future you'll be able to install from PyPI like:
pipx install changelog.py
```

Or if you're a fan of `pipx run`, you can just download the `changelog.py` script, `chomd +x ...`, and run it as any normal script in your path.

## Usage

```bash
# Running the following in a local clone of https://github.com/zenodo/zenodo-rdm
changelog.py --package-filter "invenio" --since v7.3.0 --until v7.4.0

# ...or e.g. if you have local changes
changelog.py --package-filter "invenio" --since HEAD^
```

will output:

```
📁 invenio-app-rdm (13.0.0b0.dev3 -> 13.0.0b0.dev4 )

    release: v13.0.0b0.dev4
    ui: rework secret links tab (inveniosoftware/invenio-app-rdm#2701)

    * ui: rework secret links tab
    * introduce an error handler and a jinja template for drafts not found when published record exists
    * gray out "can view" permission in share drafts
    * rework links that are being copied in links tab
    * closes https://github.com/inveniosoftware/invenio-app-rdm/issues/2694
    * templates: tombstone wording for missing draftheader: adapt menu to responsive view

📁 invenio-drafts-resources (4.0.0 -> 5.0.0 ⚠️)

    release: v5.0.0
    errors: introduce an error for drafts not found when publish record exists

    * closes https://github.com/inveniosoftware/invenio-app-rdm/issues/2694

📁 invenio-rdm-records (11.0.0 -> 11.2.0 🌈)

    📦 release: v11.2.0
    iiif: schema: only return images within size limit in manifest
    release: v11.1.0
    installation: upgrade invenio-drafts-resources

📁 invenio-records-resources (6.0.0 -> 6.1.0 🌈)

    📦 release: v6.1.0
    files: sync metadata on file edit

📁 invenio-search-ui (2.8.6 -> 2.8.7 🐛)

    release: v2.8.7
    components: search options responsiveness
```
