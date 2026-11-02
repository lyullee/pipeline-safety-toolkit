import pytest

from pipeline_integrity.water import WaterRun, simulate_water_network


def test_water_network_mass_and_head_budget(tmp_path):
    wntr = pytest.importorskip("wntr")
    model = wntr.network.WaterNetworkModel()
    model.add_reservoir("supply", base_head=100.0)
    model.add_junction("consumer", base_demand=0.01, elevation=10.0)
    model.add_pipe("pipe", "supply", "consumer", length=100.0, diameter=0.2, roughness=120.0, minor_loss=0.0)
    model.options.hydraulic.headloss = "H-W"
    source = tmp_path / "authored_network.inp"
    wntr.network.write_inpfile(model, str(source))
    output = simulate_water_network(source, WaterRun())
    assert output["status"] == "computed"
    assert output["flow_m3_s"]["pipe"][0] == pytest.approx(0.01, abs=1e-8)
    assert output["maximum_junction_continuity_residual_m3_s"] < 1e-8
    assert 0 < output["pressure_head_m"]["consumer"][0] < 90
    options = output["native_diagnostics"]["effective_hydraulic_options_si"]
    assert options["flow_change_m3_s"] == pytest.approx(1e-9, rel=1e-12)
    assert options["relative_accuracy"] == pytest.approx(1e-8, rel=1e-12)
    assert options["head_error_m"] == pytest.approx(1e-7, rel=1e-12)
    assert output["native_diagnostics"]["parsed_hydraulic_options_si"]["relative_accuracy"] == 1e-5


def test_fractional_epanet_time_is_not_silently_rounded(tmp_path):
    wntr = pytest.importorskip("wntr")
    model = wntr.network.WaterNetworkModel()
    source = tmp_path / "empty.inp"
    wntr.network.write_inpfile(model, str(source))
    with pytest.raises(ValueError, match="whole seconds"):
        simulate_water_network(source, WaterRun(duration_s=0.5))
