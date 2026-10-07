#!/usr/bin/env python3
# Tests for multiline searching (opt out with --no-multiline-search).
# Drives ./less in a tmux session.
# Usage: lesstest/search-multiline.py [path-to-less]     (needs tmux, python3)
import os, subprocess, sys, tempfile, time

LESS = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), '..', 'less'))
TMP = tempfile.mkdtemp(prefix='multiline-test-')
SESSION = 'multiline-test-%d' % os.getpid()
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

# A multiline match only succeeds through the join fallback (no single
# line contains the whole pattern), so "no 'Pattern not found'" is itself
# proof the join fired; "'Pattern not found' did appear" is proof the
# fallback was not used (e.g. because it's disabled).
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

def check_hilite(name, less, expected):
    global fails
    text = less.raw()
    if expected in text:
        print('ok  ', name)
    else:
        fails += 1
        print('FAIL', name); print('  expected', repr(expected)); print('  got', repr(text))

# --- plain word-wrap, no hyphen: the break and surrounding whitespace
# stand for a single space, so a pattern with a space in it can match
# across the break. ---
wrapf = os.path.join(TMP, 'wrap.txt')
open(wrapf, 'w').write(
    'intro line\nfiller 0\nfiller 1\nfiller 2\n'
    'the quick brown fox\n    jumps over the lazy dog\n'
    + ''.join('tail %d\n' % i for i in range(20)))

l = Less(wrapf)
l.text('/fox jumps'); l.key('Enter')
check_found('forward: plain word-wrap joined by a space is found', l)
check_hilite('forward: hilite covers both sides, no literal space shown', l,
             '\x1b[7mfox\x1b[0m\n\x1b[7m    jumps\x1b[0m')
l.close()

l = Less('--no-multiline-search ' + wrapf)
l.text('/fox jumps'); l.key('Enter')
check_not_found('forward: --no-multiline-search disables the plain join', l)
l.close()

l = Less(wrapf)
l.key('G')
l.text('?fox jumps'); l.key('Enter')
check_found('backward: plain word-wrap joined by a space is found', l)
l.key('k')
check_hilite('backward: hilite covers both sides, no literal space shown', l,
             '\x1b[7mfox\x1b[0m\n\x1b[7m    jumps\x1b[0m')
l.close()

# --- a line ending in a hyphen is ambiguous: it could be a genuine
# hyphenation (drop the hyphen) or two words joined with a literal
# hyphen (keep it).  Both interpretations must be tried. ---
dualf = os.path.join(TMP, 'dual.txt')
open(dualf, 'w').write(
    'intro line\nfiller 0\nfiller 1\nfiller 2\n'
    'lorem-\nipsum dolor\n'
    + ''.join('tail %d\n' % i for i in range(20)))

l = Less(dualf)
l.text('/loremipsum'); l.key('Enter')
check_found('hyphen is ambiguous: dehyphenated form is found', l)
check_hilite('hyphen is ambiguous: dehyphenated hilite keeps the hyphen visible', l,
             '\x1b[7mlorem-\x1b[0m\n\x1b[7mipsum\x1b[0m')
l.close()

l = Less(dualf)
l.text('/lorem-ipsum'); l.key('Enter')
check_found('hyphen is ambiguous: literal-hyphen form is also found', l)
check_hilite('hyphen is ambiguous: literal-hyphen hilite spans both lines', l,
             '\x1b[7mlorem-\x1b[0m\n\x1b[7mipsum\x1b[0m')
l.close()

l = Less('--no-multiline-search --no-hyphen-search ' + dualf)
l.text('/loremipsum'); l.key('Enter')
check_not_found('--no-multiline-search --no-hyphen-search disables both joins', l)
l.close()

# --hyphen-search and --multiline-search are independent toggles.
l = Less('--no-multiline-search ' + dualf)
l.text('/loremipsum'); l.key('Enter')
check_found('--no-multiline-search alone leaves hyphen-search (default on) working', l)
l.close()

l = Less('--no-hyphen-search ' + dualf)
l.text('/loremipsum'); l.key('Enter')
check_not_found('--no-hyphen-search disables dehyphenation even with multiline on', l)
l.close()

l = Less('--no-hyphen-search ' + dualf)
l.text('/lorem- ipsum'); l.key('Enter')
check_found('--no-hyphen-search still joins the break as plain word-wrap (with a space)', l)
l.close()

