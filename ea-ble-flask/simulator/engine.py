"""
EA-BLE simulation engine (Python version, used by the Flask server).

Discrete-event simulation of a battery-free key locator:
  * kinetic harvester  -> stochastic, activity-dependent power
  * supercapacitor     -> E = 1/2 C V^2, leakage, over-voltage clamp, brown-out
  * BLE radio          -> energy per advertising event
  * protocols          -> energy-aware (proposed) and static baseline

This is a line-by-line port of the JavaScript engine used in the thesis,
so the same seed gives the same results.
"""
import math, copy

# ---------------------------------------------------------------- random numbers
def _imul(a, b):
    return (a * b) & 0xFFFFFFFF


def mulberry32(seed):
    """Seeded random generator in [0, 1): identical seeds give identical runs."""
    state = [seed & 0xFFFFFFFF]

    def rnd():
        a = (state[0] + 0x6D2B79F5) & 0xFFFFFFFF
        state[0] = a
        t = _imul(a ^ (a >> 15), 1 | a)
        t = ((t + _imul(t ^ (t >> 7), 61 | t)) & 0xFFFFFFFF) ^ t
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296
    return rnd


def gauss(rnd):
    """Standard normal random number (Box-Muller)."""
    u = 0.0
    while u == 0.0:
        u = rnd()
    v = rnd()
    return math.sqrt(-2 * math.log(u)) * math.cos(2 * math.pi * v)


def norm_cdf(x):
    """Standard normal distribution function (Abramowitz & Stegun)."""
    t = 1 / (1 + 0.2316419 * abs(x))
    d = 0.3989423 * math.exp(-x * x / 2)
    p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))))
    return 1 - p if x > 0 else p


# ---------------------------------------------------------------- default settings
TX_LEVELS = [4, 0, -4, -8, -12, -16, -20]          # BLE transmit power levels (dBm)
TX_MA = [7.5, 5.3, 4.6, 4.1, 3.8, 3.6, 3.4]        # radio current at each level (mA)

DEFAULT_CFG = dict(
    C_mF=100, Vmax=3.6, Vmin=1.8, Von=2.4, Rleak_MOhm=2,             # supercapacitor
    Vsys=3.0, etaDC=0.85, Isleep_uA=1.9, Ihib_uA=0.35, Poff_uW=0.5,   # platform
    Ewake_uJ=8, Eboot_uJ=400, payload=24, tRamp_us=140,               # radio
    etaH=0.70, P_idle=0, P_handling=450, P_walk=300, P_run=700, P_vehicle=60, cv=0.35, hscale=1,  # harvester
    dist=10, PL0=40, nPL=2.7, sens=-92, sigma=6, scanDuty=1.0, findWin=30, soc0=0.5,               # phone link
)

DEFAULT_PROTO = dict(
    type='adaptive',
    tiers=[
        dict(name='BOOST', th=0.75, minInt=100, maxInt=500, tx=4, ch=3),
        dict(name='NORMAL', th=0.45, minInt=250, maxInt=1000, tx=0, ch=3),
        dict(name='E-SAVING', th=None, minInt=1000, maxInt=5000, tx=0, ch=3),
        dict(name='SURVIVAL', th=None, minInt=5000, maxInt=15000, tx=0, ch=3),
    ],
    reserve=0.20, crit=0.05, hyst=0.03, alpha=0.10, Th=3600,
    guard=True, reserveOn=True, burst=True, burstN=6, burstInt=200, zeroLoss=True, linkTarget=0.9999,
)

DEFAULT_BASE = dict(type='static', interval=250, tx=0, ch=3)

TIER_NAMES = ['BOOST', 'NORMAL', 'E-SAVING', 'SURVIVAL', 'HIBERNATE', 'OFF']

