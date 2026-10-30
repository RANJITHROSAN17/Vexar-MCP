# Publishing to PyPI

The workflow that uploads Vexar-MCP to PyPI has existed for a while. What has never been
proven is the round trip: a git tag becoming a PyPI file, becoming `pip install vexar-mcp`,
becoming a working `vexar-mcp` command. This page is the runbook for proving it once, and for
keeping it proven afterwards.

Two things to know before you start:

- **`vexar-mcp` does not exist on PyPI yet.** A first publish needs a *pending* trusted
  publisher, because there is no project page to add one to. See [step 1](#step-1-register-a-pending-trusted-publisher).
- **`mcp-doctor` on PyPI is somebody else's project** (`Jiansen/mcp-doctor`, one release,
  March 2026). It is not an earlier upload of this repository and it must not be claimed or
  reused. This project ships as the distribution `vexar-mcp`; `mcp_doctor` is only the import
  package name.

## How a release reaches PyPI

```
git tag vX.Y.Z ──► release.yml ──► verify_release.py (tag == version == changelog)
                        │              │
                        │              ├─► pytest + build wheel/sdist
                        │              └─► GitHub Release
                        └─► calls publish-pypi.yml ──► OIDC token ──► PyPI
                                                             └─► prove-install: pip install
                                                                 vexar-mcp==X.Y.Z in a clean venv
```

The call is explicit rather than a `release: published` trigger for a reason: the GitHub
Release is created with `GITHUB_TOKEN`, and GitHub does not start workflows for events caused
by `GITHUB_TOKEN`. A tag push used to build a Release and stop there.

`publish-pypi.yml` also runs on `release: published` (for a Release published by hand in the
UI) and on `workflow_dispatch` (for a dry run). `skip-existing: true` keeps those paths from
colliding: a re-run cannot fail a release that actually succeeded.

## Step 1: register a pending trusted publisher

Do this once, in the browser, as the PyPI account that will own the project.

1. Sign in to PyPI and open <https://pypi.org/manage/account/publishing/>.
2. Under **Add a new pending publisher**, choose **GitHub** and fill in exactly:

   | Field | Value |
   | :--- | :--- |
   | PyPI Project Name | `vexar-mcp` |
   | Owner | `RANJITHROSAN17` |
   | Repository name | `Vexar-MCP` |
   | Workflow name | `publish-pypi.yml` |
   | Environment name | `pypi` |

3. Save. The publisher stays *pending* — invisible on PyPI — until the first successful
   upload, at which point it becomes a normal publisher of a normal project.

Four of those five values are matching problems, not typing problems: the project name must
normalize to `vexar-mcp`, the workflow name is the **file name including `.yml`**, and the
environment name must equal the `environment: name:` in the workflow. `verify_release.py`
asserts the repository side of that contract, but it cannot see the PyPI side.

Once the project exists, later changes are made on the project page
(**Manage → Publishing**) instead.

## Step 2: prove the build without uploading

Worth doing once before the first real publish, and after any change to the packaging:

1. Actions → **Publish to PyPI (Trusted Publishing)** → *Run workflow*.
2. Leave `dry-run` at its default, `true`, and run it on `main`.

That runs the version, changelog and OIDC checks, the test suite, the build, the wheel
inspection and `twine check` — and stops before the upload. It uploads nothing, so it is safe
to run at any time.

## Step 3: the first real publish

The version you publish must not already exist on PyPI, and it cannot be reused afterwards:
PyPI permanently owns `vexar-mcp` `0.2.6` the moment it is uploaded.

1. Move the `## [Unreleased]` notes in `CHANGELOG.md` under a dated
   `## [0.2.7] - YYYY-MM-DD` heading.
2. Set the same version in `pyproject.toml` and `src/mcp_doctor/__init__.py`.
3. Verify locally, from the repository root:

   ```bash
   python scripts/verify_release.py --tag v0.2.7
   ```

   It checks the three version statements, the distribution name, the changelog section, the
   OIDC wiring and the release hand-off. No network, no account, no upload.

4. Commit, push, then tag and push the tag:

   ```bash
   git tag v0.2.7
   git push origin main --tags
   ```

5. Watch **Release Bot**, which verifies the tag, runs the suite, builds, publishes the
   GitHub Release, and then calls the publish workflow. The last job, *Prove pip install
   yields a working vexar-mcp*, installs the published version from PyPI in a fresh venv and
   scans a real server with it. That job is the round trip; if it is green, publishing works.

Publishing by hand instead — drafting a Release in the UI and clicking **Publish** — also
publishes, via the `release: published` trigger. Both paths leave the same evidence.

## Step 4: verify from the outside

Do not trust the workflow's own report. From any machine:

```bash
python -m venv /tmp/vexar-proof
/tmp/vexar-proof/bin/pip install "vexar-mcp==0.2.7"
/tmp/vexar-proof/bin/vexar-mcp --version   # vexar-mcp 0.2.7
```

Then confirm the four statements of the same version agree: `pyproject.toml`, the git tag, the
GitHub Release title, and <https://pypi.org/project/vexar-mcp/>. `--pypi` adds the PyPI half
to the local check:

```bash
python scripts/verify_release.py --tag v0.2.7 --pypi
```

## When it fails

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| `403 Invalid or non-existent authentication information` | No matching (pending) trusted publisher, or the workflow is not the file registered | Re-read [step 1](#step-1-register-a-pending-trusted-publisher); the file name and the `pypi` environment must match exactly |
| `403` with the publisher registered | The job is not in the `pypi` environment, so no OIDC token is issued | Check `environment: name: pypi` and that the job has `id-token: write` |
| `400 File already exists` | That version is already on PyPI; a version can never be re-uploaded | Bump `pyproject.toml`, `__init__.py`, the changelog and the tag together |
| The Release appears, nothing reaches PyPI | The `GITHUB_TOKEN` event chain: a Release created by a workflow starts no other workflow | Fixed by the explicit hand-off from `release.yml`; do not remove it |
| `verify_release: … does not match the packaged version` | The tag and `pyproject.toml` disagree | Tag the commit that carries the version, or bump the version to match the tag |
| The install job fails for the first minute | PyPI's simple index lags a few seconds behind the upload | Already retried for ~3 minutes; a longer failure means the upload failed |
| Local `vexar-mcp` still shows the old version | A stale editable install shadows the published wheel | Install into a fresh venv, as in [step 4](#step-4-verify-from-the-outside) |

## Rules worth keeping

- **No tokens.** Trusted Publishing means there is no `PYPI_API_TOKEN` to leak, rotate or
  forget. A token-based fallback would undo the only security property this setup has.
- **Never reuse a version.** PyPI keeps every file it ever accepted; a broken release is
  fixed by publishing the next patch, and withdrawn with **Yank** on the project page (which
  hides it from resolvers without breaking anyone who already pinned it).
- **One version, four places.** `pyproject.toml`, `__init__.py`, the tag, and the changelog
  heading. `verify_release.py --tag` exists because three of them were drifted before.
- **`mcp_doctor` is a shared import name.** The unrelated `mcp-doctor` distribution ships the
  same top-level package, so the two cannot be installed together. The clean-venv proof job
  is what keeps that collision from hiding a packaging bug.

Related: [release management](release-management.md), [usage](usage.md),
[the composite action](github-actions.md), [contributing](../CONTRIBUTING.md).
