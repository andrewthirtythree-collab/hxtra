#!/usr/bin/env python3
"""patch_keygate.py - the Keyed Gate block: a noise gate keyed from the Input block's clean guitar.

usage: patch_keygate.py <in .hxf | L6IA> <out .hxf | L6IA> [--donor stock.hxf]

Run after tools/patch_click.py (build.py does it for HX_KEYGATE=1 builds), before tools/patch_comp.py. It adds:
  - the model Dynamics > Mono > Keyed Gate (Entries 0, 3, 4; preset ID PRESET_ID): a copy of the mono effect
    host whose one member is the primitive 0x012e, with the knobs Key (Input, Self; the Horizon Gate's Mode
    record), Open Threshold, Close Threshold, Hold and Decay (the Hard Gate's records), Type (Smooth, Hard; the Wringer Fuzz's Fuzz Type record), as pids 5..10;
  - the kernel src/sharc/keygate.asm (Entry 2) in Block 3 right under the Metronome's kernel, and its shared
    words (the Input block's level) right under that;
  - primitive 0x2720's process entry (the Input block's noise gate core) pointed at the kernel's KG_TAP, which
    publishes the clean guitar's level, then runs the stock code.

Use: one Keyed Gate where the noise is, after the amp (the 4-cable method's return), with the Input block's gate
On: it opens and closes on the clean guitar's level. With Key on Self, or the Input gate Off, it gates on its own
input.
"""
import argparse
import os
import struct
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, 'sharc'))

import kit_patch_amp as kit
import patch_amp as pa
import nam_multi
import patch_click

ASM = os.path.join(SCRIPT_DIR, '..', 'src', 'sharc', 'keygate.asm')
NAME, DISPLAY = 'HD2_GateKeyedGateMono', 'Keyed Gate'
CLONE = 'HD2_GateHardGateMono'      # Entry 0 and 3 records: a mono dynamics effect, so it looks and lists as a gate
CATEGORY = 3                        # Dynamics
HOST = 'HD2_VolPanVolMono'
PRESET_ID = 380                     # fixed for ever: presets store it
DSP_COST = 30
PRIM = (0x012e, 0x200058)           # kit_patch_amp.UNUSED_SHARC_PRIMS' third: NAM has the first, the Metronome the second
INSTANCE = {'dm_words': 0x91, 'pm_words': 0x0d, 'sdram_words': 0}
HARD = 'HD2_GateHardGateMono'
# (slot, source model, source parameter, pid, display name, default) as kit_patch_amp.nam_params takes them
PARAMS = ((1, 'HD2_GateHorizonGateMono', 'Mode', 5, 'Mode', None),
          (2, HARD, 'OpenThreshold', 6, None, None),
          (3, HARD, 'CloseThreshold', 7, None, None),
          (4, HARD, 'HoldTime', 8, None, None),
          (5, HARD, 'Decay', 9, None, 0.1),
          (6, 'HD2_DistWringerFuzzMono', 'FuzzType', 10, 'Type', None))
MODES = ('Input', 'Self')
TYPES = ('Smooth', 'Hard')
TAP = (0x2720, 0x8e8f2)              # the Input block's (and the Noise Gate's) gate core, and its stock process code


def gate_params(nam_params):
    """kit_patch_amp.nam_params for PARAMS: Key as a two-value list, Input by default."""
    def wrapped(e3, info, spec=None):
        out = nam_params(e3, info, spec)
        for p in out:
            if p['slot'] == 1:
                p['raw'][5], p['raw'][6], p['raw'][7] = 0, len(MODES) - 1, 0
                p['default_word'] = 0
                p['meta'] = [('displayType', 7, 'list')] + [(str(k), 7, v) for k, v in enumerate(MODES)] + \
                            [('displayName', 7, 'Key'), ('displayNameShort', 7, 'Key')]
            elif p['slot'] == 6:
                p['raw'][5], p['raw'][6], p['raw'][7] = 0, len(TYPES) - 1, 0
                p['default_word'] = 0
                p['meta'] = [('displayType', 7, 'list')] + [(str(k), 7, v) for k, v in enumerate(TYPES)] + \
                            [('displayName', 7, 'Type'), ('displayNameShort', 7, 'Type')]
        return out
    return wrapped


