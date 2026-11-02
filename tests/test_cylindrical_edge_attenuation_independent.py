"""Independent fourth-order decaying shell moment boundary problem."""
import numpy as np
import pytest
from scipy.integrate import solve_bvp,quad
from pipeline_integrity.cylindrical_edge_attenuation import CylindricalEdgeMomentRequest as Request,evaluate_cylindrical_edge_moment as evaluate

def request(moment=120.,decay=2.,distance=(0.,.1,1.,3.)):
    return Request(edge_moment_per_circumference_n=moment,inverse_decay_length_per_m=decay,distance_from_edge_m=distance,
        shell_decay_provider_basis_id='authored-reviewed-shell-decay',uniform_edge_moment_zero_edge_shear_semi_infinite_shell_reviewed=True)

@pytest.mark.parametrize('moment,decay',[(120.,.5),(-120.,2.),(500.,8.)])
def test_independent_boundary_solution_and_shell_moment_equilibrium(moment,decay):
    # Dimensionless governing shell equation M''''+4M=0; no analytical
    # trigonometric/exponential response is supplied to the numerical solver.
    def fun(z,y):return np.vstack((y[1],y[2],y[3],-4*y[0]))
    def bc(a,b):return np.array([a[0]-1,a[1],b[0],b[1]])
    grid=np.linspace(0,22,180)
    solution=solve_bvp(fun,bc,grid,np.zeros((4,len(grid))),tol=1e-9,max_nodes=10000)
    assert solution.success
    z=np.linspace(0,7,31)
    r=evaluate(request(moment,decay,tuple(z/decay)))
    assert r['status']=='computed'
    assert np.array(r['moment_per_circumference_n'])/moment==pytest.approx(solution.sol(z)[0],abs=2e-9)
    assert np.array(r['moment_gradient_n_m'])/(moment*decay)==pytest.approx(solution.sol(z)[1],abs=2e-9)
    # Integral force balance of M''''=-4M: M'''(0)=4*integral M dz.
    area=quad(lambda x:float(solution.sol(x)[0]),0,22,epsabs=1e-9)[0]
    assert solution.sol(0)[3]-solution.sol(22)[3]==pytest.approx(4*area,abs=2e-8)

def test_distance_and_decay_scaling_preserves_dimensionless_response():
    a=evaluate(request());b=evaluate(request(decay=20.,distance=(0.,.01,.1,.3)))
    assert a['moment_per_circumference_n']==pytest.approx(b['moment_per_circumference_n'])

def test_zero_moment_and_zero_edge_shear():
    r=evaluate(request(moment=0.));assert all(v==0 for v in r['moment_per_circumference_n'])
    assert evaluate(request())['moment_gradient_n_m'][0]==0

def test_no_unreviewed_decay_default():
    assert evaluate(request().model_copy(update={'inverse_decay_length_per_m':None}))['status']=='needs_data'
    assert evaluate(request().model_copy(update={'uniform_edge_moment_zero_edge_shear_semi_infinite_shell_reviewed':False}))['status']=='needs_data'
