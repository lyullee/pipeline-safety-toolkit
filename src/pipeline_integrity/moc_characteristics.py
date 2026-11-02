"""Shared liquid characteristic transport for ordinary and split-flow nodes."""
from __future__ import annotations


def _fields(head,flow_to_right,flow_from_left,impedance_s_m2,resistance_s2_m5):
    import math
    import numpy as np
    h = np.asarray(head,dtype=float)
    right = np.asarray(flow_to_right,dtype=float)
    left = np.asarray(flow_from_left,dtype=float)
    b, r = impedance_s_m2, resistance_s2_m5
    if h.ndim!=1 or len(h)<3 or right.shape!=h.shape or left.shape!=h.shape:
        raise ValueError("liquid characteristics require at least three aligned one-dimensional grid nodes")
    if not all(np.isfinite(values).all() for values in (h,right,left)) or not math.isfinite(b) or not math.isfinite(r) or b<=0 or r<0:
        raise ValueError("liquid characteristics require finite fields, positive impedance and nonnegative resistance")
    return h,right,left,b,r


def _transport_invariant(h,q,b,r,direction,flow_sign=None):
    import numpy as np
    magnitude = np.abs(q) if flow_sign is None else flow_sign*q
    return h+direction*b*q-direction*r*q*magnitude


def characteristic_invariants(head, flow_to_right, flow_from_left, impedance_s_m2, resistance_s2_m5):
    """Return interior C+, C- and the two endpoint incident characteristics.

    At a cavity the right-going and left-going reach flows need not coincide.
    Ordinary liquid nodes supply the same flow array for both arguments. No
    vapor-pressure clamp or cavity-volume update is hidden in this primitive.
    """
    import math
    import numpy as np
    h,right,left,b,r = _fields(head,flow_to_right,flow_from_left,impedance_s_m2,resistance_s2_m5)
    plus = _transport_invariant(h[:-2],right[:-2],b,r,1)
    minus = _transport_invariant(h[2:],left[2:],b,r,-1)
    incident_at_start = _transport_invariant(h[1],left[1],b,r,-1)
    incident_at_end = _transport_invariant(h[-2],right[-2],b,r,1)
    if not np.isfinite(plus).all() or not np.isfinite(minus).all() or not math.isfinite(incident_at_start) or not math.isfinite(incident_at_end):
        raise ValueError("liquid characteristic transport exceeds finite arithmetic")
    return plus,minus,incident_at_start,incident_at_end


def interpolated_characteristic_values(head,flow_to_right,flow_from_left,impedance_s_m2,resistance_s2_m5,fraction):
    """C+ at 1..N and C- at 0..N-1 for 0<=CFL<=1; zero resolves an event locally."""
    import math
    import numpy as np
    h,right,left,b,r = _fields(head,flow_to_right,flow_from_left,impedance_s_m2,resistance_s2_m5)
    if not math.isfinite(fraction) or not 0 <= fraction <= 1:
        raise ValueError("fractional characteristics require 0<=CFL<=1")
    f = fraction
    hp = (1-f)*h[1:]+f*h[:-1]
    qp = (1-f)*left[1:]+f*right[:-1]
    hm = (1-f)*h[:-1]+f*h[1:]
    qm = (1-f)*right[:-1]+f*left[1:]
    plus = _transport_invariant(hp,qp,b,f*r,1)
    minus = _transport_invariant(hm,qm,b,f*r,-1)
    if not np.isfinite(plus).all() or not np.isfinite(minus).all():
        raise ValueError("fractional characteristic transport exceeds finite arithmetic")
    return plus,minus


def characteristic_foot_polynomials(head,flow_to_right,flow_from_left,impedance_s_m2,resistance_s2_m5):
    """Piecewise polynomials in CFL fraction for each nonreservoir section.

    A linear foot flow can change sign once. Splitting at those actual zeros
    keeps Darcy's q*abs(q) source polynomial without smoothing the reversal.
    Each returned section contains (alpha_lo,alpha_hi,Cplus,Cminus_or_None).
    """
    from numpy.polynomial import Polynomial
    h,right,left,b,r = _fields(head,flow_to_right,flow_from_left,impedance_s_m2,resistance_s2_m5)
    result = []
    n = len(h)-1
    for i in range(1,n+1):
        hp = Polynomial((h[i],h[i-1]-h[i]))
        qp = Polynomial((left[i],right[i-1]-left[i]))
        hm = None if i == n else Polynomial((h[i],h[i+1]-h[i]))
        qm = None if i == n else Polynomial((right[i],left[i+1]-right[i]))
        breaks = [0.0,1.0]
        for flow in (qp,qm):
            if flow is not None and len(flow.coef) > 1 and flow.coef[1] != 0:
                root = -flow.coef[0]/flow.coef[1]
                if 0 < root < 1:
                    breaks.append(float(root))
        breaks = sorted(set(breaks))
        pieces = []
        for a,z in zip(breaks,breaks[1:]):
            midpoint = (a+z)/2
            sp = 1 if qp(midpoint)>0 else -1 if qp(midpoint)<0 else 0
            cp = _transport_invariant(hp,qp,b,Polynomial((0,r)),1,sp)
            cm = None
            if qm is not None:
                sm = 1 if qm(midpoint)>0 else -1 if qm(midpoint)<0 else 0
                cm = _transport_invariant(hm,qm,b,Polynomial((0,r)),-1,sm)
            pieces.append((a,z,cp,cm))
        result.append(tuple(pieces))
    return tuple(result)
