"""Read only the explicitly simultaneous Sheffield acceleration records; combined spatial tables are excluded."""

import re
import zipfile
from pathlib import Path, PurePosixPath

import numpy as np

from .acoustics import SampledTrace
from .contracts import digest
from .datasets import file_digest


class SheffieldArchive:
    def __init__(self, path, *, asset_id="CID_MDPE_loop"):
        self.path = Path(path)
        self.asset_id = asset_id
        self.sha256 = file_digest(self.path)
        self.stat = self.path.stat()
        self.archive = zipfile.ZipFile(self.path)

    def close(self):
        self.archive.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def members(self):
        return tuple(
            i.filename
            for i in self.archive.infolist()
            if not i.is_dir()
            and "/Coherence (simultaneous recording)/" in i.filename
            and i.filename.lower().endswith(".csv")
        )

    def read_pair(self, member, *, start_index=0, sample_count=32768):
        import pandas as pd

        if (
            type(start_index) is not int
            or type(sample_count) is not int
            or start_index < 0
            or not 32 <= sample_count <= 2000000
        ):
            raise ValueError("select a nonnegative sample start and a bounded count of at least 32")
        current = self.path.stat()
        if (current.st_size, current.st_mtime_ns) != (self.stat.st_size, self.stat.st_mtime_ns):
            raise ValueError("the open source archive changed")
        logical = PurePosixPath(member)
        match = re.search(
            r"/Coherence \(simultaneous recording\)/(3[.]6mmDirect_2[.]8bar|3mmValve_2[.]8bar)/test#([1-9]|10)/[^/]+[.]csv$",
            member,
        )
        if (
            logical.is_absolute()
            or ".." in logical.parts
            or "\\" in member
            or ":" in member
            or not match
            or member not in self.members()
        ):
            raise ValueError("select an explicitly simultaneous paired record, not a combined spatial table")
        info = self.archive.getinfo(member)
        if info.file_size > 200000000:
            raise ValueError("the source member exceeds its supported byte budget")
        with self.archive.open(member) as stream:
            data = pd.read_csv(stream, skiprows=range(1, start_index + 1), nrows=sample_count)
        expected = ["Time (s)", "Accelerometer 1 (m/s^2)", "Accelerometer 2 (m/s^2)"]
        if match[1] == "3mmValve_2.8bar":
            expected.insert(1, "Hydrophone (Pa)")
        if list(data.columns) != expected or len(data) != sample_count:
            raise ValueError(
                "source headers or available sample count differ from the reviewed record schema"
            )
        data = data.apply(pd.to_numeric, errors="raise")
        clock = data["Time (s)"].to_numpy(float)
        reference = np.arange(start_index, start_index + sample_count, dtype=float) / 8192.0
        if not np.isfinite(clock).all() or not np.allclose(clock, reference, rtol=0, atol=1e-9):
            raise ValueError("source time column differs from its declared 8192 Hz elapsed clock")
        source = digest(
            {
                "archive_sha256": self.sha256,
                "member": member,
                "start_index": start_index,
                "sample_count": sample_count,
                "loader_sha256": file_digest(Path(__file__)),
            }
        )
        clock_id = f"sheffield_{info.CRC:08x}_record_elapsed"
        traces = []
        for i in (1, 2):
            values = data[f"Accelerometer {i} (m/s^2)"].to_numpy(float)
            valid = np.isfinite(values)
            traces.append(
                SampledTrace(
                    id=f"{info.CRC:08x}:acc{i}:{start_index}",
                    asset_id=self.asset_id,
                    sensor_id=f"accelerometer_{i}",
                    clock_id=clock_id,
                    start_time_s=float(clock[0]),
                    sample_period_s=1 / 8192.0,
                    quantity_id="pipe_wall_acceleration",
                    unit="m/s^2",
                    origin="laboratory",
                    source_digest=source,
                    values=tuple(float(v) if ok else None for v, ok in zip(values, valid, strict=True)),
                    valid=tuple(bool(v) for v in valid),
                )
            )
        report = {
            "archive_sha256": self.sha256,
            "member": member,
            "member_crc32": f"{info.CRC:08x}",
            "member_bytes": info.file_size,
            "loader_sha256": file_digest(Path(__file__)),
            "start_index": start_index,
            "samples_read": len(data),
            "sample_rate_hz": 8192.0,
            "time_range_s": [float(clock[0]), float(clock[-1])],
            "nominal_remote_distance_m": float(match[2]),
            "near_reference_offset_from_leak_m": None,
            "wave_speed_m_s": None,
            "relative_channel_latency_error_bound_s": None,
            "pressure_label": "2.8 bar; pressure reference unresolved",
            "quantity": "pipe_wall_acceleration",
            "unit": "m/s^2",
            "amplitude_scaling_applied": False,
            "simultaneous_recording_folder_selected": True,
            "full_member_scan_claim": False,
            "origin": "laboratory",
            "source_citation": "Mohammad Reza Shekofteh (2026); https://doi.org/10.15131/shef.data.32229270.v1",
            "license_id": "CC-BY-4.0",
        }
        return (*traces, report)
