# changelog.py

Generate a changelog summary from changes in Python dependencies.

This script compares your `requirements.txt` or `Pipfile.lock`
with its previously committed state to determines what packages have
been updated. It then fetches the commit history for each package
and generates a nicely formatted changelog to help you understand
if there are breaking changes, new features, bug fixes, etc.

## Installation

With [`uv`](https://docs.astral.sh/uv/getting-started/installation/), install the
`changelog-py` command from GitHub (no PyPI needed):

```bash
# Install as a tool:
uv tool install git+https://github.com/slint/changelog.py

# ...or run it without installing:
uvx --from git+https://github.com/slint/changelog.py changelog-py --help

# ...or an editable install from a local clone (for development):
git clone git@github.com:slint/changelog.py.git
uv tool install -e ./changelog.py
```

## Usage

```bash
# Running the following in a local clone of https://github.com/zenodo/zenodo-rdm
changelog-py --package-filter "invenio" --since v7.3.0 --until v7.4.0

# ...or e.g. if you have local changes
changelog-py --package-filter "invenio" --since HEAD
```

...will output:

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
