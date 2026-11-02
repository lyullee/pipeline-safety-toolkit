"""Reference changes are tested in a child so no global fluid state leaks."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline_integrity.thermo import CoolPropProvider, coolprop_reference_anchors


@pytest.mark.parametrize("fluids", [(), [], ("Hydrogen", "Hydrogen"), ([],), ("",), (None,)])
def test_anchor_identifiers_are_explicit_distinct_fluids(fluids):
    with pytest.raises(ValueError):
        coolprop_reference_anchors(fluids)


@pytest.mark.parametrize("flag", [None, 0, "false"])
def test_uncached_property_option_is_not_implicitly_coerced(flag):
    with pytest.raises(TypeError):
        CoolPropProvider(use_cache=flag)


def test_h_and_s_runtime_reference_change_refuses_old_native_pins():
    root = Path(__file__).resolve().parents[1]
    script = r"""
import json,runpy,sys
from pathlib import Path
root=Path.cwd()
sys.path.insert(0,str(root/'src'))
owner=root.parent/'tmp/analysis/repos/lh2dt_models/src'
if owner.is_dir():sys.path.insert(0,str(owner))
import CoolProp.CoolProp as CP
from pipeline_integrity.thermo import CoolPropProvider,StateRequest,coolprop_reference_anchors
from pipeline_integrity.compressor_train import evaluate_compressor_train
from pipeline_integrity.phase_change_exchange import evaluate_phase_change
q,c=runpy.run_path('examples/compressor_train.py')['make_case'](reviewed=True)
p,k=runpy.run_path('examples/phase_change_exchange.py')['make_case'](reviewed=True,fluid='Hydrogen')
provider=CoolPropProvider(use_cache=False)
state=StateRequest(fluid='Hydrogen',inputs={'P':100000.,'T':300.})
before=provider.resolve(state)
markers=coolprop_reference_anchors()
rho=CP.PropsSI('Dmolar','P',100000.,'T',300.,'Hydrogen')
CP.set_reference_state('Hydrogen',300.,rho,12345.,67.89)
after=provider.resolve(state)
new=coolprop_reference_anchors()
assert markers['Hydrogen']['specific_enthalpy_J_kg']!=new['Hydrogen']['specific_enthalpy_J_kg']
assert markers['Hydrogen']['specific_entropy_J_kg_K']!=new['Hydrogen']['specific_entropy_J_kg_K']
assert before.enthalpy_j_kg!=after.enthalpy_j_kg and before.entropy_j_kg_k!=after.entropy_j_kg_k
assert evaluate_compressor_train(q,c)['status']=='out_of_scope'
try:evaluate_phase_change(p,k)
except ValueError:pass
else:raise AssertionError('old phase reference accepted')
print(json.dumps({'h_changed':True,'s_changed':True,'uncached_provider_changed':True,'old_compressor_pins_refused':True,'old_phase_pins_refused':True}))
"""
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script], cwd=root, text=True, capture_output=True, check=True
    )
    assert all(json.loads(result.stdout).values())
