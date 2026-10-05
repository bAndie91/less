#!/usr/bin/env python3
# Tests for --sticky-header.  Drives ./less in a tmux session.
# Usage: lesstest/sticky-header.py [path-to-less]     (needs tmux, python3)
import os, subprocess, sys, tempfile, time

LESS = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), '..', 'less'))
TMP = tempfile.mkdtemp(prefix='sticky-test-')
SESSION = 'sticky-test-%d' % os.getpid()
fails = 0

def tmux(*a):
    return subprocess.run(('tmux',) + a, capture_output=True, text=True).stdout

class Less:
    def __init__(self, args, h=12, w=60):
        tmux('new-session', '-d', '-x', str(w), '-y', str(h), '-s', SESSION,
             'env LESS= TERM=xterm %s %s' % (LESS, args))
        self.settle()
    def key(self, *keys):
        for k in keys:
            tmux('send-keys', '-t', SESSION, k)
        self.settle()
    def text(self, s):
        tmux('send-keys', '-t', SESSION, '-l', '--', s)
        self.settle()
    def settle(self):
        time.sleep(0.25)
    def rows(self):
        return [r.rstrip().replace('\t', ' ').strip() for r in tmux('capture-pane', '-p', '-t', SESSION).split('\n')]
    def close(self):
        tmux('kill-session', '-t', SESSION)

def check(name, less, expected_top):
    global fails
    got = less.rows()[:len(expected_top)]
    if got != expected_top:
        fails += 1
        print('FAIL', name); print('  expected', expected_top); print('  got     ', got)
    else:
        print('ok  ', name)

# --- outline file: 3 sections x 3 subsections x 3 items x 4 body lines
lines = []
for a in range(1, 4):
    lines.append('Section%d' % a)
    for b in range(1, 4):
        lines.append('\tSub%d.%d' % (a, b))
        for c in range(1, 4):
            lines.append('\t\tItem%d.%d.%d' % (a, b, c))
            for d in range(1, 5):
                lines.append('\t\t\tbody %d.%d.%d.%d' % (a, b, c, d))
outline = os.path.join(TMP, 'outline.txt')
open(outline, 'w').write('\n'.join(lines) + '\n')
OPTS = r"--sticky-header='^[^\t]' --sticky-header='^\t[^\t]' --sticky-header='^\t\t[^\t]'"

l = Less(OPTS + ' ' + outline)
check('start: nothing pinned yet', l, ['Section1', 'Sub1.1', 'Item1.1.1', 'body 1.1.1.1'])
l.key('Space')
check('page 1: three levels, missing none', l, ['Section1', 'Sub1.1', 'Item1.1.2', 'body 1.1.2.4'])
l.key('Space', 'Space')
check('page 3: headers follow the scope', l, ['Section1', 'Sub1.2', 'Item1.2.2', 'body 1.2.2.4'])
l.key('b')
check('backward page keeps the headers right', l, ['Section1', 'Sub1.2', 'Item1.2.1', 'body 1.2.1.2'][:3])
l.key('g'); l.text('/body 2.2.3.2'); l.key('Enter')
check('search target lands below the headers', l, ['Section2', 'Sub2.2', 'Item2.2.3', 'body 2.2.3.2'])
l.key('G')
check('G shows the end with headers', l, ['Section3', 'Sub3.3', 'Item3.3.2', 'body 3.3.2.2'])
l.key('g', 'Space')
l.text('--sticky-header'); l.key('Enter'); l.text('-'); l.key('Enter')
if l.rows()[0].startswith('Section1') and l.rows()[1].startswith('Sub1.1'):
    fails += 1; print('FAIL clearing levels at runtime')
else:
    print('ok   clearing levels at runtime')
l.close()

# --- levels without an enclosing header take no row
l = Less(OPTS + ' ' + outline)
l.text('/Section2'); l.key('Enter'); l.key('j', 'j', 'j', 'j', 'j', 'j')
rows = l.rows()
if rows[0] != 'Section2':
    fails += 1; print('FAIL collapse: first row', rows[:4])
else:
    print('ok   level 1 header on first row')
l.close()

# --- performance: 11 MB file, no header matches (scan reaches the start)
big = os.path.join(TMP, 'big.txt')
with open(big, 'w') as f:
    for i in range(500000):
        f.write('line number %d of a big file\n' % i)
l = Less("--sticky-header='^NOMATCH' " + big)
t0 = time.time(); l.key('G'); l.key('b')
dt = time.time() - t0
print(('ok   ' if dt < 5 else 'FAIL ') + 'worst-case jump took %.2fs' % dt)
fails += dt >= 5
l.close()

print('%d failure(s)' % fails)
sys.exit(1 if fails else 0)
