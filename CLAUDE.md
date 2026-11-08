# wiki-tool

Build the `wiki` tool specified in SPEC.md. SPEC.md is the source of truth:
read it before each milestone, and read DEVLOG.md to see where things stand.

## Working rules
- One milestone at a time, in SPEC.md's order. End each with offline tests
  passing, a commit, and a DEVLOG.md entry saying what changed and what it cost.
- Stop and ask me before any paid run in M0, before starting M5, before passing
  a milestone's spend allowance, and whenever an estimate passes a command's cap.
- Make paid runs only through the wiki CLI or its runner. Total budget: $50.
- Never write API keys or other secrets to files, logs or commits.

## Commands
- Offline tests: `pytest`
- Paid live tests (ask me first): `WIKI_LIVE=1 pytest -m live`