def kernel_sizes():
    """(DM words, instructions) of the kernel."""
    src = open(ASM).read().replace('KIT_PM_BASE', '0xe5000').replace('KIT_DM_BASE', '0xe7000')
    secs, _ = kit.sasm.assemble(src, 'keygate.asm')
    return tuple(sum(len(s.words) for s in secs if s.kind == k) for k in ('dm', 'pm'))


def nw(pm):
    """A Block 3 instruction address as a normal-word address."""
    return nam_multi.NW3 + (pm - nam_multi.NW3) * 3 // 2


def place(lay=None):
    """(DM base, PM base): the code right under the Metronome's kernel, its shared words right under the code.
    tools/patch_comp.py keeps the GR meter's code under the DM base."""
    lay = lay or patch_click.nam_layout()
    n_dm, n_pm = kernel_sizes()
    n_pm += n_pm & 1
    pm = patch_click.pm_base(patch_click.kernel_size(), lay) - n_pm
    dm = nw(pm) - n_dm
    if dm < lay.b3_to:
        raise SystemExit(f'the Keyed Gate ({n_dm} words, {n_pm} instructions) does not fit between NAM\'s slots '
                         f'({lay.b3_to:#x}) and the Metronome\'s kernel')
    return dm, pm


def add_keygate(l6ia_bytes, log=print):
    """The Keyed Gate model and its kernel. Returns (new l6ia bytes, info dict)."""
    info = kit.parse_entry3_full(kit.get_entry_swapped(l6ia_bytes, 3))
    if any(m['name'] == NAME for m in info['models']):
        raise SystemExit(f'{NAME} is already in this image')
    assert PRIM[0] not in kit.stock_prims_in_use(l6ia_bytes), f'primitive {PRIM[0]:#x} is in use'
    saved = kit.nam_params
    kit.nam_params = gate_params(saved)
    try:
        out, minfo = kit.add_model(l6ia_bytes, CLONE, NAME, DISPLAY, preset_id=PRESET_ID, dsp_cost=DSP_COST,
                                   params=PARAMS, prim_id=PRIM[0], instance=INSTANCE, host=HOST, category=CATEGORY,
                                   log=log)
    finally:
        kit.nam_params = saved
    dm, pm = place()
    src = open(ASM).read().replace('KIT_DM_BASE', f'{dm:#x}')
    out, kinfo = kit.patch_kernel(out, src, 'keygate.asm', prim=PRIM, pm_base=pm, relocate=False, log=log)
    _, syms = kit.sasm.assemble(src.replace('KIT_PM_BASE', f'{pm:#x}'), 'keygate.asm')
    assert syms['CUSTOM_AMP_ENTRY'] == kinfo['entry']
    entries = pa.l6ia.parse(out)
    e2 = out[entries[2]['payload_off']: entries[2]['payload_off'] + entries[2]['payload_len']]
    ldr = pa.wrap_primitive(e2, f'{TAP[0]:#x}:{TAP[1]:#x}', syms['KG_TAP'])
    out, _, _ = pa.l6ia.rebuild_archive(out, {2: pa.l6ia.swap32(pa.inject_sharc_ldr_blocks(e2, ldr))})
    minfo.update(entry=kinfo['entry'], tap=syms['KG_TAP'], dm_base=dm, pm_base=pm)
    return out, minfo


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input')
    ap.add_argument('output')
    ap.add_argument('--donor', help='Stock .hxf of the unit, when input is a raw L6IA and output a .hxf')
    args = ap.parse_args()
    l6ia_bytes, is_hxf = kit.load_l6ia_from_any(args.input)
    out, _ = add_keygate(l6ia_bytes)
    kit.save_l6ia_to_any(out, args.output, donor_hxf=args.input if is_hxf else args.donor)
    print(f"[+] Wrote {args.output}")


if __name__ == '__main__':
    main()