# --- --hyphen-regexp overrides what counts as a hyphen.  Whitespace
# around the marker is no longer skipped implicitly: the pattern has
# to say so itself (the factory default is "([-‐])\s*", so this
# still works out of the box; a custom pattern that cares about
# trailing whitespace has to include its own "\s*" too, as this one
# does not need to since "~~" always directly abuts the line end here). ---
regexpf = os.path.join(TMP, 'regexp.txt')
open(regexpf, 'w').write(
    'intro line\nfiller 0\nfiller 1\nfiller 2\n'
    'lorem~~\nipsum dolor\n'
    + ''.join('tail %d\n' % i for i in range(20)))

l = Less(r"--hyphen-regexp='~~' " + regexpf)
l.text('/loremipsum'); l.key('Enter')
check_found('--hyphen-regexp: custom marker, dehyphenated form is found', l)
check_hilite('--hyphen-regexp: custom marker hilite keeps it visible', l,
             '\x1b[7mlorem~~\x1b[0m\n\x1b[7mipsum\x1b[0m')
l.close()

l = Less(r"--hyphen-regexp='~~' " + regexpf)
l.text('/lorem~~ipsum'); l.key('Enter')
check_found('--hyphen-regexp: custom marker, literal form is also found', l)
l.close()

l = Less(r"--hyphen-regexp='~~' " + dualf)
l.text('/loremipsum'); l.key('Enter')
check_not_found('--hyphen-regexp: once overridden to "~~", a plain "-" no longer counts', l)
l.close()

# "-" is a literal pattern here, not a sentinel that clears/resets it
# (it's also the single most likely literal value someone would set).
l = Less(r"--hyphen-regexp='-' " + dualf)
l.text('/loremipsum'); l.key('Enter')
check_found('--hyphen-regexp=\'-\' is a literal pattern, not a "clear" sentinel', l)
l.close()

# An empty --hyphen-regexp isn't "off" either: it matches with zero
# width at the end of every line, so direct (no-space) joining is
# tried everywhere, as if every line ended in a hyphen.  It has to be
# passed as its own argument -- "--hyphen-regexp ''" -- since
# "--hyphen-regexp=" with nothing after the "=" instead waits for a
# value and swallows whatever comes next, same as any STRING option.
emptyf = os.path.join(TMP, 'empty.txt')
open(emptyf, 'w').write(
    'intro line\nfiller 0\nfiller 1\nfiller 2\n'
    'foo\nbar baz\n'
    + ''.join('tail %d\n' % i for i in range(20)))

l = Less("--hyphen-regexp '' --no-multiline-search " + emptyf)
l.text('/foobar'); l.key('Enter')
check_found('--hyphen-regexp \'\': every line is treated as ending in a hyphen', l)
check_hilite('--hyphen-regexp \'\': hilite covers both sides directly, no gap', l,
             '\x1b[7mfoo\x1b[0m\n\x1b[7mbar\x1b[0m baz')
l.close()

l = Less('--no-multiline-search ' + emptyf)
l.text('/foobar'); l.key('Enter')
check_not_found('--hyphen-regexp factory default: a plain line ending is not a hyphen', l)
l.close()

# --- --hyphen-regexp: a pattern with its own capture group uses that
# group (not the whole match) as the hyphen, so context can gate it
# without being considered part of the hyphen itself.  (A lookbehind
# like the one in the feature request, e.g. "(?<![aeiou])(-)", needs
# PCRE; this build may only have POSIX ERE, which has no lookaround, so
# "[^aeiou](-)" -- a hyphen right after a consonant -- exercises the
# same capture-group logic in a way that compiles everywhere, and
# doesn't claim to consume the lookbehind either way since there isn't
# one: the consonant is consumed by group 0 but excluded from group 1.)
consonantf = os.path.join(TMP, 'consonant.txt')
open(consonantf, 'w').write(
    'intro line\nfiller 0\nfiller 1\nfiller 2\n'
    'chaptera-\nsection two\n'
    'chapterb-\nsection one\n'
    + ''.join('tail %d\n' % i for i in range(20)))

l = Less(r"--hyphen-regexp='[^aeiou](-)' " + consonantf)
l.text('/chapterasection'); l.key('Enter')
check_not_found('--hyphen-regexp capture group: hyphen after a vowel does not count', l)
l.close()

