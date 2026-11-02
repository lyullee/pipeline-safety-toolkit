"""Manufactured elastic fracture pressure curve and independently inverted bound."""
import math
import numpy as np
import pytest
from pipeline_integrity.surface_flaw_proof import SurfaceFlawProofRequest, evaluate_surface_flaw_proof

@pytest.mark.parametrize('toughness',[10e6,20e6,80e6])
def test_proof_depth_bounds_converge_to_independent_elastic_crack_energy_limit(toughness):
    wall=.02;radius=.3;proof_depth=.0037;operating_depth=.0083
    def critical_pressure(depth):
        # A supplied constant geometry factor defines this manufactured elastic
        # crack family; it is not a calibration for arbitrary pipe surface flaws.
        return toughness*wall/(radius*math.sqrt(math.pi*depth))
    proof=critical_pressure(proof_depth);operating=critical_pressure(operating_depth);errors=[]
    for n in (17,33,65,129):
        depths=np.linspace(.0005,.015,n)
        q=SurfaceFlawProofRequest(basis_id='manufactured-LEFM',flaw_family_id='constant-geometry-elastic-crack',
            material_condition_id='supplied-toughness',wall_thickness_m=wall,depth_m=tuple(depths),
            failure_pressure_pa=tuple(critical_pressure(float(a)) for a in depths),proof_pressure_pa=proof,
            maximum_operating_pressure_pa=operating,monotonic_elastic_proof_and_same_flaw_family_reviewed=True,
            failure_pressure_curve_applicability_reviewed=True)
        r=evaluate_surface_flaw_proof(q)
        assert r['status']=='computed' and r['proof_bound_status']=='supported_boundary'
        a=r['proof_limited_initial_depth_m'];b=r['operating_failure_depth_m']
        assert a<b<wall
        errors.append(max(abs(a-proof_depth),abs(b-operating_depth)))
        # Independently reconstruct elastic energy release at the exact bound.
        E=200e9;stress=proof*radius/wall
        assert math.pi*stress*stress*proof_depth/E==pytest.approx(toughness*toughness/E)
        assert not r['proof_induced_plasticity_or_tearing_predicted']
    assert all(fine<coarse for coarse,fine in zip(errors,errors[1:]))
    assert errors[-1]<1e-6
