#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI: distinguishability analysis of an arm run.

The analysis lives in fluxpcb.meta.distinguish so it can be unit-tested;
this file only locates the package and prints/writes the result.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, '..', 'flux-neurotransmitter'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', required=True)
    ap.add_argument('--out', default='')
    args = ap.parse_args()
    from fluxpcb.meta.distinguish import analyse, render
    res = analyse(args.run)
    render(res)
    out = args.out or os.path.join(args.run, 'distinguish.json')
    tmp = out + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(res, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, out)
    print('wrote', out)


if __name__ == '__main__':
    main()
