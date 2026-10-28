# The first minute

A stranger decides whether to care about this project in about sixty seconds. This page records
what those sixty seconds are supposed to contain, which patterns came from other projects, and
what is still missing.

It exists so the decisions survive: an onboarding page is easy to erode one "small" change at a
time until it is a wall of configuration tables again.

---

## What the first minute has to answer

| Question | Where it is answered | Test that keeps it honest |
| :--- | :--- | :--- |
| *What is this?* | The one-line tagline, then **What this is** | `tests/test_docs_and_assets.py::TestReadmeAboveTheFold` |
| *What does it look like?* | `assets/demo.svg`, animated, below the tagline | `TestDemoAsset` (regenerated from real output) |
| *Does it do anything I care about?* | Three differentiators: reads-never-runs, refuses-to-guess, gates-not-reports | — |
| *How do I try it?* | Three commands in **Quick start**, then [`QUICKSTART.md`](../QUICKSTART.md) | `TestQuickstart` (every subcommand verified) |
| *Is it maintained?* | CI, coverage, release badges; a changelog with dates | CI |

The rule behind all of it: **show the product before describing it.** A configuration table is
impressive to someone who has already decided to use the tool, and invisible to everyone else.

---

## Patterns borrowed from projects that grew fast

Deliberately studied, not copied: each of these is a *category* of first impression this project
was missing.

### [`jarrodwatts/claude-hud`](https://github.com/jarrodwatts/claude-hud) — show the artefact immediately

A single screenshot sits directly under one sentence, before install instructions or
configuration. There is no prose explaining what the tool does; the screenshot does it.

**Adopted:** the animated `assets/demo.svg` immediately under the tagline, generated from real
output rather than recorded once and left to rot.

### [`mokevnin/dev-quality-gates`](https://github.com/mokevnin/dev-quality-gates) — state your principles, and be opinionated

It opens with *"not an Awesome List"* and then lists the principles that decide what gets
included: *enforcement over advice*, *honest about gaps*, *fast native toolchains first*. A
reader learns the project's taste in ten seconds, which is what makes them trust the
recommendations.

**Adopted:** the *three things that make it different* block, and the same "honest about gaps"
posture already expressed in [`limitations.md`](limitations.md) — the refusal to grade an
incomplete scan is the most concrete form of it.

### [`max-sixty/worktrunk`](https://github.com/max-sixty/worktrunk) — one sentence, then a command

Description, then the install command, with no ceremony. The pitch is a single sentence that
names the job to be done.

**Adopted:** the README tagline and the three-command quick start; `QUICKSTART.md` keeps the
long version out of the README's path.

### [`backnotprop/pstack`](https://github.com/backnotprop/pstack) — make the repo itself an artefact

The repository *is* the product: skills and principles, structured to be read. Contributors can
improve it without running anything.

**Adopted:** the rule packs, the roadmap-as-JSON, and the `good first issue` queue that is
machine-checked against the code — contributing is a small, reviewable diff, not a research
project.

---

## What a great README does *not* do

It does not put you on the trending page. GitHub trending ranks by star velocity in a 24-hour
window, which is downstream of **distribution** and only partly of presentation:

1. **A spike of real traffic** (a post that lands).
2. **A first minute that converts** — and this is the only part a repository controls.
3. **Retention**: people who star and come back, which rewards an honest, maintained project.

So the honest framing is: the onboarding work above improves the *conversion rate* of whatever
traffic arrives. It cannot create the traffic. Claiming otherwise would be exactly the kind of
unverifiable claim this project refuses to make elsewhere.

### Distribution checklist (not yet done)

- [ ] Submit to the MCP server/tool directories and `awesome-mcp`-style lists.
- [ ] Post the "incomplete scans are not clean" story to MCP and LLM-security communities — it
      is the most discussable thing here, because most scanners get it wrong.
- [ ] Write one short post showing a real finding on a real open-source MCP server.
- [ ] Publish to PyPI so `pip install vexar-mcp` works (RM-023).
- [ ] Ask the first few users what made them try it, and fix that page first.

---

## Maintenance rules for this page's subject matter

- The demo is generated; never hand-edit `assets/demo.svg`. Run
  `python scripts/make_demo_svg.py`, review the diff, commit both.
- Every relative link in every Markdown file is checked by `python scripts/check_docs.py` in CI.
- A documented command must appear in `vexar-mcp --help`; `TestQuickstart` enforces it for the
  quickstart, and the same review standard applies to the README by hand.
- The social preview is **manual**: GitHub has no REST endpoint for it. After changing
  `assets/social-preview.png`, upload it under *Settings → Social preview*.
