# Commissioning Polarity Tool v0.9.1

v0.9.1 is a startup-compatibility hotfix. Use the v0.9.0 display acceptance
procedure and the v0.8.1 inspection-engine qualification procedure.

## Upgrade check

1. Back up `config.json` and the complete `runtime` directory.
2. Install or fast-forward to v0.9.1.
3. Start the application with the existing station database.
4. Confirm the Recipes page lists the same recipe names, revisions, and active
   recipe as before the upgrade.
5. Open the active recipe and confirm the conditional terminal-top geometry
   confidence value was retained.
6. Run the existing vision smoke tests.
7. Run one known-good and one known-bad inspection before returning the station
   to production.

A database reset is neither required nor recommended.
