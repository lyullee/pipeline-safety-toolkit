"""Independent exact-rational and high-precision residuals of scalar laws."""
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
import os
import runpy

import matintegrity
import pytest

CREEP=runpy.run_path('tests/test_creep_time_fraction_physical_qualification.py')['compute']
MINER=runpy.run_path('tests/test_miner_damage_physical_qualification.py')['evaluate']
PRELOAD=runpy.run_path('tests/test_bolt_preload_physical_qualification.py')['solve']
BLOCKS=[((2,7,13),(11,29,31)),((0,0),(17,23)),((30,60),(10,20)),
        ((10**12,1,500),(3*10**12,7,3001)),((1,2,3,4),(7,11,13,17)),((1,1,1),(3,3,3))]


def test_selected_immutable_owner_path_is_actual_import():
    assert Path(matintegrity.__file__).resolve().is_relative_to(Path(os.environ['PIPELINE_SELECTED_MATERIAL_SOURCE_ROOT']).resolve())


@pytest.mark.parametrize('exposure,life',BLOCKS)
def test_creep_exact_rational_damage_and_unclipped_budget(exposure,life):
    r=CREEP(exposure,life);assert r['status']=='computed'
    # Integer-rational accumulation has neither the production float division
    # nor its reduction order; the exact sum also preserves usage above unity.
    exact=sum((Fraction(a,b) for a,b in zip(exposure,life)),Fraction())
    assert r['values']['time_fraction_damage']==pytest.approx(float(exact),rel=4e-15,abs=1e-16)
    assert r['probability_semantics']=={}


@pytest.mark.parametrize('counts,lives',BLOCKS)
def test_miner_exact_rational_usage_margin_and_limit_residual(counts,lives):
    limit=.625;r=MINER(list(counts),list(lives),limit)
    exact=sum((Fraction(a,b) for a,b in zip(counts,lives)),Fraction())
    assert r['damage']==pytest.approx(float(exact),rel=4e-15,abs=1e-16)
    with localcontext() as c:
        c.prec=75
        damage=Decimal(str(r['damage']));margin=Decimal(str(r['damage_margin']))
        assert abs(damage+margin-Decimal(str(limit)))<=Decimal('2e-15')*max(1,abs(damage))
        ratio=Decimal(str(r['fraction_of_declared_limit']))
        assert abs(ratio*Decimal(str(limit))-damage)<=Decimal('2e-15')*max(1,abs(damage))


@pytest.mark.parametrize('torque,diameter,factor,area',[(127.,.023,.17,.00021),(3.2e5,.11,.31,.0037),(0.,.02,.2,.0003)])
def test_preload_high_precision_torque_and_traction_residual(torque,diameter,factor,area):
    r=PRELOAD(torque,diameter,factor,area)
    with localcontext() as c:
        c.prec=75
        T,d,K,A=(Decimal(str(x)) for x in (torque,diameter,factor,area))
        force=Decimal(str(r['preload_N']));stress=Decimal(str(r['nominal_tensile_stress_Pa']))
        assert abs(K*d*force-T)<=Decimal('2e-15')*max(1,abs(T))
        assert abs(stress*A-force)<=Decimal('2e-15')*max(1,abs(force))
