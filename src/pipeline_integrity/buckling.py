"""Elastic proportional-load frame eigenbuckling using native owner K/Kg assembly."""

from __future__ import annotations

import inspect
import json
import math
import platform
from importlib.metadata import version
from pathlib import Path
from typing import Protocol

from pydantic import Field, model_validator

from .contracts import Contract, Name, Positive, RunContext, digest
from .datasets import file_digest
from .structural import DOFS, FrameRequest, assemble_frame_stiffness, build_native_frame, solve_frame


class BucklingRequest(Contract):
    id: Name
    frame_id: Name
    coordinate_definition_id: Name
    source_port_id: Name
    target_port_id: Name
    reference_load_definition_id: Name
    frame: FrameRequest
    combination_id: Name
    provider_id: Name = "pynite_native_frame"
    expected_provider_sha256: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")
    translation_gauge_m: Positive
    rotation_gauge_rad: Positive
    requested_modes: int = Field(default=5, ge=1, le=100)
    maximum_full_dofs: int = Field(default=240, ge=12, le=600)
    relative_symmetry_tolerance: Positive = Field(default=1e-10, le=1e-4)
    relative_positive_stiffness_floor: Positive = Field(default=1e-14, le=1e-3)
    relative_eigen_residual_tolerance: Positive = Field(default=1e-8, le=1e-3)
    relative_imaginary_tolerance: Positive = Field(default=1e-9, le=1e-3)
    absolute_prebuckling_translation_tolerance_m: Positive = 1e-10
    absolute_prebuckling_rotation_tolerance_rad: Positive = 1e-10
    conservative_proportional_reference_load_reviewed: bool = False
    perfect_linear_elastic_frame_model_reviewed: bool = False
    stiffness_provider_and_dof_basis_reviewed: bool = False
    evidence_id: Name

    @model_validator(mode="after")
    def supported_model(self):
        f = self.frame
        if self.source_port_id == self.target_port_id:
            raise ValueError("buckling ports must be distinct")
        if self.combination_id not in {c.id for c in f.combinations}:
            raise ValueError("reference combination is absent from the supplied frame")
        if f.initial_strains or f.line_loads or any(m.rotation_releases for m in f.members):
            raise ValueError(
                "this native provider supports unreleased frames with nodal reference loads and no initial strain"
            )
        if any(
            v != 0
            for s in f.supports
            for v in (*s.imposed_translations_m.values(), *s.imposed_rotations_rad.values())
        ):
            raise ValueError(
                "eigen perturbations require homogeneous supports; imposed settlement prestress is not supported"
            )
        if len(f.nodes) * 6 > self.maximum_full_dofs:
            raise ValueError("dense native eigenproblem exceeds the declared full-DOF budget")
        return self


class BucklingMatrices(Contract):
    provider_id: Name
    provider_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    reference_frame_digest: Name
    reference_record_digest: Name
    dof_order: tuple[Name, ...]
    fixed_dof_indices: tuple[int, ...]
    elastic_stiffness: tuple[tuple[float, ...], ...]
    signed_geometric_stiffness: tuple[tuple[float, ...], ...]
    provenance: dict
    prestress_audit: dict


class BucklingMatrixProvider(Protocol):
    def assemble(
        self, request: BucklingRequest, context: RunContext, reference_record: dict
    ) -> BucklingMatrices: ...


