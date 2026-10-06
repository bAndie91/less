#!/usr/bin/env python3
# Tests for --sticky-header.  Drives ./less in a tmux session.
# Usage: lesstest/sticky-header.py [path-to-less]     (needs tmux, python3)
import os, subprocess, sys, tempfile, time

LESS = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), '..', 'less'))
TMP = tempfile.mkdtemp(prefix='sticky-test-')
SESSION = 'sticky-test-%d' % os.getpid()
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
    def __init__(self, args, h=12, w=60, env=''):
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
OPTS = r"--sticky-header='^[^[:space:]]' --sticky-header='^[[:space:]][^[:space:]]' --sticky-header='^[[:space:]][[:space:]][^[:space:]]'"

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

# --- indentation engine
PYSRC = """import os


class Config:
    def __init__(self, path):
        self.path = path

        # comment between statements
        if path:
            for line in open(path):
                if line.startswith("#"):
                    continue
                self.parse(line)

            self.loaded = True
        else:
            self.loaded = False

    def parse(self, line):
        key, value = line.split("=", 1)
        while value:
            value = value[1:]
            if not value:
                break
        return key


def main():
    cfg = Config(1)
    x = 1
    print(cfg)


main()
"""
pyfile = os.path.join(TMP, 'sample.py')
open(pyfile, 'w').write(PYSRC)
PYOPTS = r"--sticky-indent='^\s*(async\s+)?(def|class|if|elif|else|for|while|try|except|finally|with)\b' --sticky-skip='^\s*(#|$)'"

def find(l, pat):
    l.key('g'); l.text('/' + pat); l.key('Enter')

l = Less(PYOPTS + ' ' + pyfile, h=14, w=70)
find(l, 'continue')
check('indent: five nested headers', l, ['class Config:', 'def __init__(self, path):', 'if path:',
      'for line in open(path):', 'if line.startswith("#"):', 'continue'])
find(l, 'key, value = ')
check('indent: sibling scope closed', l, ['class Config:', 'def parse(self, line):', 'key, value = line.split("=", 1)'])
find(l, 'return key')
check('indent: loop scope ends at dedent', l, ['class Config:', 'def parse(self, line):', 'return key'])
find(l, 'x = 1')
check('indent: top level function only', l, ['def main():', 'x = 1'])
find(l, 'self.loaded = False')
check('indent: else branch is a header', l, ['class Config:', 'def __init__(self, path):', 'else:', 'self.loaded = False'])
l.close()

# tab width follows -x
tw = os.path.join(TMP, 'tabwidth.txt')
open(tw, 'w').write('x\n    y\n\tw\n' + '\t\tfill\n' * 40)
l = Less("--sticky-indent=. " + tw, h=10, w=40)
find(l, 'w')
check('indent: tab is 8 columns by default', l, ['x', 'y', 'w'])
l.close()
l = Less("-x4 --sticky-indent=. " + tw, h=10, w=40)
find(l, 'w')
check('indent: -x4 makes a tab as wide as 4 spaces', l, ['x', 'w'])
l.close()

# --- presets
PRESETS = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'lesssticky'))
ENV = 'LESSSTICKYPRESETS=' + PRESETS
FIVE = ['class Config:', 'def __init__(self, path):', 'if path:',
        'for line in open(path):', 'if line.startswith("#"):', 'continue']

def check_not(name, less, row0):
    global fails
    got = less.rows()[0]
    if got == row0:
        fails += 1; print('FAIL', name, '- row 0 is', repr(got))
    else:
        print('ok  ', name)

l = Less(pyfile, h=14, w=70, env=ENV)
find(l, 'continue')
check_not('presets are off by default', l, 'class Config:')
l.text('--sticky-presets'); l.key('Enter', 'Enter')
find(l, 'continue')
check('presets can be switched on while running', l, FIVE)
l.close()

l = Less('--sticky-presets ' + pyfile, h=14, w=70, env=ENV)
find(l, 'continue')
check('preset by file name', l, FIVE)
l.close()

l = Less('--sticky-presets --sticky-indent=- ' + pyfile, h=14, w=70, env=ENV)
find(l, 'continue')
check_not('command line "-" switches the preset off', l, 'class Config:')
l.close()

l = Less('--sticky-presets --sticky-header=^class ' + pyfile, h=14, w=70, env=ENV)
find(l, 'continue')
check('command line structure replaces the preset one', l, ['class Config:', 'continue'])
l.close()

