# dist

Release snapshots: **do not edit them manually or rebuild an existing version under the same filename**. Source updates do not rewrite these artifacts.

| File | Size (bytes) | SHA256 | Notes |
|---|---:|---|---|
| `qwenpaw-dsh-1.1.2-plugin.zip` | 209342 | `d73c8a294e7948c01d01bda616e9aeab25e2d02285e04f34d32ec58188f98847` | 2026-10-28 host-version gate fix for QwenPaw 2.3.0b1; members match its release source |
| `qwenpaw-dsh-1.1.1-plugin.zip` | 207452 | `6a7bffb1f58adcdd8b41cdbe83d4d4254454e29570fe9638cafbaa1b7a0af5af` | 2026-10-25 tool-mapping patch; members match its release source |
| `qwenpaw-dsh-1.1.0-plugin.zip` | 203101 | `527ea24b2219f8018a3da80e10af6f56c7933f4e428eea761a50ae624628971d` | Historical v1.1.0 snapshot from 2026-10-25 |
| `qwenpaw-dsh-1.0.1-plugin.zip` | 38873 | `2689d89a9cd62d748582185c07ac2654021fe9c1cced57d5c64ace0877bf4a2d` | v1.0.1 snapshot released on 2026-10-21 |
| `qwenpaw-dsh-1.0.0-plugin.zip` | 37965 | `8ef56414fa53930cedec76ee890c1c95182f767651797111145013bb8da38c92` | Historical v1.0.0 asset |

## Why old packages must not be rebuilt

Each ZIP captures a release at a particular point in time. `qwenpaw-dsh-1.0.1-plugin.zip` **does not contain `LICENSE`**, and its `plugin.py` does not accept the host's `plugin_id=` argument (before G1). It does not represent current source. Rebuilding the same ZIP filename would give one version two different byte contents, undermining release traceability. Leave historical ZIPs unchanged.

A read-only check of an old package with the new checker against current source is expected to fail: missing LICENSE, old `plugin.py`, different source bytes, and permissions. Run the following from `dist/`:

```sh
python ../tools/plugin_package.py check \
  --zip qwenpaw-dsh-1.0.1-plugin.zip --source .. --expected-version 1.0.1
```

This does not mean the old package is corrupt; it was never a snapshot of current source. **Do not rename or rebuild a failed package and present it as an official release.**

## Checking the current release package

Run from `dist/` in the matching v1.1.2 release checkout:

```sh
python ../tools/plugin_package.py check \
  --zip qwenpaw-dsh-1.1.2-plugin.zip --source .. --expected-version 1.1.2
```

The release package's 21 members match its release source byte for byte, with fixed `1980-01-01` timestamps, `0644` permissions, and no directory entries. Rebuilding identical source should produce the same SHA256. Later documentation changes on main can change packaged README bytes: compare the ZIP with its matching tag, not a newer checkout.

## Building the next version (maintainer approval required)

1. Update documentation and `CHANGELOG.md`, then bump `../qwenpaw-dsh/plugin.json` through the release workflow. **The manifest is authoritative; do not hardcode an old version in scripts.**
2. Build from an explicit checkout to a **new filename** with the allowlisted packager. It rejects existing outputs and outputs inside the source tree. From `dist/`:

   ```sh
   VERSION=$(python3 -c 'import json;print(json.load(open("../qwenpaw-dsh/plugin.json"))["version"])')
   python ../tools/plugin_package.py build \
     --source .. \
     --output /path/outside/repo/qwenpaw-dsh-${VERSION}-plugin.zip \
     --expected-version "${VERSION}"
   ```

3. Use the same tool to verify members, versions, LICENSE, byte equality, and shutdown callback compatibility:

   ```sh
   python ../tools/plugin_package.py check \
     --zip /path/outside/repo/qwenpaw-dsh-${VERSION}-plugin.zip \
     --source .. --expected-version "${VERSION}"
   ```

4. Commit/upload a new ZIP only as part of a maintainer-approved release. Do not overwrite historical ZIPs. The artifact filename, tag, and manifest version must agree.

## Package allowlist

`LICENSE`, `README.md`, `plugin.json`, `plugin.py`, and 17 module files under `qwenpaw_dsh/`: 21 files total. Tests, caches, state, logs, credentials, reports, and symlinks are excluded. `LICENSE` must be byte-identical to the repository root's `../LICENSE`.

Temporary verification packages may be built outside the repository, but **are not release assets**. Do not commit them, include them in delivery bundles, or label them as official releases.
