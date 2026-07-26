# `shared/` — code both deployables import

`backend/` and `workers/` ship as separate trees with no common import root,
so anything both need used to get forked. They do share **one virtualenv** on
the host, so `algorand_shared` is put on that venv's path with a `.pth` file
rather than pip-installed: no build backend, no package index, and it survives
a release rsync because the path (`releases/current/shared`) is stable.

Wiring:
- prod  — `deploy/scripts/link_shared.sh`, run by `deploy.sh` before restart
- local — same script, pointed at `backend/.venv` and `workers/.venv`

Keep it narrow: pure logic plus injected configuration. Nothing in here may
import `app.*` from either service.
