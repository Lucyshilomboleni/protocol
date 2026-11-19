"""
Experiments behind each objective tab. Every function returns plain
dictionaries/lists that Flask sends to the browser as JSON.
"""
import copy
from .engine import (DEFAULT_CFG, DEFAULT_PROTO, SCENARIOS, EVAL_SCENARIOS, TIER_NAMES,
                     derived, event_energy, effective_tiers, soc_to_voltage, run_sim,
                     proposed_protocol, static_protocol, no_reserve_protocol)

SCENARIO_NAMES = {k: SCENARIOS[k]['name'] for k in EVAL_SCENARIOS}


def make_cfg(start):
    """Copy the default model and apply the start values typed by the user."""
    cfg = copy.deepcopy(DEFAULT_CFG)
    cfg['C_mF'] = start['capacitance']          # supercapacitor size (mF)
    cfg['dist'] = start['distance']             # tag-to-phone distance (m)
    cfg['soc0'] = start['soc'] / 100           # starting charge (fraction)
    return cfg


def describe(start):
    """Short line telling the user which start values were used."""
    return (f"{start['hours']:g} h · start charge {start['soc']:g} % · {start['capacitance']:g} mF · "
            f"{start['distance']:g} m · seed {start['seed']}")


def _mean(values):
    return sum(values) / len(values) if values else 0.0


# ---------------------------------------------------------------- general information
def info():
    """Fixed values shown when the page loads."""
    c = DEFAULT_CFG; d = derived(c); p = DEFAULT_PROTO
    tiers = effective_tiers(c, p)
    entry = ['75 - 100 %', '45 - 75 %', '20 - 45 %', '5 - 20 % (reserve)']
    modes = [dict(name=t['name'], charge=entry[i], interval=f"{t['minInt'] / 1000:g} - {t['maxInt'] / 1000:g} s",
                  radio=f"{'+' if t['tx'] > 0 else ''}{t['tx']} dBm / {t['ch']} channels") for i, t in enumerate(tiers)]
    modes.append(dict(name='HIBERNATE', charge='below 5 %', interval='no adverts', radio='radio off, memory kept'))
    v = lambda s: f"{soc_to_voltage(c, s):.2f} V"
    zones = [
        dict(zone='Normal operation (BOOST, NORMAL, E-SAVING)', charge='20 - 100 %', voltage=f"{v(p['reserve'])} - {v(1)}"),
        dict(zone='Protected reserve (SURVIVAL)', charge='5 - 20 %', voltage=f"{v(p['crit'])} - {v(p['reserve'])}"),
        dict(zone='HIBERNATE', charge='0 - 5 %', voltage=f"{v(0)} - {v(p['crit'])}"),
        dict(zone='Brown-out', charge='store empty', voltage=f"below {v(0)}"),
    ]
    return dict(usable_mJ=round(d['Euse'] * 1000), advert_uJ=round(event_energy(c, 0, 3) * 1e6, 1),
                scenarios=SCENARIO_NAMES, modes=modes, zones=zones)


# ---------------------------------------------------------------- Objective 1
def model_run(scenario, start):
    """Run both protocols on the same scenario (same seed = same harvest)."""
    cfg = make_cfg(start); hours = start['hours']; seed = start['seed']
    A = run_sim(cfg, proposed_protocol(), scenario, seed, hours).metrics()
    B = run_sim(cfg, static_protocol(), scenario, seed, hours).metrics()
    rows = [
        ('Brown-outs', B['brown'], A['brown'], ''),
        ('Availability (time advertising)', B['avail'], A['avail'], '%'),
        ('Energy harvested', B['harv'], A['harv'], 'mJ'),
        ('Energy used by the radio', B['radio'], A['radio'], 'mJ'),
        ('Final state of charge', B['socEnd'], A['socEnd'], '%'),
        ('Events simulated', B['events'], A['events'], ''),
    ]
    return dict(title=f"{SCENARIO_NAMES[scenario]} · {describe(start)}",
                rows=[dict(measure=m, static=s, proposed=p, unit=u) for m, s, p, u in rows],
                balance=dict(proposed=A['balance'], static=B['balance']))


