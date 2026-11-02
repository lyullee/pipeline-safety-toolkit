"""Synthetic XML verifies units, nomination meaning and bounded archive parsing."""

import zipfile

import pytest

from pipeline_integrity.gaslib import read_gaslib

NETWORK = """<network xmlns="http://gaslib.zib.de/Gas" xmlns:f="http://gaslib.zib.de/Framework">
<!-- http://creativecommons.org/licenses/by/3.0/ -->
<f:nodes><source id="a" x="200" y="0"><height value="0" unit="m"/></source>
<sink id="b" x="600" y="0"><height value="0" unit="m"/></sink></f:nodes>
<f:connections><pipe id="p" from="a" to="b"><length value="55" unit="km"/>
<diameter value="500" unit="mm"/><roughness value="0.1" unit="mm"/>
<heatTransferCoefficient value="2" unit="W_per_m_square_per_K"/></pipe></f:connections></network>"""
SCENARIO = """<boundaryValue xmlns="http://gaslib.zib.de/Gas">
<!-- http://creativecommons.org/licenses/by/3.0/ -->
<scenario id="n"><node id="a" type="entry"><flow bound="lower" value="160" unit="1000m_cube_per_hour"/>
<flow bound="upper" value="160" unit="1000m_cube_per_hour"/></node>
<node id="b" type="exit"><flow bound="lower" value="160" unit="1000m_cube_per_hour"/>
<flow bound="upper" value="160" unit="1000m_cube_per_hour"/></node></scenario></boundaryValue>"""


def archive(tmp_path, network=NETWORK, scenario=SCENARIO, extra=False):
    path = tmp_path / "original.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("original.net", network)
        z.writestr("original.scn", scenario)
        if extra:
            z.writestr("second.scn", scenario)
    return path


def test_geometry_uses_declared_units_and_keeps_nomination_and_plotting_meaning(tmp_path):
    result = read_gaslib(archive(tmp_path))
    pipe = result["pipes"]["p"]
    assert pipe["length_m"] == 55000.0 and pipe["diameter_m"] == 0.5
    assert pipe["roughness_m"] == 0.0001
    assert pipe["thermal_source_parameters"]["heatTransferCoefficient"]["value"] == 2.0
    assert result["nodes"]["a"]["plotting_coordinates"]["x"] == "200"
    assert result["nominations"]["n"]["a"]["bounds"][0]["unit"] == "1000m_cube_per_hour"
    assert result["inline_cc_by_3_0_markers_present"]
    assert result["complete_network_run_status"] == "needs_data"
    assert not result["gas_composition_inferred"] and not result["nomination_is_measured_time_history"]


@pytest.mark.parametrize("problem", ["unit", "duplicate", "dtd", "nonfinite"])
def test_ambiguous_or_executable_source_is_rejected(tmp_path, problem):
    network, scenario = NETWORK, SCENARIO
    if problem == "unit":
        network = network.replace('value="55" unit="km"', 'value="55" unit="unspecified"')
    elif problem == "duplicate":
        network = network.replace('sink id="b"', 'sink id="a"')
    elif problem == "dtd":
        network = '<!DOCTYPE network [<!ENTITY x "expanded">]>' + network
    else:
        scenario = scenario.replace('value="160"', 'value="inf"')
    with pytest.raises(ValueError):
        read_gaslib(archive(tmp_path, network, scenario))


def test_source_budget_and_multiple_scenarios_need_explicit_selection(tmp_path):
    path = archive(tmp_path, extra=True)
    with pytest.raises(ValueError, match="unambiguous"):
        read_gaslib(path)
    with pytest.raises(ValueError, match="budget"):
        read_gaslib(path, scenario_member="original.scn", maximum_xml_bytes=1)
    assert read_gaslib(path, scenario_member="second.scn")["scenario_member"] == "second.scn"
