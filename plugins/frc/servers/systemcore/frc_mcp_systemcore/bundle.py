"""The support bundle: generate it on the controller, wait, download it once, check it.

The controller's `limelight_supportbundle` service (port 4808, no login): POST /api/support/generate -> {"jobId"}; poll
GET /api/support/status/<id> until `state` is done or error; one GET /api/support/download/<id>. The download is one-shot and
ignores Range, so a failed transfer means generating again. A disk-full controller answers 507. The zip holds readable
journals for the last boots and an encrypted manifest only Limelight can open.
"""

import asyncio
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from . import net
from .errors import SystemcoreError

POLL_S = 1.0


def bundle_name(host: str, now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"support-bundle-{host.replace(':', '_')}-{stamp}.zip"


def inspect_zip(path: Path) -> dict[str, Any]:
    """Check the zip is whole and list what is in it."""
    try:
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            names = archive.namelist()
    except zipfile.BadZipFile as e:
        raise SystemcoreError(f"The downloaded bundle is not a valid zip ({e}). The download is one-shot: generate it again.") from e
    if bad is not None:
        raise SystemcoreError(f"The downloaded bundle is damaged ({bad}). The download is one-shot: generate it again.")
    return {"members": names[:20], "member_count": len(names)}


async def fetch(host: str, dest_dir: Path, *, symptoms: str, include_ds_log: bool, wait_s: float) -> dict[str, Any]:
    """Generate, wait up to `wait_s`, download to `dest_dir`, verify. Raises with a hint on every failure."""
    started = time.monotonic()
    job = await net.request_json(host, "bundle", "/api/support/generate", method="POST",
                                 body={"symptoms": symptoms, "lastOccurred": "", "includeDsLog": include_ds_log})
    job_id = job.get("jobId") if isinstance(job, dict) else None
    if not isinstance(job_id, str) or not job_id.replace("-", "").isalnum():
        raise SystemcoreError(f"The support service did not return a job id: {job!r}"[:300])
    state: dict[str, Any] = {}
    while time.monotonic() - started < wait_s:
        state = await net.request_json(host, "bundle", f"/api/support/status/{job_id}")
        if state.get("state") == "error":
            raise SystemcoreError(f"The Systemcore could not build the bundle: {state.get('error') or state}"[:400])
        if state.get("state") == "done":
            break
        await asyncio.sleep(POLL_S)
    else:
        raise SystemcoreError(f"The bundle was not ready after {wait_s:g} s (last state {state.get('state')!r}, step {state.get('step')!r}). "
                              "Try again with a longer wait_s; the job keeps running on the Systemcore.")
    dest = dest_dir / bundle_name(host)
    size = await net.download(host, "bundle", f"/api/support/download/{job_id}", dest)
    return {"path": str(dest), "size": size, "seconds": round(time.monotonic() - started, 1), **inspect_zip(dest)}
