"""Additional real engineering producers for explicit simulation-observable workflows."""
import inspect
from pathlib import Path

ROOT_SPECS={
 'fluid_pipe.closed_mass_compatibility':('closed_liquid_compatibility.evaluate_closed_liquid_compatibility',('pressure_Pa_abs',),'closed_mass_target_absolute_pressure','Pa','absolute'),
 'leak.incompressible_small_hole':('liquid_orifice.evaluate_liquid_opening_release',('mass_flow_kg_s',),'incompressible_opening_mass_rate','kg/s',None),
 'multiphase.hilly_liquid_holdup_loss':('inclined_two_phase_segment.evaluate_inclined_two_phase_segment',('signed_pressure_drop_Pa',),'inclined_local_segment_signed_pressure_drop','Pa','differential'),
 'multiphase.lockhart_martinelli_loss':('horizontal_two_phase_friction.evaluate_horizontal_two_phase_friction',('friction_pressure_drop_Pa',),'horizontal_two_phase_friction_pressure_drop','Pa','differential'),
 'porous.constant_pressure_sharp_front':('porous_release.evaluate_spherical_porous_front',('cumulative_intruded_liquid_m3',-1),'sharp_front_intruded_pore_volume','m^3',None),
 'porous.richards_unsaturated_flow':('richards_fv.solve_richards_fv',('final_liquid_volume_m3',),'Richards_domain_liquid_volume','m^3',None),
 'release.offshore_advanced_inventory':('offshore_inventory_release.evaluate_offshore_inventory_release',('total_oil_reference_release_m3',),'oil_release_at_declared_common_reference_state','m^3',None),
 'release.offshore_fast_ledger':('offshore_release_ledger.evaluate_offshore_release_ledger',('estimated_total_release_m3',),'conditional_release_at_declared_liquid_reference','m^3',None),
 'rheology.potapov_yield_turbulent':('turbulent_constitutive_friction.evaluate_turbulent_resistance',('darcy_friction_factor',),'viscoplastic_Darcy_friction_factor','1',None),
 'rheology.romanova_powerlaw_turbulent':('turbulent_constitutive_friction.evaluate_turbulent_resistance',('darcy_friction_factor',),'powerlaw_Darcy_friction_factor','1',None),
 'spill.constant_feed_viscous_spread':('porous_release.evaluate_surface_viscous_spill',('radius_m',-1),'conditional_axisymmetric_surface_spill_radius','m',None),
 'acoustics.uniform_pressure_standing_modes':('acoustic_cavity_modes.evaluate_acoustic_cavity_modes',('modes',0,'frequency_hz'),'first_quiescent_ideal_boundary_standing_mode_frequency','Hz',None),
 'leak.growing_tank_hole_integral':('growing_pit_leak.evaluate_growing_pit_leak',('cumulative_mass_kg',-1),'cumulative_spherical_pit_opening_leaked_mass','kg',None),
 'rheology.bingham_circular_laminar':('slurry_transport.evaluate_laminar_pressure_flow',('signed_volume_flow_m3_s',),'Bingham_pressure_driven_pipe_volume_rate','m^3/s',None),
 'rheology.power_law_circular_laminar':('slurry_transport.evaluate_laminar_pressure_flow',('signed_volume_flow_m3_s',),'powerlaw_pressure_driven_pipe_volume_rate','m^3/s',None),
 'meter.supplied_gas_differential_flow':('gas_meter.evaluate_gas_meter',('values','signed_mass_flow_kg_s'),'meter_supplied_coefficient_signed_gas_mass_rate','kg/s',None),
}