class PyNiteBucklingProvider:
    """Consume the structural owner's public factory and versioned native matrix receipt."""

    def assemble(self, request, context, reference_record):
        q = request
        selected = tuple(c for c in q.frame.combinations if c.id == q.combination_id)
        frame = q.frame.model_copy(update={"combinations": selected})
        assembly = build_native_frame(frame, context)
        if assembly.record["status"] != "ready":
            raise ValueError("shared native frame factory is not ready")
        native = assemble_frame_stiffness(
            assembly,
            q.combination_id,
            reference_record=reference_record,
            include_geometric=True,
            maximum_full_dofs=q.maximum_full_dofs,
            absolute_translation_tolerance_m=q.absolute_prebuckling_translation_tolerance_m,
            absolute_rotation_tolerance_rad=q.absolute_prebuckling_rotation_tolerance_rad,
        )
        return BucklingMatrices(
            provider_id="pynite_native_frame",
            provider_sha256=file_digest(Path(__file__)),
            reference_frame_digest=digest(q.frame),
            reference_record_digest=reference_record["record_digest"],
            dof_order=tuple(native["dof_order"]),
            fixed_dof_indices=tuple(native["fixed_dof_indices"]),
            elastic_stiffness=tuple(tuple(row) for row in native["elastic_stiffness"]),
            signed_geometric_stiffness=tuple(tuple(row) for row in native["signed_geometric_stiffness"]),
            provenance={
                "native_backend": assembly.record["native_backend"],
                "public_structural_stiffness_receipt": native,
                "assembly": "structural.build_native_frame + structural.assemble_frame_stiffness",
                "equation": "(K + lambda*Kg_reference) phi = 0",
                "Kg_sign": "native EA/L axial extension force: tension positive",
                "native_sign_source": "owner FEModel3D.Kg and Analysis.py Ke+Kg source path",
            },
            prestress_audit=native["prestress_audit"],
        )


def buckling_boundary_metadata(q):
    return {
        "id": q.id,
        "asset_id": q.frame.asset_id,
        "clock_id": q.frame.clock_id,
        "time_s": q.frame.time_s,
        "frame_id": q.frame_id,
        "coordinate_definition_id": q.coordinate_definition_id,
        "ports": [q.source_port_id, q.target_port_id],
        "reference_load_definition_id": q.reference_load_definition_id,
        "request_digest": digest(q.model_dump(mode="json")),
    }


def _inputs(request, context):
    q = BucklingRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.frame.asset_id, q.frame.clock_id) != (c.asset_id, c.clock_id) or c.metadata.get(
        "buckling_boundary"
    ) != buckling_boundary_metadata(q):
        raise ValueError("buckling frame/clock/reference load/ports context differs")
    evidence = [e.id for e in c.evidence]
    required = {q.evidence_id, q.frame.geometry_evidence_id}
    required.update(
        r.evidence_id
        for rows in (
            q.frame.sections,
            q.frame.materials,
            q.frame.supports,
            q.frame.node_loads,
            q.frame.combinations,
        )
        for r in rows
    )
    if len(evidence) != len(set(evidence)) or required - set(evidence):
        raise ValueError("buckling geometry/material/load/support/provider evidence must be present uniquely")
    return q, c


