"""Shared EPANET 2.2 execution and native diagnostics; no hydraulic or transport solver is duplicated."""

import ctypes
import math
from pathlib import Path
from tempfile import TemporaryDirectory

from .datasets import file_digest


def _project_value(engine, function_name, output_type, index=None):
    project = getattr(engine, "_project", None)
    if project is None or not project.value:
        raise RuntimeError("the inspected WNTR EPANET 2.2 project handle is unavailable")
    fn = getattr(engine.ENlib, function_name)
    args = [ctypes.c_void_p(project.value)]
    types = [ctypes.c_void_p]
    if index is not None:
        args.append(index)
        types.append(ctypes.c_int)
    value = output_type()
    args.append(ctypes.byref(value))
    types.append(ctypes.POINTER(output_type))
    fn.argtypes, fn.restype = types, ctypes.c_int
    code = fn(*args)
    if code:
        raise RuntimeError(f"EPANET {function_name} returned error {code}")
    return value.value


def solve_epanet(model, *, quality_statistics=False, maximum_hydraulic_periods=1000000):
    import wntr

    with TemporaryDirectory(prefix="pipeline_quality_") as folder:
        prefix = Path(folder) / "case"
        inp, report, binary = (str(prefix.with_suffix(suffix)) for suffix in (".inp", ".rpt", ".bin"))
        model.options.hydraulic.inpfile_pressure_units = "METERS"
        model.options.report.report_filename = None
        # WNTR 1.5 writes these convergence options without converting their units.
        si_flow_limit = model.options.hydraulic.flowchange
        try:
            model.options.hydraulic.flowchange = si_flow_limit * 1000.0
            wntr.network.write_inpfile(model, inp, units="LPS", version=2.2)
        finally:
            model.options.hydraulic.flowchange = si_flow_limit
        encoded_digest = file_digest(Path(inp))
        engine = wntr.epanet.toolkit.ENepanet(version=2.2)
        opened, hydraulic_open, warnings = False, False, []
        periods, warning_count = 0, 0
        maxima = {"iterations": 0.0, "relative_error": 0.0, "head_error_m": 0.0, "flow_change_m3_s": 0.0}
        finite_statistics = True
        try:
            engine.ENopen(inp, report, binary)
            opened = True
            requested = {
                "maximum_trials": (0, float(model.options.hydraulic.trials), 1.0),
                "relative_accuracy": (1, model.options.hydraulic.accuracy, 1.0),
                "head_error_m": (5, model.options.hydraulic.headerror, 1.0),
                "flow_change_m3_s": (6, si_flow_limit, 1000.0),
            }
            parsed, effective = {}, {}
            setter = engine.ENlib.EN_setoption
            setter.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_double]
            setter.restype = ctypes.c_int
            for key, (index, target, factor) in requested.items():
                parsed[key] = _project_value(engine, "EN_getoption", ctypes.c_double, index) / factor
                # The INP parser clamps accuracy below 1e-5; the public setter supports 1e-8.
                code = setter(ctypes.c_void_p(engine._project.value), index, target * factor)
                if code:
                    raise RuntimeError(f"EPANET could not apply the requested hydraulic option {key}: {code}")
                effective[key] = _project_value(engine, "EN_getoption", ctypes.c_double, index) / factor
                if not math.isclose(effective[key], target, rel_tol=1e-12, abs_tol=0.0):
                    raise RuntimeError(f"EPANET did not preserve the requested hydraulic option {key}")
            engine.ENopenH()
            hydraulic_open = True
            engine.ENinitH(1)
            while True:
                if periods >= maximum_hydraulic_periods:
                    raise ValueError("the native hydraulic-period budget was exhausted")
                elapsed = engine.ENrunH()
                if engine.errcode:
                    warning_count += 1
                    if len(warnings) < 32:
                        warnings.append(
                            {"stage": "ENrunH", "code": engine.errcode, "elapsed_time_s": elapsed}
                        )
                periods += 1
                for key, index, factor in (
                    ("iterations", 0, 1.0),
                    ("relative_error", 1, 1.0),
                    ("head_error_m", 2, 1.0),
                    ("flow_change_m3_s", 3, 0.001),
                ):
                    value = _project_value(engine, "EN_getstatistic", ctypes.c_double, index) * factor
                    finite_statistics = finite_statistics and math.isfinite(value)
                    if math.isfinite(value):
                        maxima[key] = max(maxima[key], value)
                if engine.ENnextH() == 0:
                    break
            hydraulic_elapsed = elapsed
            engine.ENcloseH()
            hydraulic_open = False
            engine.ENsolveQ()
            if engine.errcode:
                warning_count += 1
                if len(warnings) < 32:
                    warnings.append({"stage": "ENsolveQ", "code": engine.errcode})
            # EN_MASSBALANCE=4 is the EPANET 2.2 public API statistic, not a demand-reduction statistic.
            ratio = (
                _project_value(engine, "EN_getstatistic", ctypes.c_double, 4) if quality_statistics else None
            )
            times = {
                name: _project_value(engine, "EN_gettimeparam", ctypes.c_long, index)
                for name, index in (
                    ("duration_s", 0),
                    ("hydraulic_timestep_s", 1),
                    ("quality_timestep_s", 2),
                    ("pattern_timestep_s", 3),
                    ("pattern_start_s", 4),
                    ("report_timestep_s", 5),
                    ("report_start_s", 6),
                    ("quality_elapsed_s", 12),
                    ("hydraulic_halt_flag", 13),
                )
            }
            native_version = ctypes.c_int()
            get_version = engine.ENlib.EN_getversion
            get_version.argtypes, get_version.restype = [ctypes.POINTER(ctypes.c_int)], ctypes.c_int
            if get_version(ctypes.byref(native_version)):
                raise RuntimeError("the EPANET native version could not be read")
            engine.ENreport()
            library = Path(engine.ENlib._name)
            diagnostics = {
                "mass_balance_ratio": ratio if ratio is not None and math.isfinite(ratio) else None,
                "native_version_code": native_version.value,
                "effective_time_parameters": times,
                "warnings": warnings,
                "warning_count": warning_count,
                "warning_details_truncated": warning_count > len(warnings),
                "hydraulic_periods": periods,
                "hydraulic_elapsed_s": hydraulic_elapsed,
                "maximum_native_hydraulic_statistics": maxima if finite_statistics else None,
                "parsed_hydraulic_options_si": parsed,
                "effective_hydraulic_options_si": effective,
                "hydraulic_options_verified": True,
                "native_options_reapplied_after_INP_parse": True,
                "native_library_sha256": file_digest(library),
                "serialized_model_sha256": encoded_digest,
                "binding_scope": "WNTR_ENepanet_project_handle_and_EPANET_2_2_public_C_API",
                "serialization_flow_units": "LPS",
                "serialization_pressure_units": "METERS",
            }
        finally:
            if hydraulic_open:
                engine.ENcloseH()
            if opened:
                engine.ENclose()
        result = wntr.epanet.io.BinFile().read(
            binary, convergence_error=True, darcy_weisbach=model.options.hydraulic.headloss == "D-W"
        )
    return result, diagnostics