def produce_root(unit,tmp_path,*,json_boundary=False):
    from properties_pipeline_cases_223 import api,dump,get,helper,context_for,Observable
    from pipeline_integrity.contracts import EvidenceRef,digest
    from pipeline_integrity.datasets import file_digest
    from pipeline_integrity.metrology import MeasurementBasis
    name,path,quantity,si,reference=ROOT_SPECS[unit]
    fn=api(name); kw={}; c=None
    if unit=='fluid_pipe.closed_mass_compatibility':
        h=helper('closed_liquid_compatibility_individual');q=h.request()
        kw={'density_provider':h.Density(),'volume_provider':h.Volume()}
    elif unit=='leak.incompressible_small_hole':q=helper('liquid_opening_release_independent').request()
    elif unit=='multiphase.hilly_liquid_holdup_loss':q=helper('inclined_two_phase_segment_individual').make()
    elif unit=='multiphase.lockhart_martinelli_loss':q=helper('horizontal_two_phase_friction_individual').make()
    elif unit=='porous.constant_pressure_sharp_front':q=helper('porous_release_independent').sph()
    elif unit=='spill.constant_feed_viscous_spread':q=helper('porous_release_independent').srf()
    elif unit=='release.offshore_advanced_inventory':q=helper('offshore_inventory_release_individual').request()
    elif unit=='release.offshore_fast_ledger':
        q=api('offshore_release_ledger.OffshoreReleaseLedgerRequest')(initial_pipe_liquid_volume_m3=10.,maximum_release_fraction=.6,
            gas_oil_reduction_factor=.8,pre_shutin_feed_volume_flow_m3_s=.03,time_until_shutin_s=600.,
            reference_volume_basis='authored_common_liquid_reference',factor_source_id='authored_factors',
            supplied_factors_and_same_liquid_volume_basis_reviewed=True,horizontal_single_full_bore_segment_reviewed=True)
    elif unit.startswith('rheology.') and unit.endswith('turbulent'):
        q=api('turbulent_constitutive_friction.TurbulentResistanceRequest')(
            model='viscoplastic' if 'potapov' in unit else 'power_law',reynolds_coordinate=1e5,
            **({'hedstrom_number':1000.} if 'potapov' in unit else {'flow_index':2.}),
            turbulent_regime_coordinate_and_calibration_reviewed=True)
    elif unit=='acoustics.uniform_pressure_standing_modes':
        q=api('acoustic_cavity_modes.AcousticCavityModeRequest')(asset_id='pipe',clock_id='clock',length_m=10.,sound_speed_m_s=400.,
            left_boundary='rigid_closed',right_boundary='pressure_release_open',elements=32,requested_modes=1,
            property_geometry_boundary_evidence_id='input',uniform_quiescent_linear_acoustics_and_ideal_end_conditions_reviewed=True)
    elif unit=='leak.growing_tank_hole_integral':
        q=api('growing_pit_leak.GrowingPitLeakRequest')(geometry='spherical_intersection',density_kg_m3=1000.,gravity_m_s2=2.,
            wall_thickness_m=.01,initial_pit_radius_m=.01,radial_growth_m_s=.001,
            history=({'time_s':0.,'liquid_head_m':1.,'discharge_coefficient':.5},
                     {'time_s':2.,'liquid_head_m':1.,'discharge_coefficient':.5}))
    elif unit in {'rheology.bingham_circular_laminar','rheology.power_law_circular_laminar'}:
        import runpy
        props=runpy.run_path(str(Path(__file__).resolve().parents[1]/'examples/slurry_transport.py'))['make_case'](reviewed=True)[0].properties
        b='bingham' in unit
        props=type(props).model_validate({**props.model_dump(),'rheology':'bingham' if b else 'power_law',
            'consistency_Pa_s_n':.1 if b else 1.,'flow_index':1. if b else .5,'yield_stress_Pa':5. if b else 0.,
            'mixture_density_kg_m3':1000.,'solid_volume_fraction':0.,'particle_diameter_m':None,
            'maximum_particle_to_pipe_diameter_ratio':None,'supported_absolute_velocity_m_s':(0.,10.)})
        q=api('slurry_transport.LaminarPressureFlowRequest')(properties=props,inner_diameter_m=.1,length_m=10.,
            driving_pressure_difference_Pa=4000. if b else 400.,radial_positions_fraction=(0.,.5,1.),
            laminar_homogeneous_no_slip_domain_reviewed=True)
    elif unit=='meter.supplied_gas_differential_flow':
        q,c,_,_=helper('formula_extreme_root_223').gas_meter_case()
        c=c.model_copy(update={'origin':'simulation'})
    elif unit=='porous.richards_unsaturated_flow':
        h=helper('richards_fv_independent');mesh,_=h.grid(3);settings=h.settings(.02,.01)
        law=h.LinearHydraulicLaw(.4,.2,.01);initial=(.1,)*3
        # All four public solver arguments are retained; this API is not a single Pydantic request.
        q={'mesh':dump(mesh),'settings':dump(settings),'initial_head_m':initial,'law':dump(law)}
        if json_boundary:
            import json
            q=json.loads(json.dumps(q))
            mesh=type(mesh).model_validate_json(json.dumps(q['mesh']))
            settings=type(settings).model_validate_json(json.dumps(q['settings']))
            law=type(law)(**q['law'])
        result=fn(mesh,settings,tuple(q['initial_head_m']),law)
    else:raise KeyError(unit)
    if unit!='porous.richards_unsaturated_flow':
        if json_boundary:q=type(q).model_validate_json(q.model_dump_json())
        if c is None:c=context_for(q,unit)
        if 'context' in inspect.signature(fn).parameters:kw['context']=c
        result=fn(q,**kw)
    if c is None:c=context_for(q,unit)
    raw=dump(result)
    assert raw.get('status','computed')=='computed',raw
    value=get(raw,path)
    import math
    assert math.isfinite(value)
    c=c.model_copy(update={'evidence':c.evidence+(
        EvidenceRef(id='model-output',citation='Actual model calculation '+name,origin='simulation',sha256=digest(raw)),
        EvidenceRef(id='synthetic-correction',citation='Authored synthetic covariance and correction declaration',origin='synthetic'))})
    basis=MeasurementBasis(quantity=quantity,unit=si,definition_id=unit+':'+'.'.join(map(str,path)),evidence_id='model-output',pressure_reference=reference)
    return Observable(unit,'pipeline_integrity.'+name,file_digest(Path(inspect.getsourcefile(fn))),dump(q),raw,float(value),basis,c)