def evaluate_buckling(request, context, *, provider: BucklingMatrixProvider | None = None):
    """Compute a perfect elastic frame load factor, never a shell/collapse allowance."""
    q, c = _inputs(request, context)
    body = {
        "schema": "pipeline_integrity.buckling.v1",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "implementation_sha256": file_digest(Path(__file__)),
        "structural_owner_sha256": file_digest(Path(__file__).with_name("structural.py")),
        "runtime": {"python": platform.python_version(), "pydantic": version("pydantic")},
        "status": "needs_data",
        "reference_frame_record": None,
        "matrix_receipt": None,
        "matrix_audit": None,
        "solver": None,
        "solution": None,
        "issues": [],
        "shell_collapse_plasticity_or_imperfection_solved": False,
        "field_or_code_acceptance": False,
        "verification_state": "execution is not regression, convergence or field qualification",
    }

    def seal():
        return {**body, "record_digest": digest(body)}

    if not all(
        (
            q.conservative_proportional_reference_load_reviewed,
            q.perfect_linear_elastic_frame_model_reviewed,
            q.stiffness_provider_and_dof_basis_reviewed,
        )
    ):
        body["issues"] = [
            "reviewed conservative proportional load, perfect elastic frame and native matrix basis required"
        ]
        return seal()
    if q.provider_id != "pynite_native_frame" and (provider is None or q.expected_provider_sha256 is None):
        body["issues"] = ["external matrix provider implementation and explicit source pin required"]
        return seal()
    selected = tuple(x for x in q.frame.combinations if x.id == q.combination_id)
    frame = q.frame.model_copy(update={"combinations": selected})
    reference = solve_frame(frame, c)
    body["reference_frame_record"] = reference
    if reference["status"] != "computed":
        body.update(status=reference["status"], issues=reference["issues"])
        return seal()
    import numpy as np
    from scipy import linalg

    body["runtime"].update({name: version(name) for name in ("numpy", "scipy", "PyNiteFEA")})
    body["solver"] = {
        "id": "scipy.linalg.eig",
        "homogeneous_eigvals": True,
        "source_sha256": file_digest(Path(inspect.getfile(linalg.eig))),
    }
    try:
        active_provider = PyNiteBucklingProvider() if provider is None else provider
        if q.provider_id == "pynite_native_frame" and type(active_provider) is not PyNiteBucklingProvider:
            raise ValueError("an external callback cannot impersonate the built-in native provider")
        actual_provider_sha = file_digest(Path(inspect.getfile(type(active_provider))))
        matrices = active_provider.assemble(q, c, reference)
        matrices = BucklingMatrices.model_validate(matrices.model_dump(mode="python"))
        body["matrix_receipt"] = matrices.model_dump(mode="json")
        if (
            matrices.provider_id != q.provider_id
            or q.expected_provider_sha256 is not None
            and matrices.provider_sha256 != q.expected_provider_sha256
        ):
            raise ValueError("actual matrix provider differs from the reviewed identity/source pin")
        if matrices.provider_sha256 != actual_provider_sha:
            raise ValueError("provider-reported source hash differs from the actual callback source file")
        if (
            matrices.reference_frame_digest != digest(q.frame)
            or matrices.reference_record_digest != reference["record_digest"]
        ):
            raise ValueError("matrix receipt is not bound to the actual frame and audited reference state")
        if matrices.prestress_audit.get("reference_state_matched") is not True:
            raise ValueError("matrix provider has not confirmed its audited reference prestress state")
        expected_dofs = tuple(f"{n.id}:{dof}" for n in q.frame.nodes for dof in DOFS)
        anchors = {s.node_id: set(s.fixed_dofs) for s in q.frame.supports}
        fixed = tuple(
            i
            for i, (n, dof) in enumerate((n, d) for n in q.frame.nodes for d in DOFS)
            if dof in anchors.get(n.id, set())
        )
        if matrices.dof_order != expected_dofs or matrices.fixed_dof_indices != fixed:
            raise ValueError("provider DOF order or constrained coordinates differ from the explicit frame")
        k = np.asarray(matrices.elastic_stiffness, dtype=float)
        g = np.asarray(matrices.signed_geometric_stiffness, dtype=float)
        n = len(expected_dofs)
        if k.shape != (n, n) or g.shape != (n, n) or not np.isfinite(k).all() or not np.isfinite(g).all():
            raise ValueError("native matrices are not finite full-DOF square arrays")
        free = [i for i in range(n) if i not in fixed]
        if not free:
            raise ValueError("no free perturbation coordinates remain")
        scales = np.asarray([q.translation_gauge_m if i % 6 < 3 else q.rotation_gauge_rad for i in free])
        a = k[np.ix_(free, free)] * np.outer(scales, scales)
        b = -g[np.ix_(free, free)] * np.outer(scales, scales)
        if not np.isfinite(a).all() or not np.isfinite(b).all():
            raise ValueError("declared dimensional congruence gauge is not representable")
        norm_a, norm_b = float(linalg.norm(a)), float(linalg.norm(b))
        if not math.isfinite(norm_a) or not math.isfinite(norm_b):
            raise ValueError("scaled matrix norms are not representable")
        sym_a = float(linalg.norm(a - a.T)) / max(norm_a, np.finfo(float).tiny)
        sym_b = float(linalg.norm(b - b.T)) / max(norm_b, np.finfo(float).tiny)
        if max(sym_a, sym_b) > q.relative_symmetry_tolerance:
            raise ValueError("native elastic/geometric matrices violate conservative symmetry")
        elastic_eigenvalues = linalg.eigvalsh(a, check_finite=True)
        if not np.isfinite(elastic_eigenvalues).all() or elastic_eigenvalues[-1] <= 0:
            raise ValueError("supported elastic stiffness has no finite positive stiffness scale")
        ratio = float(elastic_eigenvalues[0] / elastic_eigenvalues[-1])
        body["matrix_audit"] = {
            "full_dofs": n,
            "free_dof_indices": free,
            "fixed_dof_indices": list(fixed),
            "relative_elastic_symmetry_error": sym_a,
            "relative_geometric_symmetry_error": sym_b,
            "gauge_translation_m": q.translation_gauge_m,
            "gauge_rotation_rad": q.rotation_gauge_rad,
            "scaled_elastic_minimum_to_maximum_eigenvalue_ratio": ratio,
            "elastic_stiffness_sha256": digest(matrices.elastic_stiffness),
            "signed_geometric_stiffness_sha256": digest(matrices.signed_geometric_stiffness),
            "automatic_gauge_constraints_or_regularization": False,
        }
        if elastic_eigenvalues[-1] <= 0 or ratio <= q.relative_positive_stiffness_floor:
            raise ValueError(
                "supported elastic stiffness is indefinite, singular or unresolved at the declared gauge"
            )
        # K is audited positive definite. Whiten the symmetric generalized
        # pencil K v = lambda B v into a real symmetric eigenproblem so
        # singular geometric-stiffness directions do not create spurious
        # complex eigenpairs in the generic nonsymmetric solver.
        cholesky = linalg.cholesky(a, lower=True, check_finite=True)
        left = linalg.solve_triangular(cholesky, b, lower=True, check_finite=True)
        transformed = linalg.solve_triangular(cholesky, left.T, lower=True, check_finite=True).T
        transformed_norm = float(linalg.norm(transformed))
        transformed_asymmetry = float(linalg.norm(transformed - transformed.T)) / max(
            transformed_norm, np.finfo(float).tiny
        )
        if not math.isfinite(transformed_asymmetry) or transformed_asymmetry > q.relative_symmetry_tolerance:
            raise ValueError("Cholesky-transformed geometric stiffness lost symmetric structure")
        transformed = (transformed + transformed.T) / 2.0
        inverse_factors, vectors = linalg.eigh(transformed, check_finite=True)
        if not np.isfinite(inverse_factors).all() or not np.isfinite(vectors).all():
            raise ValueError("native symmetric eigensolver returned nonfinite coordinates")
        inverse_factor_zero_tolerance = (
            np.finfo(float).eps * max(1, len(free)) * max(1.0, float(linalg.norm(transformed)))
        )
        body["solver"].update(
            id="scipy.linalg.cholesky+eigh",
            homogeneous_eigvals=False,
            generalized_pencil="K v = lambda B v; B whitened by K=L L^T",
            inverse_factor_zero_tolerance=inverse_factor_zero_tolerance,
            transformed_geometric_relative_asymmetry=transformed_asymmetry,
        )
        zero_modes = np.abs(inverse_factors) <= inverse_factor_zero_tolerance
        positive_mode_indices = np.flatnonzero(
            inverse_factors > inverse_factor_zero_tolerance
        )[::-1]
        counts = {
            "infinite": int(np.count_nonzero(zero_modes)),
            "nonpositive": int(np.count_nonzero(inverse_factors < -inverse_factor_zero_tolerance)),
            "positive": int(len(positive_mode_indices)),
        }
        modes = []
        # Only the lowest requested positive load factors need physical-mode
        # reconstruction and equilibrium certification; higher factors are
        # not returned as qualified modes.
        for i in positive_mode_indices[: q.requested_modes]:
            inverse_factor = float(inverse_factors[i])
            factor = 1.0 / inverse_factor
            if not math.isfinite(factor):
                raise ValueError("requested positive load factor is not representable")
            vector = linalg.solve_triangular(
                cholesky.T, vectors[:, i], lower=False, check_finite=True
            )
            v = vector.copy()
            pivot = int(np.argmax(np.abs(v)))
            if v[pivot] == 0:
                raise ValueError("native mode vector has zero norm")
            v /= v[pivot]
            denominator = (norm_a + abs(factor) * norm_b) * float(linalg.norm(v))
            if not math.isfinite(denominator) or denominator <= 0:
                raise ValueError("eigenpair residual scaling is not representable")
            residual = float(linalg.norm(a @ v - factor * (b @ v))) / denominator
            if not math.isfinite(residual) or residual > q.relative_eigen_residual_tolerance:
                raise ValueError("native generalized eigenpair residual exceeds declared tolerance")
            physical = np.zeros(n)
            physical[free] = scales * v
            uf = physical[free]
            kf, gf = k[np.ix_(free, free)], g[np.ix_(free, free)]
            original_residual = kf @ uf + factor * (gf @ uf)
            original_scale = np.abs(kf) @ np.abs(uf) + abs(factor) * (np.abs(gf) @ np.abs(uf))
            limits = (
                np.asarray(
                    [
                        q.frame.absolute_force_tolerance_n
                        if d % 6 < 3
                        else q.frame.absolute_moment_tolerance_nm
                        for d in free
                    ]
                )
                + q.relative_eigen_residual_tolerance * original_scale
            )
            if (
                not np.isfinite(original_residual).all()
                or not np.isfinite(limits).all()
                or not np.all(np.abs(original_residual) <= limits)
            ):
                normalized_residuals = np.abs(original_residual) / np.maximum(
                    limits, np.finfo(float).tiny
                )
                worst = int(np.argmax(normalized_residuals))
                raise ValueError(
                    "original free-DOF force/moment eigen-equilibrium residual failed "
                    f"(normalized residual {normalized_residuals[worst]:.6g} at DOF {free[worst]})"
                )
            modes.append(
                {
                    "load_factor": factor,
                    "relative_eigen_residual": residual,
                    "gauge_mode_vector": v.tolist(),
                    "physical_mode_by_full_dof": physical.tolist(),
                    "original_free_dof_equilibrium_residual": original_residual.tolist(),
                    "original_free_dof_equilibrium_limit": limits.tolist(),
                    "original_free_dof_equilibrium_passed": True,
                    "normalization": "largest gauge coordinate is positive one; amplitude is arbitrary",
                }
            )
        modes.sort(key=lambda m: m["load_factor"])
        body["solution"] = {
            "equation": "K phi = lambda*(-Kg_reference) phi",
            "critical_positive_load_factor": modes[0]["load_factor"] if modes else None,
            "modes": modes[: q.requested_modes],
            "eigenvalue_counts": counts,
            "reference_pattern_scaled_proportionally": q.reference_load_definition_id,
        }
        body["status"] = "computed_diagnostic" if modes else "no_positive_eigenvalue_in_supplied_model"
        digest(body)
    except (ArithmeticError, ValueError, RuntimeError, np.linalg.LinAlgError) as exc:
        body.update(status="out_of_scope", solution=None, issues=[str(exc)])
    return seal()


def verify_buckling_record(record, *, request=None, context=None, provider=None):
    """Reassemble through the actual provider and solve; unsigned replay is not authentication."""
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {k: v for k, v in record.items() if k != "record_digest"}
    ):
        raise ValueError("buckling record seal changed")
    q = BucklingRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
    c = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
    if request is not None and request != q or context is not None and context != c:
        raise ValueError("buckling caller boundary differs")
    if evaluate_buckling(q, c, provider=provider) != record:
        raise ValueError(
            "buckling matrices, modes, gauge, supports, reference state or source pins differ on replay"
        )
    return True
