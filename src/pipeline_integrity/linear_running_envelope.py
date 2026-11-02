"""Exact interval events for a supplied piecewise-linear running maximum.

This unit-neutral primitive is shared by conditional head-floor routing and
nonnegative inventory reflection. Physical units, boundary consistency and
the meaning of the increasing regulator belong to each caller's contract.
"""
from __future__ import annotations

import math


def piecewise_linear_running_max(stations,values,initial_level):
    x=tuple(float(v) for v in stations)
    y=tuple(float(v) for v in values)
    initial=float(initial_level)
    if len(x)<2 or len(x)!=len(y) or not all(math.isfinite(v) for v in (*x,*y,initial)):
        raise ValueError("running envelope needs matching finite arrays with at least two points")
    if any(b<=a for a,b in zip(x,x[1:])):
        raise ValueError("running-envelope stations must strictly increase")
    level=max(initial,y[0])
    levels=[level]
    pieces=[]

    def piece(i,left,right,first,last,tracking):
        if not math.isfinite(right-left) or right<=left:
            raise ValueError("running-envelope event interval is not numerically resolved")
        pieces.append(dict(source_interval=i,start=left,end=right,
            level_start=first,level_end=last,tracking_input=tracking))

    for i,(left,right,first,last) in enumerate(zip(x,x[1:],y,y[1:])):
        if last<=level:
            piece(i,left,right,level,level,False)
        elif first==level:
            piece(i,left,right,first,last,True)
            level=last
        else:
            # A linear input can meet the previous maximum only once.
            fraction=(level-first)/(last-first)
            event=left+(right-left)*fraction
            if not left<event<right:
                raise ValueError("running-envelope crossing is below station resolution")
            piece(i,left,event,level,level,False)
            piece(i,event,right,level,last,True)
            level=last
        levels.append(level)
    return dict(stations=x,maximum_values=tuple(levels),pieces=pieces,
        initial_raise=max(0.0,y[0]-initial))
