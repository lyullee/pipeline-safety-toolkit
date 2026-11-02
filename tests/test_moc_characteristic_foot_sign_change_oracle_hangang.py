"""Independent analytic oracle for frictional MOC characteristic-foot splitting."""
import pytest
from pipeline_integrity.moc_characteristics import characteristic_foot_polynomials, interpolated_characteristic_values
HEAD=(50.0,49.0,47.0,45.0); RIGHT=(0.05,0.04,-0.03,-0.06); LEFT=(0.02,-0.02,-0.04,0.01); B=2.0; R=10.0

def direct(alpha,section):
    i=section; hp=HEAD[i]+alpha*(HEAD[i-1]-HEAD[i]); qp=LEFT[i]+alpha*(RIGHT[i-1]-LEFT[i]); cp=hp+B*qp-alpha*R*qp*abs(qp)
    if i==len(HEAD)-1:return cp,None,qp,None
    hm=HEAD[i]+alpha*(HEAD[i+1]-HEAD[i]); qm=RIGHT[i]+alpha*(LEFT[i+1]-RIGHT[i]); cm=hm-B*qm+alpha*R*qm*abs(qm)
    return cp,cm,qp,qm

def test_piecewise_characteristics_split_exactly_at_reversing_flow_feet():
    all_pieces=characteristic_foot_polynomials(HEAD,RIGHT,LEFT,B,R); pieces=all_pieces[0]
    cuts=[pieces[0][0],*(part[1] for part in pieces)]
    assert cuts==pytest.approx((0.0,2.0/7.0,0.5,1.0),abs=2e-15)
    for section,section_pieces in enumerate(all_pieces,start=1):
        for lo,hi,plus_poly,minus_poly in section_pieces:
            assert hi>lo
            for alpha in (lo,(lo+hi)/2.0,hi):
                cp,cm,_,_=direct(alpha,section)
                assert float(plus_poly(alpha))==pytest.approx(cp,rel=2e-14,abs=2e-14)
                if minus_poly is not None:
                    assert cm is not None
                    assert float(minus_poly(alpha))==pytest.approx(cm,rel=2e-14,abs=2e-14)
    for fraction in (0.0,0.25,0.5,0.75,1.0):
        plus,minus=interpolated_characteristic_values(HEAD,RIGHT,LEFT,B,R,fraction)
        for index in range(1,len(HEAD)-1):
            cp,cm,_,_=direct(fraction,index)
            assert plus[index-1]==pytest.approx(cp,rel=2e-14,abs=2e-14)
            assert minus[index]==pytest.approx(cm,rel=2e-14,abs=2e-14)
    for alpha in (2.0/7.0,0.5):
        _,_,qp,qm=direct(alpha,1)
        assert min(abs(qp),abs(qm))<=2e-16
