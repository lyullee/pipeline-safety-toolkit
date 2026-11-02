"""Miner linear cycle-damage summation and invariance checks."""
import pytest
from matintegrity.models.fatigue import MinerDamage, miner_damage


def evaluate(counts, lives, limit=0.2):
    return miner_damage(MinerDamage(cycle_counts=counts, cycles_to_failure=lives,
                                    declared_damage_limit=limit)).values


def test_miner_damage_matches_independent_sum_of_cycle_fractions():
    counts=[1000.0, 2000.0, 750.0]
    lives=[1.0e6, 4.0e6, 3.0e6]
    expected=sum(n/N for n,N in zip(counts,lives))
    result=evaluate(counts,lives,limit=0.02)
    assert result['damage']==pytest.approx(expected,rel=1e-14)
    assert result['damage_margin']==pytest.approx(0.02-expected,rel=1e-14)
    assert result['fraction_of_declared_limit']==pytest.approx(expected/0.02,rel=1e-14)


def test_miner_damage_is_invariant_to_reordering_of_cycle_blocks():
    counts=[120.0,500.0,70.0,250.0]
    lives=[5000.0,20000.0,800.0,2500.0]
    a=evaluate(counts,lives)
    b=evaluate(list(reversed(counts)),list(reversed(lives)))
    assert b['damage']==pytest.approx(a['damage'],rel=1e-14)
    assert b['damage_margin']==pytest.approx(a['damage_margin'],rel=1e-14)


def test_miner_damage_scales_linearly_with_repeated_block_counts():
    counts=[50.0,180.0,400.0]
    lives=[1000.0,6000.0,10000.0]
    base=evaluate(counts,lives,limit=2.0)['damage']
    repeated=evaluate([4*n for n in counts],lives,limit=2.0)['damage']
    assert repeated==pytest.approx(4*base,rel=1e-14)


def test_miner_damage_above_declared_limit_is_preserved_not_clipped():
    result=evaluate([250.0,100.0],[100.0,200.0],limit=1.0)
    assert result['damage']==pytest.approx(3.0)
    assert result['damage_margin']==pytest.approx(-2.0)
    assert result['fraction_of_declared_limit']==pytest.approx(3.0)
