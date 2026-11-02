"""Fixed-rate wall-loss forecast against exact thickness and exhaustion boundaries."""
import pytest
from matintegrity.models.corrosion import UniformForecast,uniform_forecast

def run(current,required,rate,horizon):
 return uniform_forecast(UniformForecast(current_thickness_m=current,required_thickness_m=required,
    rate_m_s=rate,horizon_s=horizon))

def test_linear_wall_loss_and_time_to_required_thickness_match_closed_form():
 result=run(.01,.007,1e-10,1e7)
 assert result.status=='computed'
 assert result.values['thickness_margin_m']==pytest.approx(.003)
 assert result.values['predicted_thickness_m']==pytest.approx(.009)
 assert result.values['forecast_margin_m']==pytest.approx(.002)
 assert result.values['time_to_required_s']==pytest.approx(3e7)

def test_zero_loss_has_no_finite_crossing_and_preserves_wall():
 result=run(.01,.007,0.,1e12)
 assert result.status=='computed'
 assert result.values['predicted_thickness_m']==pytest.approx(.01)
 assert result.values['time_to_required_s'] is None

def test_exact_or_negative_wall_exhaustion_is_withheld():
 for horizon in (1e7,1.01e7):
  result=run(.01,.001,1e-9,horizon)
  assert result.status=='out_of_scope'
  assert result.values['predicted_thickness_m'] is None
  assert result.values['forecast_margin_m'] is None
