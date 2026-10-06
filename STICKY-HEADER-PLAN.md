# Sticky headers for less: design and plan

Branch: `hband-sticky-header` (off `hband-master`, less 668).
Source: `sticky.c`; pattern primitives in `search.c`; hooks in `forwback.c`,
`jump.c`, `optfunc.c`, `command.c`, `opttbl.c`, `edit.c`.
Tests: `lesstest/sticky-header.py` (tmux driven; needs `tmux`, `python3`).

## 1. Goal

While scrolling, keep the lines that *structurally enclose* the first visible
line pinned at the top of the screen, outermost first, like the sticky scroll
of code editors.  A level with no enclosing header takes no row.  No language
knowledge is built in: all structure is described by options, and options can
be bundled per file name by a preset file (section 6).

## 2. Architecture

Every mode feeds the same machinery in `sticky.c`:

1. **Classifier**: looks at one input line and says whether it is a header
   candidate and, if so, at which *depth*.
2. **Header index**: a sorted deque of the candidate lines of one contiguous
   region of the file, grown on demand in both directions.  Scrolling by a few
   lines costs only the lines scrolled over.  Reset when the file or the
   options change.
3. **Stack search** (`stack_for`): from a line, scan the index backward and
   accept an entry only if its depth is strictly shallower than the last
   accepted one; stop at depth 0.  Result: enclosing headers, outermost first.
4. **Overlay** (`overlay_sticky`): after every repaint, draw the stack over the
   top rows; the smallest `k` such that the headers of the line on row `k`
   fit in `k` rows.  Page commands and jumps are adjusted so that no line ends
   up hidden under the overlay.

The engines differ only in how the classifier assigns depth.  Internally the
index stores `depth = level` with the convention "entry encloses a line whose
reference depth is `d` iff `level <= d`" (explicit levels: the level number;
indentation: `indent + 1`).

## 3. Engine A: explicit levels (done)

`--sticky-header=REGEX`, repeated; use *n* is level *n*.  A line belongs to the
deepest level whose pattern matches.  `--sticky-header=-` clears the levels.

## 4. Engine B: indentation (stage 1, done)

A header encloses a line if it is **indented strictly less** than that line.
No indent unit is needed, so 2/4/8 spaces, tabs and mixed styles all work.

Options:

| option | meaning |
|---|---|
| `--sticky-indent=REGEX` | enable the engine; lines matching REGEX are header candidates (`.` = any line); `-` disables it |
| `--sticky-skip=REGEX` | lines that are transparent: never headers, never define the reference indent (comments etc.); blank lines are always transparent |
| `--sticky-close=REGEX` | lines which close scopes (`fi`, `done`, `}`, `end`, ...): see "Closing lines" |

Rules:

- **Indent width**: leading blanks; a tab advances to the next tab stop
  (`-x`).  Leading ANSI SGR sequences are ignored with `-R`.
- **Reference indent** `d` of a line: its own indent if it is not
  transparent, else the indent of the next non-transparent line (bounded
  look-ahead, 256 lines; none found means `d = 0`, i.e. no headers).
- **Ancestors**: candidates before the line with `indent < d`, nearest first,
  each one lowering the bound to its own indent.  A candidate line that is
  itself on the first row is not its own ancestor.
- **Closing lines** (`--sticky-close`): a header encloses a line only if no
  closing line lies between them with indent `<=` the header's.  Indentation
  alone cannot tell that a `fi` ended an `elif` scope: a later line indented
  deeper (continuation line, heredoc body) would still find the `elif` as its
  nearest shallower header.  Closing lines are indexed next to the headers
  (negative level) and lower the enclosure bound during the backward walk.
  Only lines matching the pattern close scopes, so a heredoc terminator or a
  col-0 string line inside a function does not end the function (a rule
  "any dedent closes" would).  Without the option only headers close scopes
  (right for Python, where dedent ends blocks).
