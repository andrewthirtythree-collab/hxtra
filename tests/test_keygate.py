"""The Keyed Gate block's kernel (src/sharc/keygate.asm) in ssim: KG_TAP in place of primitive 0x2720's process code,
called as the Input block's noise gate calls it, and a Gate instance keyed from it."""
import math
import random

import pytest

import sasm
import ssim

PATH = 'src/sharc/keygate.asm'
PM, DM = 0xE5000, 0xE7000
TAP_I0, GATE_I0, BI, BO = 0xB3000, 0xB3400, 0xB8000, 0xB8100
ORIG_2720 = 0x8e8f2
FS = 48000


def assemble():
    src = open(PATH).read().replace('KIT_PM_BASE', f'{PM:#x}').replace('KIT_DM_BASE', f'{DM:#x}')
    # the stock 0x2720 code stands in as a mark that it ran (with I1 as it came) and a return
    src += (f'\n.section pm {ORIG_2720:#x}\n    DM(0xB7000) = I1;\n    DM(0xB7002) = R0;\n    R0 = DM(0xB7001);\n'
            '    R0 = R0 + 1;\n    DM(0xB7001) = R0;\n    R0 = DM(0xB7002);\n    RTS;\n')
    return sasm.assemble(src, 'keygate.asm')


class Rig:
    """The kernel loaded once; key() and gate() each run one 16-sample block of their instance."""

    def __init__(self, open_db=-50.0, close_db=-60.0, hold=0.01, decay=0.05, key=0, kind=1, seed=1):
        secs, self.syms = assemble()
        self.sim = ssim.Sim()
        self.sim.strict_cbufen = True
        self.sim.load(secs)
        self.rnd = random.Random(seed)
        for k in range(0x91):
            self.sim.wr(GATE_I0 + k, 0)
        self.sim.wr(GATE_I0 + 5, key)
        self.sim.wr(GATE_I0 + 10, kind)                   # Type: 0 Smooth, 1 Hard (most tests here: Hard)
        for pid, v in ((6, open_db), (7, close_db), (8, hold), (9, decay)):
            self.sim.wr(GATE_I0 + pid, ssim.f2b(v))

    def _call(self, i0, block, in_place=False, entry='CUSTOM_AMP_ENTRY'):
        sim = self.sim
        out_buf = BI if in_place else BO
        for k in range(16):
            sim.wr(BI + k, ssim.f2b(block[k]))
        for r in range(16):
            sim.setR(r, self.rnd.getrandbits(32))
            sim.alt[r] = self.rnd.getrandbits(32)
        keep = [c for c in range(0x10, 0x50) if c not in (0x10, 0x11, 0x12, 0x40, 0x41, 0x42)]
        for c in keep:
            sim.set(c, self.rnd.getrandbits(8) or 1 if 0x30 <= c < 0x40 else self.rnd.getrandbits(32) & 0xFFFFF)
        for c, v in ((0x10, i0), (0x11, BI), (0x12, out_buf)):
            sim.set(c, v)
        sim.u[ssim.U_MODE1] |= 0x1000000
        before = [sim.R(r) for r in range(16)], list(sim.alt[:16]), {c: sim.get(c) for c in keep}, sim.u[ssim.U_MODE1]
        sim.call(self.syms[entry], limit=20_000)
        after = [sim.R(r) for r in range(16)], list(sim.alt[:16]), {c: sim.get(c) for c in keep}, sim.u[ssim.U_MODE1]
        assert after == before, 'registers changed'
        assert not sim.loops and not sim.pcstk
        return [ssim.b2f(sim.rd(out_buf + k)) for k in range(16)]

    def tap(self, block):
        """One of the Input block's 0x2720 calls, input at I1; the stock code must run, with I1 as it came."""
        ran = self.sim.rd(0xB7001)
        self._call(TAP_I0, block, entry='KG_TAP')
        assert self.sim.rd(0xB7001) == ran + 1 and self.sim.rd(0xB7000) == BI, 'the stock 0x2720 code ran, as called'

    def frame(self, key_l, key_r, gate_in):
        """A frame as the unit runs it: the Input block's two gate cores, then the Keyed Gate."""
        self.tap(key_l)
        self.tap(key_r)
        return self.gate(gate_in)

    def peak(self):
        return ssim.b2f(self.sim.rd(self.syms['KG_PEAK']))

    def stamp(self):
        return self.sim.rd(self.syms['KG_STAMP'])

    def gate(self, block, in_place=False):
        return self._call(GATE_I0, block, in_place)

    def gain(self):
        return ssim.b2f(self.sim.rd(GATE_I0 + 21))


