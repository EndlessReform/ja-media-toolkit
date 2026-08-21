"""Environment-owned command fixture for the transport-neutral worker seam."""

from datetime import UTC, datetime
import json
import os
from pathlib import Path


request = json.loads(Path(os.environ["JA_MEDIA_WORK_REQUEST"]).read_text())
payload = request["payload"]
result = {
    "contract_version": 1,
    "request_id": request["request_id"],
    "status": "succeeded",
    "completed_at": datetime.now(UTC).isoformat(),
    "result": {
        "operation": "vad_segments",
        "locator": payload["locator"],
        "output_fingerprint": "output-fixture",
        "chunks": [{
            "object": {
                "bucket": "worker-output",
                "key": payload["output_prefix"] + "/000.flac",
                "etag": "etag-output",
                "bytes": 1024,
            },
            "start_seconds": 0,
            "end_seconds": 12.5,
        }],
        "elapsed_seconds": 0.1,
    },
}
print(json.dumps(result))
