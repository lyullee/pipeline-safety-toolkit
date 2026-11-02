"""Selected thin-cylinder modal energy/mass projections through existing owners."""
import math
from types import SimpleNamespace
from typing import Literal
import numpy as np
from pydantic import Field
from .contracts import Contract,Positive
from .elastic_shell_ring import _ring_discretization
from .harmonic_response import HarmonicOscillator,frequency_response

class CylindricalModalProjectionRequest(Contract):
    mean_radius_m: Positive
    wall_thickness_m: Positive
    axial_length_m: Positive
    elastic_modulus_pa: Positive
    poisson_ratio: float = Field(gt=-1,lt=.5)
    material_density_kg_m3: Positive | None = None
    radial_axial_constraint: Literal['plane_stress_ring','plane_strain_axially_uniform_shell']
    maximum_thickness_to_radius_ratio: Positive = Field(default=.05,le=.1)
    quadrature_points: int = Field(default=96,ge=64,le=1024)
    isotropic_thin_uniform_shell_modal_fields_and_free_axial_mode_reviewed: bool = False

def evaluate_cylindrical_modal_projection(request: CylindricalModalProjectionRequest):
    """Return explicit ideal-mode properties, not a finite-shell eigenanalysis.

    Existing geometrically exact ring energy supplies the zero-load Hessian.
    Radial breathing is uniform, while the two selected flexural coordinates
    use inextensional cosine-lobe fields with consistent tangential motion.
    The axial coordinate is the first non-rigid free-free longitudinal mode.
    Rotational inertia, finite-end/head coupling, added fluid mass, prestress,
    plasticity and damping are not inferred. Each modal displacement is in m.
    """
    q=CylindricalModalProjectionRequest.model_validate(request.model_dump())
    if q.wall_thickness_m/q.mean_radius_m>q.maximum_thickness_to_radius_ratio:
        return dict(status='out_of_scope',issues=('wall exceeds declared thin-shell support',))
    if q.material_density_kg_m3 is None or not q.isotropic_thin_uniform_shell_modal_fields_and_free_axial_mode_reviewed:
        return dict(status='needs_data')
    r=q.mean_radius_m;t=q.wall_thickness_m;length=q.axial_length_m;rho=q.material_density_kg_m3
    # Only the existing energy kernel is used. No fictitious pressure case,
    # continuation state or pressure-applicability review is constructed.
    kernel=_ring_discretization(SimpleNamespace(mean_reference_centerline_radius_m=r,initial_diameter_ovality_fraction=0.,
        maximum_cartesian_harmonic=4,young_modulus_Pa=q.elastic_modulus_pa,poisson_ratio=q.poisson_ratio,
        axial_constraint=q.radial_axial_constraint,wall_thickness_m=t,axial_strip_width_m=length),q.quadrature_points)
    labels=kernel['labels'];zero=np.zeros(len(labels));reference=kernel['fields'](zero)
    stiffness=np.asarray(kernel['hessian'](zero,0.))*kernel['energy_scale']
    modes=[]
    def project(name,vector,pressure_area):
        fields=kernel['fields'](vector)
        shape_x=(np.asarray(fields[0])-np.asarray(reference[0]))/r
        shape_y=(np.asarray(fields[1])-np.asarray(reference[1]))/r
        modal_mass=rho*t*length*r*2*math.pi*float(np.mean(shape_x**2+shape_y**2))
        modal_stiffness=float(vector@stiffness@vector)/r**2
        response=frequency_response(HarmonicOscillator(modal_mass,modal_stiffness,0.,0.,0.))
        modes.append(dict(mode=name,modal_mass_kg=modal_mass,modal_stiffness_n_m=modal_stiffness,
            frequency_hz=response.natural_frequency_Hz,angular_frequency_rad_s=response.natural_angular_frequency_rad_s,
            generalized_uniform_pressure_area_m2=pressure_area))
    radial=zero.copy();radial[labels.index(('x','cos',1))]=1.;radial[labels.index(('y','sin',1))]=1.
    project('uniform_radial_breathing',radial,2*math.pi*r*length)
    for n in (2,3):
        vector=zero.copy()
        for label,value in ((('x','cos',n-1),.5*(1+1/n)),(('x','cos',n+1),.5*(1-1/n)),
                            (('y','sin',n-1),-.5*(1+1/n)),(('y','sin',n+1),.5*(1-1/n))):
            vector[labels.index(label)]=value
        project(f'inextensional_{n}_lobe_cosine',vector,0.)
    area=2*math.pi*r*t
    axial_mass=rho*area*length/2
    axial_stiffness=q.elastic_modulus_pa*area*math.pi**2/(2*length)
    axial=frequency_response(HarmonicOscillator(axial_mass,axial_stiffness,0.,0.,0.))
    modes.append(dict(mode='first_nonrigid_free_free_axial',modal_mass_kg=axial_mass,modal_stiffness_n_m=axial_stiffness,
        frequency_hz=axial.natural_frequency_Hz,angular_frequency_rad_s=axial.natural_angular_frequency_rad_s,
        generalized_uniform_pressure_area_m2=None))
    return dict(status='computed',modes=tuple(modes),full_shell_eigenmodes_or_added_fluid_mass_inferred=False)
