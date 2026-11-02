#!/usr/bin/env python3
"""Find the PlayerCamera stick step in a TotK main: the code that turns the right stick into the camera's turn input.

weeping-of-the-empire (MIT) patches this step on 1.0.0 (games/totk/patches/sites_v100.py, the `mouse` patch): it adds the
mouse's motion to the processed stick right before the step stores it, so the camera turns linearly, one to one, with
no dead zone, response curve or speed cap. To do the same here the site must be found in the 1.2.1 and 1.4.3 mains.

What the step looks like on 1.0.0 (read there, not assumed for the others): it stores the processed stick x and y as
two floats at [x20, #0x418] and [x20, #0x41c] (x20 = camera + 0x428, so camera + 0x840 / +0x844); the dead zone is
0.005 (0x3ba3d70a), and the response curve is a call to powf. This script reports where the same shape occurs:

  1. two `str s<t>, [x<n>, #imm]` stores to the same base with the offsets 0x418 / 0x41c within a few instructions,
  2. every place that builds the float 0.005 (movz w, #0xd70a / movk w, #0x3ba3, lsl #16) or loads it from a literal,
     and which of those lie in the same function as a store pair from 1.

Usage:  python tools/find_camera_stick.py --main build/extracted/v143/exefs/main
Prints module offsets (the numbers `site main <offset>` takes) and the words around each hit. It changes nothing.
"""
import argparse
import struct
import sys
from pathlib import Path

DEAD_ZONE_BITS = 0x3BA3D70A   # 0.005f


def str_s(word):
    """(rt, rn, byte offset) of `str s<rt>, [x<rn>, #off]` (unsigned offset form), else None."""
    if word & 0xFFC00000 != 0xBD000000:
        return None
    return word & 31, (word >> 5) & 31, ((word >> 10) & 0xFFF) * 4


def movz_w(word):
    """(rd, imm16, shift) of movz w<rd>, #imm16 {, lsl #shift}, else None."""
    if word & 0xFF800000 != 0x52800000:
        return None
    return word & 31, (word >> 5) & 0xFFFF, ((word >> 21) & 3) * 16


def movk_w(word):
    if word & 0xFF800000 != 0x72800000:
        return None
    return word & 31, (word >> 5) & 0xFFFF, ((word >> 21) & 3) * 16


def function_start(words, i, limit=4000):
    """Index of the nearest prologue above word i: a pre-indexed stp of x29 / x30 or a `sub sp, sp, #n`."""
    for j in range(i, max(0, i - limit), -1):
        w = words[j]
        if w & 0xFFC003E0 == 0xA98003E0 and (w >> 10) & 0x1F == 30 and ((w >> 5) & 31) == 31:   # stp xA, x30, [sp, #-n]!
            return j
        if w & 0xFF0003FF == 0xD10003FF:   # sub sp, sp, #imm
            return j
    return None


def scan(text, base=0):
    """text: the .text bytes, base: the module offset of text[0]. Returns (store_pairs, constants)."""
    n = len(text) // 4
    words = struct.unpack_from(f'<{n}I', text)
    pairs, consts = [], []
    near = {}   # (rn, off) -> last index of a store to it
    for i, w in enumerate(words):
        s = str_s(w)
        if s:
            rt, rn, off = s
            if off in (0x418, 0x41C):
                other = 0x41C if off == 0x418 else 0x418
                j = near.get((rn, other))
                if j is not None and i - j <= 12:
                    pairs.append((min(i, j), max(i, j), rn))
                near[(rn, off)] = i
        z = movz_w(w)
        if z and z[1] == (DEAD_ZONE_BITS & 0xFFFF) and z[2] == 0:
            for k in range(i + 1, min(i + 4, n)):
                kk = movk_w(words[k])
                if kk and kk[0] == z[0] and kk[2] == 16 and kk[1] == (DEAD_ZONE_BITS >> 16):
                    consts.append((i, k))
    return words, pairs, consts


def show(words, base, i, before=14, after=14):
    for j in range(max(0, i - before), min(len(words), i + after + 1)):
        mark = '  <==' if j == i else ''
        print(f'    {base + 4 * j:08x}: {words[j]:08x}{mark}')


def summarize(words, base, i, span=700):
    """What the function around the 0.005f at word i does, in a few lines: its stores and loads of floats at a
    camera-sized offset, the calls it makes, where it ends."""
    f = function_start(words, i)
    if f is None:
        return None
    end = f
    for j in range(f + 1, min(len(words), f + span)):
        if words[j] == 0xD65F03C0:   # ret
            end = j
            break
    else:
        end = min(len(words) - 1, f + span)
    stores, loads, calls = [], [], 0
    for j in range(f, end + 1):
        w = words[j]
        if w & 0xFC000000 == 0x94000000:
            calls += 1
        if w & 0xFFC00000 == 0xBD000000:
            stores.append((base + 4 * j, w & 31, (w >> 5) & 31, ((w >> 10) & 0xFFF) * 4))
        elif w & 0xFFC00000 == 0xBD400000:
            loads.append((base + 4 * j, w & 31, (w >> 5) & 31, ((w >> 10) & 0xFFF) * 4))
    return f, end, calls, stores, loads


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--main', required=True, help='the extracted exefs/main (an NSO)')
    ap.add_argument('--context', type=int, default=14, help='words to print around each hit')
    args = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from recomp.nso import Nso
    nso = Nso(args.main)
    off, size = nso.text
    text = bytes(nso.image[off:off + size])
    words, pairs, consts = scan(text, off)
    print(f'{args.main}: .text {size:#x} bytes, {len(words)} words')

    print(f'\n== store pairs [xN, #0x418] / [xN, #0x41c] within 12 words: {len(pairs)}')
    for a, b, rn in pairs:
        f = function_start(words, a)
        fs = f'function from about {off + 4 * f:#x}' if f is not None else 'no prologue found'
        print(f'\n  stores at {off + 4 * a:#x} and {off + 4 * b:#x}, base x{rn}; {fs}')
        show(words, off, a, args.context, args.context)

    print(f'\n== 0.005f built with movz / movk: {len(consts)}')
    pair_funcs = {function_start(words, a) for a, _, _ in pairs}
    for i, k in consts:
        f = function_start(words, i)
        tag = '   <== same function as a store pair' if f in pair_funcs and f is not None else ''
        print(f'  {off + 4 * i:#x} (movk at {off + 4 * k:#x}); function from about '
              f'{(off + 4 * f) if f is not None else 0:#x}{tag}')
    print('\n== what each function with a 0.005f does (float stores / loads at offsets >= 0x100, calls)')
    seen = set()
    for i, _ in consts:
        r = summarize(words, off, i)
        if r is None or r[0] in seen:
            continue
        seen.add(r[0])
        f, end, calls, stores, loads = r
        print(f'\n  function {off + 4 * f:#x} .. {off + 4 * end:#x}, {end - f + 1} words, {calls} calls')
        print('    stores:', ' '.join(f's{t},[x{n},#{o:#x}]@{a:x}' for a, t, n, o in stores if o >= 0x100) or '-')
        print('    loads: ', ' '.join(f's{t},[x{n},#{o:#x}]@{a:x}' for a, t, n, o in loads if o >= 0x100) or '-')
    if not pairs:
        print('\nNo store pair found: the camera layout differs on this version. Send this output anyway; the 0.005f '
              'list narrows the function down.')


if __name__ == '__main__':
    main()