shsrc = '#!/usr/bin/env /bin/bash -e\nf() {\n    if [ "$1" ]; then\n        for i in 1 2; do\n' + ''.join('            echo %d\n' % i for i in range(40)) + '        done\n    fi\n}\n'
shfile = os.path.join(TMP, 'myscript')
open(shfile, 'w').write(shsrc)
l = Less('--sticky-presets ' + shfile, h=14, w=70, env=ENV)
find(l, 'echo 29')
check('shebang via env (first argument is the interpreter)', l, ['f() {', 'if [ "$1" ]; then', 'for i in 1 2; do', 'echo 29'])
l.close()

pyscript = os.path.join(TMP, 'mytool')
open(pyscript, 'w').write('#!/usr/bin/python3\n' + PYSRC)
l = Less('--sticky-presets ' + pyscript, h=14, w=70, env=ENV)
find(l, 'continue')
check('shebang with a plain interpreter path', l, FIVE)
l.close()

mine = os.path.join(TMP, 'my-presets')
open(mine, 'w').write('# first matching block wins\n*.txt $zsh\n    --sticky-indent=.\n*.txt\n    --sticky-indent=^NEVER\n')
nest = os.path.join(TMP, 'nest.txt')
open(nest, 'w').write('a\n\tb\n\t\tc\n' + '\t\t\tfill\n' * 40)
l = Less('--sticky-presets ' + nest, h=10, w=40, env='LESSSTICKYPRESETS=%s/no-such-file:%s' % (TMP, mine))
find(l, 'fill')
check('first matching block wins; missing files in the list are skipped', l, ['a', 'b', 'c', 'fill'])
l.close()

# --- closing lines (--sticky-close)
FISH = """#!/bin/bash
f() {
    local n=1
    if [ -z "$1" ]; then
        echo none
    elif [ "$1" = a ]; then
        echo a
    elif [ "$1" = b ]; then
        echo b
    fi
    cmd arg1 \\
        arg2 \\
        arg3
    cat <<EOT
        heredoc text
EOT
    echo after1
    echo after2
}
"""
fish = os.path.join(TMP, 'fish.sh')
open(fish, 'w').write(FISH)
l = Less('--sticky-presets ' + fish, h=10, w=50, env=ENV)
find(l, 'arg3')
check('close: a deeper line after fi is not under the elif', l, ['f() {', 'arg3'])
find(l, 'echo after2')
check('close: heredoc terminator at column 0 does not end the function', l, ['f() {', 'echo after2'])
l.close()

SHOPTS = r"--sticky-indent='^[[:space:]]*(f\(\)|if|elif|else)' --sticky-skip='^[[:space:]]*(#|$)'"
l = Less(SHOPTS + ' ' + fish, h=10, w=50)
find(l, 'arg3')
check('close: without --sticky-close the elif is still the header', l, ['f() {', 'elif [ "$1" = b ]; then', 'arg3'])
l.close()
l = Less(SHOPTS + r" --sticky-close='^[[:space:]]*fi' " + fish, h=10, w=50)
find(l, 'arg3')
check('close: --sticky-close ends the scope of the elif', l, ['f() {', 'arg3'])
l.close()

# --- balanced delimiters (--sticky-open and friends)
BALSRC = """int f(int a)
{
    if (a) { g("}"); }   // } in a comment
    /* { comment */
    for (;;) {
        if (a) {
            x1
            x2
            x3
            x4
            x5
            x6
            x7
        }
        y1
        y2
        y3
        y4
        y5
        y6
        y7
    }
    z1
    z2
    z3
    z4
    z5
}
int h(void)
{
    w1
    w2
    w3
    w4
}
tail1
tail2
""" + 'pad\n' * 20
balfile = os.path.join(TMP, 'bal.c')
open(balfile, 'w').write(BALSRC)
balpre = os.path.join(TMP, 'bal-presets')
open(balpre, 'w').write(r"""*.c
    --sticky-open=\{
    --sticky-close=\}
    --sticky-ignore="([^"\\]|\\.)*"|//.*$|/\*.*\*/
    --sticky-root=^\}
    --sticky-lead=^[[:space:]]*\{[[:space:]]*$
""")
BENV = 'LESSSTICKYPRESETS=' + balpre
l = Less('--sticky-presets ' + balfile, h=10, w=50, env=BENV)
find(l, 'x4')
check('balanced: nested scopes, lone { shows the line before', l, ['int f(int a)', 'for (;;) {', 'if (a) {', 'x4'])
find(l, 'y4')
check('balanced: closed scope is dropped; braces in strings and comments ignored', l, ['int f(int a)', 'for (;;) {', 'y4'])
find(l, 'z3')
check('balanced: only the function remains', l, ['int f(int a)', 'z3'])
find(l, 'w3')
check('balanced: next function, after the root line', l, ['int h(void)', 'w3'])
find(l, 'tail2')
check('balanced: nothing enclosing at top level', l, ['tail2'])
l.close()

