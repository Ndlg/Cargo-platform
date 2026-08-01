# 1.0.0-rc.1 validation rollback checkpoint

This checkpoint applies only to the isolated `cargo-platform-validation` stack
on ports 6173/6174.

## Frozen live stack (must remain unchanged)

- backend: `eb42223185ae`
- tenant UI: `5ed09f8a74fc`
- parser: `5d76b24c5040`
- admin UI: `ea7b3ad8827d`
- data volume: `cargo-platform-data`

## Previous validation images

- backend: `sha256:9cf468c2e7e1dbeceaff0acb70b712f48054057437f63eec84f643036bcda3e1`
- tenant UI: `sha256:94a59a8eb203a7b89b879982342e50fa7713e30aab16688096ebd17a03badf15`
- parser: `sha256:f0c5ec997675deb2c18166db168d2e397185a89e3a98855f3292ad8b5627183f`
- admin UI: `sha256:88b014ee162174dddab0322c9e4309a76036960c39d942bafd662e9c61111dbc`
- data volume: `cargo-platform-validation-no-model-20260801-153819`

## Verified database snapshot

- file: `C:\Users\ndlgx\Documents\Projects\GitHub\Ndlg\cargo-platform\.worktrees\cargo-platform-validation-backups\cargo-platform-validation-no-model-20260801-153819-20260802-000053219.db`
- SHA-256: `00f7ae1c5765d37b2f2b33daf35d76ca5fa58d557f25b7b5fa805effcd24311d`
- SQLite integrity: `ok`
- size: `67297280` bytes

Image rollback is attempted before database restore. Database restore is only
performed with all validation containers stopped and the recorded SHA-256.