# Usage scenarios: w = activity weights, dur = mean duration of each activity (s)
SCENARIOS = {
    'commuter': dict(name='Active commuter', w=dict(idle=.30, handling=.15, walk=.30, vehicle=.25), dur=dict(idle=1500, handling=20, walk=420, vehicle=1500)),
    'office': dict(name='Office / sedentary', w=dict(idle=.65, handling=.20, walk=.15), dur=dict(idle=2700, handling=15, walk=180)),
    'drawer': dict(name='Forgotten in drawer', w=dict(idle=.97, handling=.03), dur=dict(idle=21600, handling=8)),
    'field': dict(name='Field worker', w=dict(idle=.15, handling=.15, walk=.50, run=.20), dur=dict(idle=600, handling=30, walk=900, run=480)),
    'mixed': dict(name='Mixed day', w=dict(idle=.40, handling=.15, walk=.25, run=.05, vehicle=.15), dur=dict(idle=1800, handling=20, walk=480, run=600, vehicle=1200)),
    'noMotion': dict(name='No motion', w=dict(idle=1), dur=dict(idle=1e12), hidden=True),
}
EVAL_SCENARIOS = ['commuter', 'office', 'drawer', 'field', 'mixed']


def proposed_protocol(**changes):
    p = copy.deepcopy(DEFAULT_PROTO); p.update(changes); return p


def no_reserve_protocol():
    return proposed_protocol(reserveOn=False, guard=False, burst=False)


def static_protocol(**changes):
    p = dict(DEFAULT_BASE); p.update(changes); return p


# ---------------------------------------------------------------- physics
def tx_current(tx):
    return TX_MA[TX_LEVELS.index(tx)] if tx in TX_LEVELS else 5.3


def derived(c):
    """Energies at the voltage thresholds (E = 1/2 C V^2), leakage rate and standby powers."""
    C = c['C_mF'] / 1000
    E = lambda v: 0.5 * C * v * v
    Emax, Emin, Eon = E(c['Vmax']), E(c['Vmin']), E(c['Von'])
    return dict(C=C, Emax=Emax, Emin=Emin, Eon=Eon, Euse=Emax - Emin,
                b=2 / (C * max(0.05, c['Rleak_MOhm']) * 1e6),
                Psleep=c['Vsys'] * c['Isleep_uA'] * 1e-6 / c['etaDC'],
                Phib=c['Vsys'] * c['Ihib_uA'] * 1e-6 / c['etaDC'],
                Poff=c['Poff_uW'] * 1e-6, Eboot=c['Eboot_uJ'] * 1e-6)


def channel_time(c):
    """Airtime of one advertising packet on one channel (s)."""
    return (c['tRamp_us'] + (c['payload'] + 16) * 8) * 1e-6


def event_energy(c, tx, ch):
    """Energy taken from the supercapacitor by one advertising event (J)."""
    return (c['Ewake_uJ'] * 1e-6 + ch * c['Vsys'] * tx_current(tx) * 1e-3 * channel_time(c)) / c['etaDC']


def _rssi(c, tx):
    return tx - c['PL0'] - 10 * c['nPL'] * math.log10(max(c['dist'], 0.5))


def p_event(c, tx, ch):
    """Probability that the phone receives one advertising event."""
    return c['scanDuty'] * (ch / 3) * norm_cdf((_rssi(c, tx) - c['sens']) / c['sigma'])


def link_ok(c, tx):
    return norm_cdf((_rssi(c, tx) - c['sens']) / c['sigma'])


def min_reliable_tx(c, target):
    for t in sorted(TX_LEVELS):
        if link_ok(c, t) >= target:
            return t
    return max(TX_LEVELS)


def effective_tiers(c, p):
    """Zero-loss rule: every mode uses at least the reliable TX power and all 3 channels."""
    if not p['zeroLoss']:
        return p['tiers']
    t0 = min_reliable_tx(c, p.get('linkTarget') or 0.9999)
    return [dict(T, tx=max(T['tx'], t0), ch=3) for T in p['tiers']]