- Multi-line signatures show only their first line.
- `--sticky-indent` takes precedence over `--sticky-header`.
- The value of every `--sticky-*` regex option is the whole rest of the argument
  (new `RAW_STRING` option flag), because less normally ends a string option at
  `$`, which regexes need.
- At most 64 nested headers are tracked; at most half of the screen is used.

Typical (the repo's presets would hold these, see section 6):

    --sticky-indent=^\s*(async\s+)?(def|class|if|elif|else|for|while|try|except|finally|with|match|case)\b
    --sticky-skip=^\s*(#|$)

Formatted brace languages also work, without counting braces: candidates
`^\s*(if|else|for|while|do|switch)\b|^\S.*\)\s*\{?\s*$` (K&R and Allman, since
a lone `{` is never a candidate).  Weak spots: minified or badly indented
code; the `}` line shows its function but not its own `if`.

## 5. Engine C: balanced delimiters (stage 3, done)

For unformatted code and keyword-delimited syntax (shell `if`/`fi`, Ruby
`def`/`end`, Lua, SQL `BEGIN`/`END`).

Options:

| option | meaning |
|---|---|
| `--sticky-open=REGEX` | each match opens a scope; the line is that scope's header; enables the engine |
| `--sticky-close=REGEX` | each match closes a scope (the same option as in the indentation engine, section 4, where a matching line ends the scopes of headers indented as much or more) |
| `--sticky-match=REGEX` | only scopes whose opening line matches are shown; others are counted but transparent |
| `--sticky-ignore=REGEX` | text removed from a line before counting (strings, char literals, line comments) |
| `--sticky-root=REGEX` | a line known to be at depth 0 (e.g. `^\}`): bounds the backward scan |
| `--sticky-lead=REGEX` | if the header line matches (e.g. a lone `{`), show the previous non-blank line instead (Allman) |

Algorithm: per line, collect OPEN and CLOSE matches in order of offset
(after `--sticky-ignore`).  Scanning backward from the first visible line with
`need = 0`: right to left, a CLOSE does `need++`; an OPEN does `need--` if
`need > 0`, else it is an unmatched opener, i.e. an enclosing scope whose
header is that line.  Per-line (opens, closes) deltas are cached in the index
so forward scrolling is incremental.

Implemented as follows.  A line is reduced when it is indexed: matched pairs
inside the line cancel, leaving `closes` unmatched closing delimiters followed
by `opens` unmatched opening ones; only those two counts, and the flags
(shown / root / lead), are stored.  The backward walk processes a line's
opens first, then its closes.  Details settled while implementing:

- Precedence of engines: `--sticky-indent`, then `--sticky-open`, then
  `--sticky-header`.  A command-line `--sticky-open` replaces the preset's
  structure as a whole, and then `close/ignore/match/root/lead` come from the
  command line only (a preset's close pattern belongs to the preset's open
  pattern).  With the structure from a preset, each of them can still be
  overridden separately on the command line.
- `--sticky-root`: a root line has no scope open before its own opening
  delimiters (its closing delimiters are applied first), so the walk processes
  that line's unmatched opens and stops.  `^\}` and `^function` both fit.
- `--sticky-match` and `--sticky-lead` are tested against the whole header
  line; `--sticky-ignore` text is blanked before counting.  A line with several
  unmatched openers is pinned once.
- `--sticky-lead` shows the closest non-blank line before the header (at most
  16 lines back); a result equal to the outer header is not repeated.
- Limits: 4096 delimiters per line, 60000 unmatched ones per line.
- Primitives in `search.c`: `sticky_line_convert`, `sticky_pattern_find`.
- Shipped: CSS/SCSS/Less preset (`--sticky-root=^\}`).

Known limits, to be documented: block comments and heredocs spanning lines
are not understood (backward scans cannot know the lexer state); without
`--sticky-root` a scan may reach the start of the file (cost is linear and
cached; capped at 16 MB).

## 6. Presets: options by file name or shebang (stage 2, done)

No language names anywhere: a preset is just a pattern list mapped to
options.  Code: `stickypre.c`.

- **Opt-in**: `--sticky-presets` (off by default; can be toggled while less
  runs).
- **Files** (first matching block over all of them wins):
  `$LESSSTICKYPRESETS` (colon-separated list; if set, only these), else
  `~/.lesssticky`, then the installed `lesssticky` (compiled-in path
  `${datadir}/less/lesssticky`; `make install` installs the repo's `lesssticky`
  there, `make uninstall` removes it).
- **Read** when a file is opened (`edit_ifile`), so `:n`, `:p`, `:e`
  re-evaluate.
- **Format**: a block starts with one or more patterns on an unindented line;
  the following indented lines are `--sticky-*` options, one per line, the
  value being everything after the first `=` (no quoting).  `#` starts a
  comment line.
- **Patterns**: a glob matched against the base name (the whole name if the
  pattern contains `/`).  A pattern starting with `$` (the `$` is not part of
  it) is a glob for the interpreter of the `#!` first line: base name of the
  interpreter path, or if that is `env`, of the first argument of env which
  is not an option or `NAME=value` (`-u`/`-C` and their argument are
  skipped).  Pipes have no name but their first line is still matched.
- **Precedence**: every setting has a command-line slot and a preset slot.
  If `--sticky-header` or `--sticky-indent` is given on the command line (or
  in `LESS`, or interactively), the preset's structure (levels or indent
  pattern) is ignored as a whole; `--sticky-skip` is taken from the command
  line if given there, else from the preset.  `-` as a value is a command-line
  setting that means "none".  Only `--sticky-*` options with a `=VALUE` are
  allowed in a preset (`--sticky-presets` is not).
- **Shipped presets**: `lesssticky`, with blocks for Python, shell, Ruby, Lua,
  Makefile, YAML, JSON, HTML/XML, C-family and JS-family curly-bracket
  languages (by indentation), Markdown, Org, diffs, INI, LaTeX, roff and
  Dockerfile.  POSIX regular expression syntax only.

## 7. Stage 4: levels from capture groups (done)

For Markdown, org-mode and similar: `--sticky-level=EXPR` with
`len(\N)` / `len(\N)+K`, so one `--sticky-header` pattern gives many levels
(`^(#+)\s` with `len(\1)`).

Implemented: `len(\N)`, `len(\N)+K`, `len(\N)-K`, N from 1 to 5 (the
groups the regex libraries report); a level below 1 is not a header, above 64
is clamped.  The first `--sticky-header` pattern which matches decides.  The
expression comes from the command line if `--sticky-header` or `--sticky-level`
was given there, else from the preset.  `lesssticky`: Markdown and Org use it
(no longer limited to 6 and 4 levels).  Primitive: `sticky_pattern_group_len`.

## 8. Stages and status

- [x] A. explicit levels, overlay, paging/jump adjustments, docs, test
- [x] B. indentation engine (`--sticky-indent`, `--sticky-skip`), docs, tests
- [x] C. preset file (`LESSSTICKYPRESETS`), `--sticky-presets`, shebang patterns, CLI-over-preset slots, shipped `lesssticky`, `make install`
- [x] D. balanced-delimiter engine (`--sticky-open/close/match/ignore/root/lead`), CSS/SCSS/Less preset, tests
- [x] E. capture-group levels (`--sticky-level`), Markdown/Org presets
- [ ] F. other makefiles, regenerate `less.man`/`less.hlp`, mouse wheel check

## 9. Testing

`lesstest/sticky-header.py` drives `less` in tmux and checks the top rows after
paging, searching, `G`/`g`, runtime option changes, and a worst-case speed
test.  Each stage adds scenarios (Python-like and tab-indented samples for B,
glob/precedence cases for C, brace samples with comments/strings for D).
