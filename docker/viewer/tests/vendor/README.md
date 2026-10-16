# Vendored test assets (issue #67 pinning rules)

- `axe-core-4.11.4.min.js` -- sha256 fb83a4378d978ecb7d2dae48a3a3778a84c971ac692f3afd45d714fba89c0f0d.
  Provenance: byte-identical to `package/axe.min.js` from the official npm
  tarball `https://registry.npmjs.org/axe-core/-/axe-core-4.11.4.tgz`
  (verified 2026-10-15 by extracting the tarball and comparing).
  Vendored (not CDN-fetched at test time) so the a11y gate is deterministic
  and works on network-restricted runners; bump = change filename+sha+README
  line together. MPL-2.0 (see file header).