def soc_to_voltage(c, s):
    d = derived(c)
    return math.sqrt(2 * (d['Emin'] + s * d['Euse']) / d['C'])


# ---------------------------------------------------------------- event queue
class Heap:
    """Binary min-heap of events ordered by time (same tie order as the JavaScript version)."""
    def __init__(self):
        self.a = []

    def push(self, e):
        a = self.a; a.append(e); i = len(a) - 1
        while i > 0:
            p = (i - 1) >> 1
            if a[p]['t'] <= e['t']:
                break
            a[i] = a[p]; i = p
        a[i] = e

    def pop(self):
        a = self.a; top = a[0]; last = a.pop()
        if a:
            i = 0; n = len(a)
            while True:
                l = 2 * i + 1
                if l >= n:
                    break
                c = l + 1 if (l + 1 < n and a[l + 1]['t'] < a[l]['t']) else l
                if a[c]['t'] >= last['t']:
                    break
                a[i] = a[c]; i = c
            a[i] = last
        return top

    def peek(self):
        return self.a[0]

    def __len__(self):
        return len(self.a)


# ---------------------------------------------------------------- simulator
class Sim:
    """One key-locator node running one protocol on one scenario.
    Events: ADV (advert), TICK (1 s control step), HARV (new harvest level), ACT (activity change)."""

    def __init__(self, cfg, proto, scenario, seed, soc0=None, max_log=800):
        self.cfg = cfg; self.d = derived(cfg); self.p = proto; self.adaptive = proto['type'] == 'adaptive'
        self.scen = SCENARIOS[scenario]
        self.rng = mulberry32(seed); self.rng2 = mulberry32(seed * 7919 + 13); self.rng3 = mulberry32(seed * 104729 + 7)
        self.last_rx = 0.0; self.max_log = max_log
        d = self.d; self.t = 0.0
        self.E = d['Emin'] + (cfg['soc0'] if soc0 is None else soc0) * d['Euse']; self.E0 = self.E
        self.mode = 'off'; self.tier = -1; self.cur_int = None
        self.Ph = 0.0; self.ewma = 0.0; self.last_harv = 0.0; self.last_tick = 0.0
        self.adv_gen = 0; self.next_adv = math.inf; self.burst = 0; self.burst_cool = -1e9
        self.acc = dict(harv=0.0, quies=0.0, leak=0.0, radio=0.0, boot=0.0, wasted=0.0)
        self.st = dict(adv=0, brown=0, boots=0, skipped=0, failTx=0, availT=0.0, hibT=0.0, offT=0.0,
                       firstAdvT=None, firstBrownT=None, tierT=[0.0] * 5, bursts=0, rx=0, lost=0, gap2=0.0)
        self.log = []; self.events = 0; self.heap = Heap()
        self._prep()
        self.act = self._pick_state(None)
        self.Ph = self._sample_harvest(); self.ewma = self.Ph
        self._schedule_activity()
        self.heap.push(dict(t=3 + 12 * self.rng(), type='HARV'))
        self.heap.push(dict(t=0.0, type='TICK'))
        if self.E >= d['Eon']:
            self.mode = 'on'; self._log('BOOT', 'Initial charge above V_on - node running'); self._on_start()
        else:
            self._log('BOOT', 'Store below V_on - waiting for cold start')

    # ---- set-up
    def _prep(self):
        p, c, d = self.p, self.cfg, self.d
        if self.adaptive:
            ro = p['reserveOn']
            self.th = [p['tiers'][0]['th'], p['tiers'][1]['th'], p['reserve'] if ro else -math.inf, p['crit'] if ro else -math.inf]
            self.E_res = d['Emin'] + p['reserve'] * d['Euse'] if ro else d['Emin']
            self.E_crit = d['Emin'] + p['crit'] * d['Euse'] if ro else d['Emin']
            self.T = effective_tiers(c, p)
            self.E_ev = [event_energy(c, T['tx'], T['ch']) for T in self.T]
            self.p_ev = [p_event(c, T['tx'], T['ch']) for T in self.T]
            self.E_burst = event_energy(c, 0, 3); self.p_burst = p_event(c, 0, 3)
        else:
            self.E_base = event_energy(c, p['tx'], p['ch']); self.p_base = p_event(c, p['tx'], p['ch'])

    def soc(self):
        return max(0.0, min(1.0, (self.E - self.d['Emin']) / self.d['Euse']))

    def voltage(self):
        return math.sqrt(2 * max(0.0, self.E) / self.d['C'])

    def _log(self, kind, msg):
        if len(self.log) < self.max_log:
            self.log.append(dict(t=self.t, type=kind, msg=msg, V=self.voltage()))

    # ---- user activity (Markov process)
    def _pick_state(self, cur):
        w = self.scen['w']; keys = [k for k in w if k != cur] or [cur]
        u = self.rng() * sum(w[k] for k in keys)
        for k in keys:
            u -= w[k]
            if u <= 0:
                return k
        return keys[-1]

    def _schedule_activity(self):
        m = self.scen['dur'].get(self.act, 600)
        self.heap.push(dict(t=self.t - m * math.log(1 - self.rng()), type='ACT'))

    def _sample_harvest(self):
        c = self.cfg
        mean = c.get('P_' + self.act, 0) * 1e-6 * c['etaH'] * (c.get('hscale') or 1)
        if mean <= 0:
            return 0.0
        s = math.sqrt(math.log(1 + c['cv'] ** 2)); mu = math.log(mean) - s * s / 2
        return math.exp(mu + s * gauss(self.rng))

    # ---- exact energy integration between events: dE/dt = a - bE
    def _advance(self, t1):
        d = self.d; b = d['b']
        while self.t < t1 - 1e-12:
            dt = t1 - self.t
            Pl = d['Psleep'] if self.mode == 'on' else d['Phib'] if self.mode == 'hib' else d['Poff']
            a = self.Ph - Pl; Einf = a / b
            tc, kind = dt, None
            if self.E >= d['Emax'] - 1e-12 and Einf >= d['Emax']:      # clamped by over-voltage protection
                self.acc['harv'] += self.Ph * dt; self.acc['quies'] += Pl * dt
                self.acc['leak'] += b * d['Emax'] * dt; self.acc['wasted'] += (a - b * d['Emax']) * dt
                self._account(dt); self.t = t1; break
            if self.mode != 'off' and Einf < d['Emin'] and self.E > d['Emin']:   # brown-out before t1?
                x = math.log((self.E - Einf) / (d['Emin'] - Einf)) / b
                if x < tc: tc, kind = x, 'brown'
            if Einf > d['Emax'] and self.E < d['Emax']:                            # store full before t1?
                x = math.log((self.E - Einf) / (d['Emax'] - Einf)) / b
                if x < tc: tc, kind = x, 'full'
            e = math.exp(-b * tc); En = Einf + (self.E - Einf) * e
            intE = Einf * tc + (self.E - Einf) * (1 - e) / b
            self.acc['harv'] += self.Ph * tc; self.acc['quies'] += Pl * tc; self.acc['leak'] += b * intE
            self.E = En; self._account(tc); self.t += tc
            if kind == 'full': self.E = d['Emax']
            if kind == 'brown':
                self.E = d['Emin']; self._brownout('Supply fell below V_min')

    def _account(self, dt):
        s = self.st
        if self.mode == 'on':
            if self.cur_int: s['availT'] += dt
            s['tierT'][max(0, self.tier) if self.adaptive else 0] += dt
        elif self.mode == 'hib':
            s['hibT'] += dt; s['tierT'][4] += dt
        else:
            s['offT'] += dt

    # ---- state changes
    def _schedule_adv(self, dt):
        self.adv_gen += 1; t = self.t + dt + self.rng2() * 0.01        # BLE random advDelay 0-10 ms
        self.next_adv = t; self.heap.push(dict(t=t, type='ADV', g=self.adv_gen))

    def _brownout(self, why):
        self.mode = 'off'; self.st['brown'] += 1
        if self.st['firstBrownT'] is None: self.st['firstBrownT'] = self.t
        self.adv_gen += 1; self.next_adv = math.inf; self.cur_int = None; self.tier = -1; self.burst = 0
        self._log('BROWNOUT', why + ' - uncontrolled reset, state lost')

    def _boot(self):
        self.E -= self.d['Eboot']; self.acc['boot'] += self.d['Eboot']; self.st['boots'] += 1
        self.mode = 'on'; self._log('BOOT', 'Cold boot at V_on'); self._on_start()

    def _on_start(self):
        if self.adaptive:
            self.tier = -1; self._decide()
            if self.mode == 'on': self._schedule_adv(0.05)
        else:
            self.cur_int = self.p['interval'] / 1000; self._schedule_adv(0.05)

    def _enter_hibernate(self):
        self.mode = 'hib'; self.tier = 4; self.adv_gen += 1; self.next_adv = math.inf; self.cur_int = None
        self._log('HIBERNATE', 'Charge below critical - radio off, state kept')

    # ---- core of the energy-aware protocol (Objective 2)
    def _decide(self):
        p, d, soc, th, h = self.p, self.d, self.soc(), self.th, self.p['hyst']
        if self.tier < 0 or self.tier > 3:
            k = 0
            while k < 4 and soc < th[k]: k += 1
        else:
            k = self.tier
            while k > 0 and soc >= th[k - 1] + h: k -= 1          # move up only above threshold + hysteresis
            while k < 4 and soc < th[k]: k += 1
        if k >= 4:
            if self.mode == 'on': self._enter_hibernate()
            return
        T = self.T[k]; floor = self.E_res if k <= 2 else self.E_crit
        p_avail = self.ewma + max(0.0, self.E - floor) / p['Th'] - (d['Psleep'] + d['b'] * self.E)   # energy budget
        iv = self.E_ev[k] / p_avail if p_avail > 0 else math.inf
        iv = min(T['maxInt'] / 1000, max(T['minInt'] / 1000, iv))
        iv = math.floor(iv * 100 + 0.5) / 100
        if k != self.tier:
            self._log('TIER', f"{TIER_NAMES[self.tier] if self.tier >= 0 else 'START'} -> {T['name']} (SoC {soc * 100:.1f}%)")
        self.tier = k; self.cur_int = iv
        if self.mode == 'on' and not self.burst and self.next_adv - self.t > iv + 0.011:
            self._schedule_adv(iv)

    # ---- advertising event with the reserve guard (Objective 3)
    def _on_adv(self, e):
        if e['g'] != self.adv_gen or self.mode != 'on':
            return
        d = self.d; bursting = self.adaptive and self.burst > 0
        Eev = (self.E_burst if bursting else self.E_ev[self.tier]) if self.adaptive else self.E_base
        if self.adaptive and self.p['guard']:
            floor = self.E_crit if bursting else (self.E_res if self.tier <= 2 else self.E_crit)
            if self.E - Eev < floor:
                self.st['skipped'] += 1; self.burst = 0; self._decide()
                if self.mode == 'on': self._schedule_adv(self.cur_int)
                return
        if self.E - Eev < d['Emin']:
            self.acc['radio'] += self.E - d['Emin']; self.E = d['Emin']
            self.st['failTx'] += 1; self.st['lost'] += 1; self._brownout('Voltage collapse during TX'); return
        self.E -= Eev; self.acc['radio'] += Eev; self.st['adv'] += 1
        p = (self.p_burst if bursting else self.p_ev[self.tier]) if self.adaptive else self.p_base
        if self.rng3() < p:                                  # did the phone receive this packet?
            g = self.t - self.last_rx; self.st['gap2'] += g * g; self.last_rx = self.t; self.st['rx'] += 1
        else:
            self.st['lost'] += 1
        if self.st['firstAdvT'] is None: self.st['firstAdvT'] = self.t
        if bursting:
            self.burst -= 1; nxt = self.p['burstInt'] / 1000
        else:
            nxt = self.cur_int if self.adaptive else self.p['interval'] / 1000
        self._schedule_adv(nxt)

    # ---- 1 s control tick
    def _on_tick(self):
        d = self.d; dT = self.t - self.last_tick
        if dT > 0:
            meas = (self.acc['harv'] - self.last_harv) / dT
            if self.adaptive:
                prev = self.ewma; self.ewma = self.p['alpha'] * meas + (1 - self.p['alpha']) * prev   # EWMA harvest estimate
                if (self.p['burst'] and self.mode == 'on' and self.tier >= 2 and meas > 50e-6
                        and meas > 3 * max(prev, 10e-6) and self.t - self.burst_cool > 60):
                    self.burst = self.p['burstN']; self.burst_cool = self.t; self.st['bursts'] += 1
                    self._log('BURST', 'Motion spike - fast adverts from reserve'); self._schedule_adv(0.02)
            self.last_harv = self.acc['harv']; self.last_tick = self.t
        if self.mode == 'off':
            if self.E >= d['Eon']: self._boot()
        elif self.mode == 'hib':
            if self.soc() >= self.p['crit'] + self.p['hyst']:
                self.mode = 'on'; self.tier = 3; self._log('WAKE', 'Recovered above critical + hysteresis'); self._decide()
                if self.mode == 'on': self._schedule_adv(0.05)
        elif self.adaptive and not self.burst:
            self._decide()
        self.heap.push(dict(t=self.t + 1, type='TICK'))

    def _handle(self, e):
        k = e['type']
        if k == 'ADV': self._on_adv(e)
        elif k == 'TICK': self._on_tick()
        elif k == 'HARV':
            self.Ph = self._sample_harvest(); self.heap.push(dict(t=self.t + 3 + 12 * self.rng(), type='HARV'))
        elif k == 'ACT':
            self.act = self._pick_state(self.act); self.Ph = self._sample_harvest()
            self._log('ACT', 'Activity -> ' + self.act); self._schedule_activity()

    def run_until(self, T):
        """Main discrete-event loop."""
        while len(self.heap) and self.heap.peek()['t'] <= T:
            e = self.heap.pop(); self._advance(e['t']); self.events += 1; self._handle(e)
        self._advance(T)

    # ---- results
    def latency(self):
        T = max(self.t, 1e-9); g = self.t - self.last_rx
        return (self.st['gap2'] + g * g) / (2 * T)

    def pdr(self):
        n = self.st['rx'] + self.st['lost']
        return 100 * self.st['rx'] / n if n else 0.0

    def metrics(self):
        s, a, T = self.st, self.acc, self.t
        bal = self.E0 + a['harv'] - a['quies'] - a['leak'] - a['radio'] - a['boot'] - a['wasted'] - self.E
        return dict(brown=s['brown'], boots=s['boots'], adv=s['adv'], rx=s['rx'], lost=s['lost'],
                    pdr=self.pdr(), latency=self.latency(), avail=100 * s['availT'] / T,
                    availH=s['availT'] / 3600, firstBrown=s['firstBrownT'],
                    harv=a['harv'] * 1e3, radio=a['radio'] * 1e3, socEnd=self.soc() * 100,
                    tierT=[100 * x / T for x in s['tierT']], offPct=100 * s['offT'] / T,
                    balance=bal, events=self.events)


def run_sim(cfg, proto, scenario, seed, hours, soc0=None):
    s = Sim(cfg, proto, scenario, seed, soc0=soc0)
    s.run_until(hours * 3600)
    return s
