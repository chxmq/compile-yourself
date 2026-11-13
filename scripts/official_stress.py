#!/usr/bin/env python3
"""echo and reverse on the official VM_tier1and2 (needs Docker): empty, 1 byte, newline only, and random inputs up to
5000 bytes (all byte values 1..255). Each output must equal the expected bytes and the output of sqvm."""
import os, random, subprocess, sys
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
random.seed(7)
cases = {'empty': b'', 'one': b'a', 'newline': b'\n', 'text': b'hello world\n',
         'bytes1000': bytes(random.randrange(1, 256) for _ in range(1000)),
         'ascii1000': bytes(random.randrange(32, 127) for _ in range(1000)),
         'lines1000': bytes(random.choice(b'ab\n') for _ in range(1000)),
         'bytes5000': bytes(random.randrange(1, 256) for _ in range(5000))}
bad = 0
for prog, expect in (('echo', lambda b: b), ('reverse', lambda b: b[::-1])):
    for name, data in cases.items():
        ours = subprocess.run(['vm/sqvm', f'tier1/{prog}.txt'], input=data, capture_output=True)
        off = subprocess.run(['scripts/official_vm.sh', f'tier1/{prog}.txt'], input=data, capture_output=True)
        if not (off.returncode == 0 and off.stdout == expect(data) and ours.stdout == off.stdout):
            print(f'FAIL {prog} {name} (official rc={off.returncode})'); bad += 1
print(f'{len(cases) * 2} echo/reverse cases, {bad} failures')
sys.exit(1 if bad else 0)