# the same without --sticky-ignore: the "{" in the comment is counted
l = Less(r"--sticky-open='\{' --sticky-close='\}' " + balfile, h=10, w=50)
find(l, 'x4')
check('balanced: without ignore, a brace in a comment opens a bogus scope', l, ['/* { comment */', 'for (;;) {', 'if (a) {'])
l.close()

l = Less(r"--sticky-open='\{' --sticky-close='\}' --sticky-match='^[[:space:]]*(for|if)' " + balfile, h=10, w=50)
find(l, 'x4')
check('balanced: --sticky-match shows only matching scopes', l, ['for (;;) {', 'if (a) {', 'x4'])
l.close()

SHBAL = """f() {
if a; then
for x in 1 2; do
echo 1
echo 2
echo 3
echo 4
done
echo 5
echo 6
fi
echo 7
echo 8
}
""" + 'pad\n' * 20
shbal = os.path.join(TMP, 'bal.txt')
open(shbal, 'w').write(SHBAL)
SHB = (r"--sticky-open='(^|[[:space:]])(if|for|while|case)[[:space:]]|\{[[:space:]]*$' "
       r"--sticky-close='(^|[[:space:];])(fi|done|esac)([[:space:];]|$)|^[[:space:]]*\}' ")
l = Less(SHB + shbal, h=10, w=50)
find(l, 'echo 3')
check('balanced: keyword delimiters, no indentation', l, ['f() {', 'if a; then', 'for x in 1 2; do', 'echo 3'])
find(l, 'echo 6')
check('balanced: done closes the for', l, ['f() {', 'if a; then', 'echo 6'])
find(l, 'echo 8')
check('balanced: fi closes the if', l, ['f() {', 'echo 8'])
l.close()

# shipped CSS preset
CSS = """/* } not a brace */
@media (min-width: 600px) {
  .a, .b {
    color: red;
    content: "}";
    margin: 0;
    padding: 0;
    border: 0;
    top: 0;
    left: 0;
    right: 0;
  }
  .c { color: blue; }
  .d {
    color: green;
    x1: 1;
    x2: 2;
    x3: 3;
    x4: 4;
    x5: 5;
  }
}
.e {
  color: black;
  y1: 1;
  y2: 2;
  y3: 3;
  y4: 4;
}
""" + 'pad: 0;\n' * 20
cssf = os.path.join(TMP, 'style.css')
open(cssf, 'w').write(CSS)
l = Less('--sticky-presets ' + cssf, h=10, w=50, env=ENV)
find(l, 'margin')
check('css preset: nested rule inside @media', l, ['@media (min-width: 600px) {', '.a, .b {', 'margin: 0;'])
find(l, 'x3')
check('css preset: sibling rule after a one-line rule', l, ['@media (min-width: 600px) {', '.d {', 'x3: 3;'])
find(l, 'y3')
check('css preset: top level rule after the @media block', l, ['.e {', 'y3: 3;'])
l.close()

# --- levels from a capture group (--sticky-level)
MD = ("# One\n\n## Two\n\ntext\n\n### Three\n\n" + "body\n" * 3 + "\n## Four\n\n#### Five\n"
      + "deep\n" * 12 + "\n# Six\n" + "last\n" * 20)
mdf = os.path.join(TMP, 'doc.md')
open(mdf, 'w').write(MD)
l = Less('--sticky-presets ' + mdf, h=10, w=50, env=ENV)
find(l, 'deep')
check('level: markdown headings by number of #, a skipped level takes no row', l, ['# One', '## Four', '#### Five', 'deep'])
find(l, 'last')
check('level: new top level heading closes the previous ones', l, ['# Six', 'last'])
l.close()

eqf = os.path.join(TMP, 'eq.txt')
open(eqf, 'w').write('= A\n== B\n=== C\n' + 'text\n' * 20 + '== D\n' + 'more\n' * 20)
l = Less(r"--sticky-header='^(=+)[[:space:]]' --sticky-level='len(\1)-1' " + eqf, h=10, w=50)
find(l, 'text')
check('level: len(\\1)-1 shifts the levels (level 0 is not a header)', l, ['== B', '=== C', 'text'])
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

# balanced engine on the same file, no root: the search goes back to the start
l = Less(r"--sticky-open='\{' --sticky-close='\}' " + big)
t0 = time.time(); l.key('G'); l.key('b')
dt = time.time() - t0
print(('ok   ' if dt < 8 else 'FAIL ') + 'balanced worst-case jump took %.2fs' % dt)
fails += dt >= 8
l.close()

tmux('kill-session', '-t', KEEPALIVE)
print('%d failure(s)' % fails)
if fails:
    print('less stderr and exit codes: %s/stderr.log' % TMP)
sys.exit(1 if fails else 0)
