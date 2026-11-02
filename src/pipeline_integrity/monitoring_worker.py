"""One JSON request per process; optional monitoring dependencies remain isolated."""

import json
import sys


def main():
    from .monitoring_exchange import NativeMonitoringClient

    sys.dont_write_bytecode = True
    try:
        raw = sys.stdin.buffer.read(64000001)
        if len(raw) > 64000000:
            raise ValueError("monitoring worker request budget exceeded")
        value = json.loads(raw)
        client = NativeMonitoringClient(value["source_root"])
        if value["operation"] == "describe":
            result = client.describe(value["method"], expected_source_sha256=value["expected_source_sha256"])
        elif value["operation"] == "run":
            result = client.run(value["recipe"], value["splits"], value["context"])
        else:
            raise ValueError("unsupported monitoring worker operation")
        response = dict(status="ok", result=result)
    except Exception as error:
        response = dict(status="error", error_type=type(error).__name__, private_error=str(error)[:20000])
    print(json.dumps(response, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