def tone(level, n=16, f=110.0, start=0):
    return [level * math.sin(2 * math.pi * f * (start + k) / FS) for k in range(n)]


def db(x):
    return 20 * math.log10(x)


def test_the_tap_publishes_only_the_two_calls_after_a_gate():
    """Unarmed (no Keyed Gate yet), the tap only runs the stock code; after a Gate call the next two calls publish the
    larger channel's peak, and a third (a stock Noise Gate later in the chain) does not."""
    rig = Rig()
    rig.tap(tone(0.3, f=1000.0))
    assert rig.stamp() == 0
    rig.gate([0.0] * 16)
    rig.tap(tone(0.1, f=1000.0))
    rig.tap([0.0] * 16)                                    # a silent right channel: the left's peak stands
    assert rig.stamp() == 2 and rig.peak() == pytest.approx(max(abs(x) for x in tone(0.1, f=1000.0)), rel=1e-6)
    rig.tap([0.9] * 16)
    assert rig.stamp() == 2 and rig.peak() < 0.2
    rig.gate([0.0] * 16)
    rig.tap([0.0] * 16)                                    # a silent first channel: the second sets it
    rig.tap([-0.4] * 16)
    assert rig.peak() == pytest.approx(0.4)


def test_without_a_key_the_gate_follows_its_own_input():
    """No Key block: a loud note opens the gate, silence closes it after Hold and Decay."""
    rig = Rig(hold=0.01, decay=0.02)
    for b in range(150):                                  # 50 ms of a -20 dB note
        out = rig.gate(tone(0.1, start=16 * b))
    assert rig.gain() == pytest.approx(1.0, abs=1e-3)
    assert out == pytest.approx(tone(0.1, start=16 * 149), abs=1e-4)
    for _ in range(3000):                                 # 1 s of -80 dB hiss
        rig.gate([1e-4] * 16)
    assert rig.gain() < 1e-6


def test_the_input_holds_the_gate_open_and_shuts_it_on_its_own_level():
    """The Gate's input stays loud (an amp's hiss and hum after the loop); only the clean guitar's level decides."""
    rig = Rig(hold=0.0, decay=0.01)
    noisy = [0.05] * 16                                   # -26 dB of noise at the gate, above Open
    for b in range(300):                                  # playing: open
        rig.frame(tone(0.2, start=16 * b), [0.0] * 16, noisy)
    assert rig.gain() == pytest.approx(1.0, abs=1e-3)
    for b in range(3000):                                 # the guitar quiet (-90 dB): closed, though the gate's input is loud
        out = rig.frame([3e-5] * 16, [3e-5] * 16, noisy)
    assert rig.gain() < 1e-6 and max(abs(x) for x in out) < 1e-7


def test_key_on_self_ignores_the_input():
    rig = Rig(hold=0.0, decay=0.01, key=1)
    for _ in range(600):
        rig.frame([3e-5] * 16, [3e-5] * 16, [0.05] * 16)
    assert rig.gain() == pytest.approx(1.0, abs=1e-3)


def test_with_the_input_gate_off_it_gates_on_its_own_input():
    """The Input block's gate Off: no 0x2720 calls, so the key goes stale and the block listens to itself."""
    rig = Rig(hold=0.0, decay=0.01)
    for _ in range(50):
        rig.frame([3e-5] * 16, [3e-5] * 16, [0.05] * 16)
    for _ in range(600):
        rig.gate([0.05] * 16)
    assert rig.gain() == pytest.approx(1.0, abs=1e-3)