# ---------------------------------------------------------------- Objective 2
def decision(soc_percent, harvest_uW, capacitance=100):
    """The protocol's decision rule for a given stored energy and harvested power."""
    c = copy.deepcopy(DEFAULT_CFG); c['C_mF'] = capacitance; p = DEFAULT_PROTO; d = derived(c); T = effective_tiers(c, p)
    soc = max(0.0, min(1.0, soc_percent / 100)); ph = max(0.0, harvest_uW) * 1e-6
    th = [p['tiers'][0]['th'], p['tiers'][1]['th'], p['reserve'], p['crit']]
    k = 0
    while k < 4 and soc < th[k]:
        k += 1
    if k == 4:
        return dict(mode='HIBERNATE')
    E = d['Emin'] + soc * d['Euse']
    floor = d['Emin'] + (p['reserve'] if k <= 2 else p['crit']) * d['Euse']
    budget = ph + max(0.0, E - floor) / p['Th'] - (d['Psleep'] + d['b'] * E)
    iv = event_energy(c, T[k]['tx'], T[k]['ch']) / budget if budget > 0 else float('inf')
    iv = min(T[k]['maxInt'] / 1000, max(T[k]['minInt'] / 1000, iv))
    return dict(mode=T[k]['name'], interval_ms=round(iv * 1000), tx=T[k]['tx'], channels=T[k]['ch'],
                budget_uW=round(budget * 1e6, 1))


def protocol_run(start):
    """Share of time the proposed protocol spends in each mode, per scenario."""
    cfg = make_cfg(start); rows = []
    for sc in EVAL_SCENARIOS:
        m = run_sim(cfg, proposed_protocol(), sc, start['seed'], start['hours']).metrics()
        rows.append(dict(scenario=SCENARIO_NAMES[sc], modes=[round(x) for x in m['tierT']], off=round(m['offPct'])))
    return dict(columns=TIER_NAMES, rows=rows, used=describe(start))


# ---------------------------------------------------------------- Objective 3
def drawer_test(start):
    """Keys lying still with no harvest at all: three configurations."""
    tests = [('Proposed, with reserve', proposed_protocol(), 'proposed'),
             ('Proposed, reserve switched off', no_reserve_protocol(), ''),
             ('Static BLE', static_protocol(), 'static')]
    cfg = make_cfg(start); rows = []
    for name, proto, style in tests:
        m = run_sim(cfg, proto, 'noMotion', start['seed'], start['hours']).metrics()
        rows.append(dict(config=name, style=style, findable_h=round(m['availH'], 1),
                         first_brownout_h=None if m['firstBrown'] is None else round(m['firstBrown'] / 3600, 1),
                         packets=m['rx']))
    return dict(rows=rows, used=describe(start))


# ---------------------------------------------------------------- Objective 4
MEASURES = [('brown', 'Brown-outs per run'), ('avail', 'Availability (%)'), ('latency', 'Latency (s)'),
            ('pdr', 'Packet delivery ratio (%)'), ('rx', 'Successful packets'), ('lost', 'Packets lost')]


def compare(runs, start):
    """Proposed vs static in all five scenarios, same seeds for both protocols."""
    cfg = make_cfg(start); hours = start['hours']; per_scenario = []; all_A, all_B = [], []
    for sc in EVAL_SCENARIOS:
        A, B = [], []
        for r in range(runs):
            seed = start['seed'] + r * 31        # each extra run uses a new seed
            A.append(run_sim(cfg, proposed_protocol(), sc, seed, hours).metrics())
            B.append(run_sim(cfg, static_protocol(), sc, seed, hours).metrics())
        all_A += A; all_B += B
        per_scenario.append(dict(scenario=SCENARIO_NAMES[sc], values=[
            dict(key=k, measure=n, static=_mean([m[k] for m in B]), proposed=_mean([m[k] for m in A])) for k, n in MEASURES]))
    overall = [dict(key=k, measure=n, static=_mean([m[k] for m in all_B]), proposed=_mean([m[k] for m in all_A])) for k, n in MEASURES]
    return dict(runs=runs, hours=hours, used=describe(start) + f" · {runs} run(s) per scenario",
                overall=overall, scenarios=per_scenario)
