# Renegade Archipelago 1.0 release checklist

The release is prepared locally. Building and exporting do not publish to GitHub.

## Gameplay validation

- [x] Packaged Steam session: Start/Continue, real checks, supplies/effects and reconnect.
- [x] Packaged TT 4.8.4 b9000 session: corresponding gameplay checks.
- [x] Mission 02 SAM early-destruction/recovery check on Steam and TT.
- [x] Checkpoint input recovery: no stuck firing/movement after saving.
- [x] Concrete Boots prevents jumping and restores normal control after expiry.
- [x] Scorpion Hunters opening Black Hand reward, checkpoint and duplicate prevention.

The user confirmed the final Steam/TT sessions on **16 October 2026**. The input,
SAM and Concrete Boots checks were confirmed on **9 October**, together with the
Black Hand checkpoint/duplicate behavior. These are user-reported live results;
automated/emulated tests provide additional coverage, not a substitute for those reports.

## Release preparation

- [x] Review runtime/build code; fix disconnect/display-queue races and optimize inventory polling.
- [x] Run the source regression suite and focused static checks.
- [x] Set the APWorld release version to 1.0.0; preserve existing protocol/location IDs.
- [x] Provide standalone and Archipelago Launcher installation paths.
- [x] Document supported game builds, YAML settings, generation/hosting, saves and troubleshooting.
- [x] Include MIT license, game-asset attribution and dependency notices.
- [x] Exclude local room state, logs, research and historical drafts from the source export.
- [x] Verify the final standalone and embedded-client packaging checks.
- [x] Generate both recommended presets with the release APWorld.
- [x] Verify the exported source tests, archive contents and SHA-256 manifests.
- [ ] Upload the clean repository and intended release assets to GitHub.

See [the final review](release-review-2026-10-16.md) for test evidence. The last
upload step is performed separately; no repository or release has been published
by these build/export commands.
