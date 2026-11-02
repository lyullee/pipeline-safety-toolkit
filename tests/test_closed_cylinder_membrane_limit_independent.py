"""Additional thin-geometry cap-force and membrane-limit study of the existing owner."""
import pytest

from pipeline_integrity.contracts import EvidenceRef,Pressure,RunContext
from pipeline_integrity.material_exchange import PressureSection,evaluate_pressure_section
from pipeline_integrity.nominal_pipe_pressure import NominalHoopStressInput,nominal_hoop_stress_Pa


def test_closed_annulus_thin_limit_preserves_cap_force_and_approaches_od_membrane_stresses():
    context=RunContext(asset_id='thin_annulus',scenario_id='membrane_limit',clock_id='static',origin='synthetic',
        evidence=(EvidenceRef(id='geometry',citation='Authored thin-annulus geometry sequence',origin='synthetic'),
                  EvidenceRef(id='pressure',citation='Authored differential pressure sequence',origin='synthetic')))
    pressure,common,outer_diameter=3e4,1e5,.2
    errors_axial=[];errors_hoop=[]
    for ratio in (.05,.02,.005,.001):
        thickness=ratio*outer_diameter;b=outer_diameter/2;a=b-thickness
        q=PressureSection(asset_id=context.asset_id,location_id='wall',clock_id=context.clock_id,time_s=0.,
            inner_radius_m=a,outer_radius_m=b,evaluation_radii_m=(a,(a+b)/2,b),
            internal_pressure=Pressure(value=common+pressure,unit='Pa',reference='absolute'),
            external_pressure=Pressure(value=common,unit='Pa',reference='absolute'),pressure_loaded_closed_ends=True,
            geometry_evidence_id='geometry',pressure_evidence_id='pressure',absolute_stress_tolerance_pa=1e-4)
        result=evaluate_pressure_section(q,context)
        assert result['status']=='computed'
        rows=[r['native']['payload']['values'] for r in result['material_records']]
        axial=[r['axial_stress_Pa']+common for r in rows]
        # Separate closed-cap force balance in the actual annular geometry.
        for stress in axial:
            assert stress*(b*b-a*a)==pytest.approx(pressure*a*a,rel=3e-13)
        nominal_hoop=nominal_hoop_stress_Pa(NominalHoopStressInput(differential_pressure_Pa=pressure,
            outside_diameter_m=outer_diameter,wall_thickness_m=thickness))
        nominal_axial=nominal_hoop/2
        hoop=rows[0]['hoop_stress_Pa']+common
        errors_axial.append(abs(axial[0]/nominal_axial-1))
        errors_hoop.append(abs(hoop/nominal_hoop-1))
    assert all(b<a for a,b in zip(errors_axial,errors_axial[1:]))
    assert all(b<a for a,b in zip(errors_hoop,errors_hoop[1:]))
    assert errors_axial[-1]<.0031
    assert errors_hoop[-1]<.0011
    assert errors_axial[0]>.1
    assert errors_hoop[0]>.04
