#!/usr/bin/env python3
# Tests for hyphen-aware searching (opt out with --no-hyphen-search).
# Drives ./less in a tmux session.
# Usage: lesstest/search-hyphenation.py [path-to-less]     (needs tmux, python3)
import os, subprocess, sys, tempfile, time

LESS = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), '..', 'less'))
TMP = tempfile.mkdtemp(prefix='hyphen-test-')
SESSION = 'hyphen-test-%d' % os.getpid()
fails = 0
KEEPALIVE = SESSION + '-keep'

def start_server():
    # Killing the last tmux session stops the tmux server, and starting
    # the next one races with that.  Keep a dummy session alive.
    subprocess.run(['tmux', 'new-session', '-d', '-s', KEEPALIVE, 'sleep 100000'], capture_output=True)

def tmux(*a):
    return subprocess.run(('tmux',) + a, capture_output=True, text=True).stdout

start_server()

class Less:
    def __init__(self, args, h=10, w=50, env=''):
        tmux('new-session', '-d', '-x', str(w), '-y', str(h), '-s', SESSION,
             'env LESS= TERM=xterm %s %s %s 2>>%s/stderr.log; echo "exit $?" >>%s/stderr.log' % (env, LESS, args, TMP, TMP))
        self.settle()
    def key(self, *keys):
        for k in keys:
            tmux('send-keys', '-t', SESSION, k)
        self.settle()
    def text(self, s):
        tmux('send-keys', '-t', SESSION, '-l', '--', s)
        self.settle()
    def settle(self):
        # Wait until the screen stops changing.
        time.sleep(0.15)
        last = None
        for _ in range(60):
            cur = tmux('capture-pane', '-p', '-t', SESSION)
            if cur == last and cur.strip() != '':
                break
            last = cur
            time.sleep(0.08)
    def rows(self):
        return [r.rstrip().replace('\t', ' ').strip() for r in tmux('capture-pane', '-p', '-t', SESSION).split('\n')]
    def raw(self):
        # Pane contents with SGR escapes kept in, to check highlight spans.
        return tmux('capture-pane', '-e', '-p', '-t', SESSION)
    def close(self):
        tmux('kill-session', '-t', SESSION)

# A hyphen-joined match only succeeds through the hyphenation fallback (no
# single line contains the whole pattern), so "no 'Pattern not found'" is
# itself proof the join fired; "'Pattern not found' did appear" is proof
# the fallback was not used (e.g. because it's disabled).
def check_found(name, less):
    global fails
    rows = less.rows()
    if not any('Pattern not found' in r for r in rows):
        print('ok  ', name)
    else:
        fails += 1
        print('FAIL', name); print('  rows', rows)

def check_not_found(name, less):
    global fails
    rows = less.rows()
    if any('Pattern not found' in r for r in rows):
        print('ok  ', name)
    else:
        fails += 1
        print('FAIL', name, '(expected "Pattern not found")'); print('  rows', rows)

# The whole hit should be highlighted on both sides of the break, including
# the hyphen and the whitespace around it: "demonstra-" fully reversed on
# one line, "    tion" (leading whitespace included) fully reversed on the
# next.
def check_hilite(name, less, expected):
    global fails
    text = less.raw()
    if expected in text:
        print('ok  ', name)
    else:
        fails += 1
        print('FAIL', name); print('  expected', repr(expected)); print('  got', repr(text))

# --- word split by HYPHEN-MINUS ('-') across a line break ---
lines = (['intro line'] + ['filler %d' % i for i in range(3)] +
         ['this is a demonstra-', '    tion of something'] +
         ['tail %d' % i for i in range(20)])
asciif = os.path.join(TMP, 'ascii.txt')
open(asciif, 'w').write('\n'.join(lines) + '\n')

l = Less(asciif)
l.text('/demonstration'); l.key('Enter')
check_found('forward: word split by hyphen-minus is found', l)
check_hilite('forward: hilite spans hyphen and whitespace on both lines', l,
             '\x1b[7mdemonstra-\x1b[0m\n\x1b[7m    tion\x1b[0m')
l.close()

l = Less('--no-hyphen-search ' + asciif)
l.text('/demonstration'); l.key('Enter')
check_not_found('forward: --no-hyphen-search disables the join', l)
l.close()

l = Less(asciif)
l.key('G')
l.text('?demonstration'); l.key('Enter')
check_found('backward: word split by hyphen-minus is found', l)
l.key('k')  # scroll the hyphen side back into view, if it isn't already
check_hilite('backward: hilite spans hyphen and whitespace on both lines', l,
             '\x1b[7mdemonstra-\x1b[0m\n\x1b[7m    tion\x1b[0m')
l.close()

# --- a plain match earlier on the line, and a hyphen-joined match
# later on the same line/break (regression: the earlier plain match
# used to make hilite_line() skip the later hyphen-joined one, since
# only the first search_range() match_pattern call tried the join) ---
dupf = os.path.join(TMP, 'dup.txt')
open(dupf, 'w').write(
    'intro\nESC stands for the\n'
    'ESCAPE key; for example ESC-v means the  two  character  sequence  "ES-\n'
    'CAPE", then "v".\nmore filler\n')

l = Less(dupf, w=90)
l.text('/ESCAPE'); l.key('Enter')
check_found('plain + hyphen-joined match on one line: found', l)
check_hilite('plain + hyphen-joined match on one line: both are highlighted', l,
             '\x1b[7mESCAPE\x1b[0m key; for example ESC-v means the  two  character  sequence  "'
             '\x1b[7mES-\x1b[0m\n\x1b[7mCAPE\x1b[0m')
l.close()

# --- word split by U+2010 HYPHEN across a line break ---
ulines = (['intro line'] + ['filler %d' % i for i in range(3)] +
          ['this uses a hy‐', '    phen break here'] +
          ['tail %d' % i for i in range(20)])
unicodef = os.path.join(TMP, 'unicode.txt')
open(unicodef, 'w', encoding='utf-8').write('\n'.join(ulines) + '\n')

l = Less(unicodef, env='LESSCHARSET=utf-8')
l.text('/hyphen'); l.key('Enter')
check_found('forward: word split by U+2010 HYPHEN is found', l)
check_hilite('forward: hilite spans U+2010 HYPHEN and whitespace', l,
             '\x1b[7mhy‐\x1b[0m\n\x1b[7m    phen\x1b[0m')
l.close()

l = Less(unicodef, env='LESSCHARSET=utf-8')
l.key('G')
l.text('?hyphen'); l.key('Enter')
check_found('backward: word split by U+2010 HYPHEN is found', l)
l.key('k')
check_hilite('backward: hilite spans U+2010 HYPHEN and whitespace', l,
             '\x1b[7mhy‐\x1b[0m\n\x1b[7m    phen\x1b[0m')
l.close()

tmux('kill-session', '-t', KEEPALIVE)
print('%d failure(s)' % fails)
if fails:
    print('less stderr and exit codes: %s/stderr.log' % TMP)
sys.exit(1 if fails else 0)
