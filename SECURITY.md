# Security Policy

## Supported versions

Pole Position is deployed per station. Only the current release receives fixes;
older station installations are expected to be upgraded through the documented
installer path rather than patched in place. The current version is recorded in
[`BUILD_NOTES.md`](BUILD_NOTES.md) and `pyproject.toml`.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Report it privately through GitHub's **Report a vulnerability** button under the
repository's Security tab, which opens a private advisory visible only to the
maintainers. If private reporting is not enabled on this repository, contact the
repository owner directly and ask for a private channel before sending details.

Useful details: affected version, the station configuration involved (camera and
PLC backends, frozen installer or source), and what an attacker would gain.

## Scope notes specific to this application

- **The application carries no safety rating.** Bypass, heartbeat, and
  inspection results are operational quality signals. Interlock enforcement
  belongs to PLC ladder logic. A report that assumes the HMI is a safety device
  is a documentation question, not a vulnerability.
- **Station data is local.** Configuration, recipes, evidence, and audit records
  live on the station filesystem under the documented data directory. The
  application exposes no network service of its own; it initiates connections to
  a camera and a PLC.
- **`SHA256SUMS.txt` establishes integrity, not provenance.** A matching
  checksum proves bytes match that manifest. It does not establish that the
  manifest came from a trusted signer, and it is not a substitute for
  organizational code signing of the installer.
- **Installers are unsigned by default.** Production distribution should use
  organizational code signing; see the README's known-limitations section.
- **Model weights are never bundled.** A report about weights shipped in the
  installer would indicate a build error worth reporting.