l = Less(r"--hyphen-regexp='[^aeiou](-)' " + consonantf)
l.text('/chapterbsection'); l.key('Enter')
check_found('--hyphen-regexp capture group: hyphen after a consonant counts', l)
l.close()

# A pattern with no group of its own is still wrapped in one: the
# whole "[0-9]-" is taken as the hyphen (has_capture_group() asks the
# regex library itself, via pattern_group_count(), not a text guess,
# so it correctly sees no group here and wraps it).
nogroupf = os.path.join(TMP, 'nogroup.txt')
open(nogroupf, 'w').write(
    'intro line\nfiller 0\nfiller 1\nfiller 2\nchapter2-\nsection one\n'
    + ''.join('tail %d\n' % i for i in range(20)))

l = Less(r"--hyphen-regexp='[0-9]-' " + nogroupf)
l.text('/chaptersection'); l.key('Enter')
check_found('--hyphen-regexp without its own group: whole pattern is still the hyphen', l)
l.close()

# --- --linestart-hyphen-regexp: a hyphen repeated at the start of the
# continuation line resolves the usual hyphen ambiguity instead of
# guessing both ways.  Off (well, "unmarked": see below) by default. ---
suppf = os.path.join(TMP, 'linestart.txt')
open(suppf, 'w').write(
    'intro line\nfiller 0\nfiller 1\nfiller 2\n'
    'lorem-\n-ipsum dolor\n'
    + ''.join('tail %d\n' % i for i in range(20)))

# Point 6's duplicated-hyphen example: allow (but don't require, and
# don't capture) leading whitespace ahead of the repeated hyphen.
l = Less(r"--linestart-hyphen-regexp='\s*([-‐])' " + suppf)
l.text('/lorem-ipsum'); l.key('Enter')
check_found('--linestart-hyphen-regexp: repeated hyphen resolves to one hyphen', l)
check_hilite('--linestart-hyphen-regexp: hilite covers both markers', l,
             '\x1b[7mlorem-\x1b[0m\n\x1b[7m-ipsum\x1b[0m')
l.close()

l = Less(r"--linestart-hyphen-regexp='\s*([-‐])' " + suppf)
l.text('/loremipsum'); l.key('Enter')
check_not_found('--linestart-hyphen-regexp: dehyphenated form no longer matches', l)
l.close()

l = Less(r"--linestart-hyphen-regexp='\s*([-‐])' " + suppf)
l.text('/lorem--ipsum'); l.key('Enter')
check_not_found('--linestart-hyphen-regexp: double hyphen does not match either', l)
l.close()

# off (unmarked) by default: the usual ambiguous guessing applies
# instead, and "-ipsum" keeps its own leading hyphen (it's just part
# of the continuation text now), so only the single-hyphen form --
# dehyphenating side_a but not side_b -- is still found.
l = Less(suppf)
l.text('/lorem-ipsum'); l.key('Enter')
check_found('--linestart-hyphen-regexp unmarked by default: plain dehyphenation still works', l)
l.close()

# An explicitly empty --linestart-hyphen-regexp (passed as its own
# argument -- see the --hyphen-regexp note above) behaves exactly like
# the unset default, since both compile to a pattern that can only
# ever match with zero width, which is never treated as a genuine
# marker: lines are "unmarked", not switched on, by an empty pattern.
l = Less("--linestart-hyphen-regexp '' " + suppf)
l.text('/lorem-ipsum'); l.key('Enter')
check_found("--linestart-hyphen-regexp '': same as the unmarked default", l)
l.close()

# --- word split by HYPHEN-MINUS ('-'), as a special case of the above
# where dropping the hyphen is the only interpretation that matches ---
asciif = os.path.join(TMP, 'ascii.txt')
open(asciif, 'w').write(
    'intro line\nfiller 0\nfiller 1\nfiller 2\n'
    'this is a demonstra-\n    tion of something\n'
    + ''.join('tail %d\n' % i for i in range(20)))

l = Less(r"--hyphen-regexp='~~' --no-multiline-search " + asciif)
l.text('/demonstration'); l.key('Enter')
check_not_found("--hyphen-regexp: plain '-' no longer counts as a hyphen once overridden", l)
l.close()

l = Less(asciif)
l.text('/demonstration'); l.key('Enter')
check_found('forward: word split by hyphen-minus is found', l)
check_hilite('forward: hilite spans hyphen and whitespace on both lines', l,
             '\x1b[7mdemonstra-\x1b[0m\n\x1b[7m    tion\x1b[0m')
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