def test_hold_keeps_it_open_then_decay_fades_it():
    rig = Rig(open_db=-40.0, close_db=-50.0, hold=0.1, decay=0.05)
    for b in range(100):
        rig.gate(tone(0.1, start=16 * b))
    blocks_open = 0
    while rig.gain() > 0.99 and blocks_open < 2000:
        rig.gate([0.0] * 16)
        blocks_open += 1
    # the level falls from -20 dB to under -50 dB in about 1.5 x 20 ms x ln(10) = 70 ms, then 100 ms of Hold
    assert 0.15 < blocks_open * 16 / FS < 0.2
    for _ in range(int(0.05 * FS / 16)):                  # one Decay time constant: about 1/e
        rig.gate([0.0] * 16)
    assert rig.gain() == pytest.approx(math.exp(-1), abs=0.03)


def test_close_above_open_acts_as_open():
    rig = Rig(open_db=-40.0, close_db=-10.0, hold=0.0, decay=0.01)
    for b in range(200):
        rig.gate(tone(0.05, start=16 * b))                # -26 dB: over Open, under the Close knob
    assert rig.gain() == pytest.approx(1.0, abs=1e-3)


@pytest.mark.parametrize('level_db, opens', [(-45.0, True), (-55.0, False)])
def test_the_open_threshold_in_db(level_db, opens):
    rig = Rig(open_db=-50.0, close_db=-60.0, hold=0.0, decay=0.01)
    a = 10 ** (level_db / 20)
    for b in range(300):
        rig.gate(tone(a, f=1000.0, start=16 * b))
    assert (rig.gain() > 0.99) == opens


@pytest.mark.parametrize('under_db', [3.0, 10.0, 20.0])
def test_smooth_is_a_1_to_4_expander_under_close(under_db):
    """Smooth: a steady level `under_db` under Close settles at 3 x under_db of cut, not at silence."""
    rig = Rig(open_db=-40.0, close_db=-50.0, hold=0.0, decay=0.01, kind=0)
    a = 10 ** ((-50.0 - under_db) / 20)
    for _ in range(3000):
        rig.gate([a] * 16)
    assert db(rig.gain()) == pytest.approx(-3 * under_db, abs=0.2)


def test_smooth_opens_fully_above_open():
    rig = Rig(open_db=-40.0, close_db=-50.0, hold=0.0, decay=0.01, kind=0)
    for b in range(300):
        rig.gate(tone(0.05, start=16 * b))
    assert rig.gain() == pytest.approx(1.0, abs=1e-3)


def test_smooth_lets_a_note_fade_where_hard_cuts_it():
    """A note dying away at 20 dB a second through Close: 0.3 s after it crosses Close, Hard has shut it (Decay
    10 ms) while Smooth still passes it about 18 dB down, and keeps falling with it."""
    gains = {}
    for kind in (0, 1):
        rig = Rig(open_db=-40.0, close_db=-50.0, hold=0.0, decay=0.01, kind=kind)
        for b in range(300):                               # -30 dB, then falling 20 dB/s: Close (-50 dB) at 1 s
            level = 10 ** ((-30.0 - 20.0 * max(0.0, (b - 100) * 16 / FS)) / 20)
            rig.frame(tone(level, start=16 * b), [0.0] * 16, [0.01] * 16)
        n = 100 + int(1.3 * FS / 16)
        for b in range(300, n):
            level = 10 ** ((-30.0 - 20.0 * (b - 100) * 16 / FS) / 20)
            rig.frame(tone(level, start=16 * b), [0.0] * 16, [0.01] * 16)
        gains[kind] = db(max(rig.gain(), 1e-12))
    assert gains[1] < -100
    assert -25 < gains[0] < -12
