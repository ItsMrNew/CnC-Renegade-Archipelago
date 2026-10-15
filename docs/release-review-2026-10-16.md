# Release review — 16 October 2026

Renegade Archipelago 1.0.0 is prepared for a Windows release supporting stock
Steam and Tiberian Technologies 4.8.4 b9000. Publication is a separate action.

## Findings and fixes

- Desktop actions now capture their worker/client safely. A click racing
  disconnect no longer raises a closed-loop error or targets a later session.
  Tests cover supplies, mission starts, restart confirmations, item recovery and
  transfer settings.
- Display-queue overflow is serialized between producers. Concurrent logging and
  status updates cannot fill the gap between eviction and insertion and raise an
  unhandled queue-full exception. Durable AP progress is separate from this queue.
- Inventory counts are indexed and replaced/appended with the authoritative item
  stream. Status snapshots compute room readiness and unlocked maps once. Tests
  cover reconnect replacement, incremental items, empty inventories, invalid
  packets and out-of-sequence delivery. These reduce polling work without changing
  IDs, pool composition or gameplay rules.
- Release exports now default to the mod alone, include their exporter in the
  source, and reject mixed EXE/APWorld/Windows ZIP files or stale documentation.
  Historical reports, local state, research and logs are excluded.
- The standalone EXE includes the original-source license and third-party notices,
  plus dependency licenses. The Windows ZIP includes final release notes.
- APWorld metadata identifies 1.0.0. The client logs its version at startup.

## Automated validation

- Full workspace regression suite: **2,397 passed** after the runtime fixes.
- Clean mod-only source export: **2,286 passed**, without companion files.
- Focused runtime/export/launcher suite after final packaging changes: **155 passed**.
- Focused static scan of runtime, APWorld, entry scripts and release builders:
  no undefined names, unused imports/locals, or selected bug-pattern findings.
- Standalone EXE copied alone to an isolated folder, with Python absent from PATH:
  passed Tk startup, both adapter imports, local WebSocket exchange, license
  presence and all **36** mission-start save validations.
- Embedded EXE launched through an isolated **Archipelago 0.6.8** installation
  twice; its verified cache was reused and its packaging check passed.
- Both recommended YAMLs generated with the release world in Archipelago 0.6.8.
  Each disposable local server acknowledged **190 checks**, delivered **191 items**
  including the starting mission, unlocked all **12 maps**, granted all **22 weapon
  families** and five upgrades per stat, and recorded the goal. DeathLink setting
  propagation was checked in both presets. Completion events in this test were
  synthetic transport inputs; no game objectives or player deaths were forced.
- EXE, embedded payload and Windows ZIP match. Export checksums and source/archive
  contents are checked before delivery; private room state is excluded.

A synthetic benchmark of 300,000 item-count queries over 190 received items took
about 0.014 seconds with indexed counts versus 1.57 seconds scanning the tuple.
This measures that lookup only; it is not an overall client/game speed claim.

## Live validation and limits

The user confirmed final Steam and TT packaged gameplay sessions on 16 October.
Checkpoint input recovery, SAM objective recovery on both builds, Concrete Boots
jump blocking and the opening Black Hand reward/checkpoint/duplicate behavior
were confirmed on 9 October. Those reports remain the live evidence; this review
made no native game-hook or save-format changes and did not run a new gameplay
session. Automated native tests emulate the hooks and do not cover every possible
campaign state. Other game patches/custom binaries remain unsupported.

## Reproduction

```powershell
python -m pytest -q
python -m tools.build_launcher --output-dir dist/build-1.0
python -m tools.smoke_ap_launcher --apworld dist/build-1.0/renegade.apworld --output .research/launcher-1.0
python -m tools.prepare_release --launcher-dir dist/build-1.0 --output dist/release-1.0
```

Use fresh output directories so previous evidence and builds are preserved.
The source repository contains tests and build tools; local logs/research are not
release assets. The checklist records the remaining publication step.
