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

## 4. Engine B: indentation (stage 1)

A header encloses a line if it is **indented strictly less** than that line.
No indent unit is needed, so 2/4/8 spaces, tabs and mixed styles all work.

Options:

| option | meaning |
|---|---|
| `--sticky-indent=REGEX` | enable the engine; lines matching REGEX are header candidates (`.` = any line); `-` disables it |
| `--sticky-skip=REGEX` | lines that are transparent: never headers, never define the reference indent (comments etc.); blank lines are always transparent |

Rules:

- **Indent width**: leading blanks; a tab advances to the next tab stop
  (`-x`).  Leading ANSI SGR sequences are ignored with `-R`.
- **Reference indent** `d` of a line: its own indent if it is not
  transparent, else the indent of the next non-transparent line (bounded
  look-ahead, 256 lines; none found means `d = 0`, i.e. no headers).
- **Ancestors**: candidates before the line with `indent < d`, nearest first,
  each one lowering the bound to its own indent.  A candidate line that is
  itself on the first row is not its own ancestor.
- Multi-line signatures show only their first line.
- `--sticky-indent` takes precedence over `--sticky-header`.
- At most 64 nested headers are tracked; at most half of the screen is used.

Typical (the repo's presets would hold these, see section 6):

    --sticky-indent=^\s*(async\s+)?(def|class|if|elif|else|for|while|try|except|finally|with|match|case)\b
    --sticky-skip=^\s*(#|$)

Formatted brace languages also work, without counting braces: candidates
`^\s*(if|else|for|while|do|switch)\b|^\S.*\)\s*\{?\s*$` (K&R and Allman, since
a lone `{` is never a candidate).  Weak spots: minified or badly indented
code; the `}` line shows its function but not its own `if`.

## 5. Engine C: balanced delimiters (stage 3)

For unformatted code and keyword-delimited syntax (shell `if`/`fi`, Ruby
`def`/`end`, Lua, SQL `BEGIN`/`END`).

Options:

| option | meaning |
|---|---|
| `--sticky-open=REGEX` | each match opens a scope; the line is that scope's header; enables the engine |
| `--sticky-close=REGEX` | each match closes a scope |
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

Known limits, to be documented: block comments and heredocs spanning lines
are not understood (backward scans cannot know the lexer state); without
`--sticky-root` a scan may reach the start of the file (cost is linear and
cached; capped at 16 MB).

## 6. Presets: options by file name (stage 2)

No language names anywhere: a preset is just a file-name glob mapped to
options.

- **File**: path in `$LESSSTICKYPRESETS` (fallback `~/.lesssticky`, to be
  confirmed).  Read when a file is opened (`edit_ifile`), so `:n`, `:p`, `:e`
  re-evaluate.  Input from a pipe has no name and gets no preset.
- **Format**: a block starts with one or more globs on an unindented line;
  the following indented lines are options, one per line.  The value is
  everything after the first `=` up to the end of the line, so regexes need
  no quoting.  `#` starts a comment line.

      # presets
      *.py
          --sticky-indent=^\s*(async\s+)?(def|class|if|elif|else|for|while|try|except|finally|with)\b
          --sticky-skip=^\s*(#|$)
      *.c *.h *.go
          --sticky-indent=^\s*(if|else|for|while|do|switch)\b|^\S.*\)\s*\{?\s*$
      Makefile *.mk
          --sticky-indent=^\S.*:

- **Matching**: a glob is matched against the base name (against the whole
  path if it contains `/`), `fnmatch` rules.  The **first** matching block
  wins (alternative: apply all matching blocks in order; to be decided).
- **Precedence**: command line (and `LESS`) beats presets.  Every sticky
  setting has a *CLI slot* and a *preset slot*; the effective value is the CLI
  slot if it was set, else the preset slot.  `--sticky-xxx=-` on the command
  line is a CLI setting meaning "none" and so disables the preset's value.
  For `--sticky-header` the list of levels is replaced as a whole.  Options
  given interactively with `--` count as CLI.
- Preset options are validated like normal options; errors name the preset
  file and line.

## 7. Stage 4: levels from capture groups

For Markdown, org-mode and similar: `--sticky-level=EXPR` with
`len(\N)` / `len(\N)+K`, so one `--sticky-header` pattern gives many levels
(`^(#+)\s` with `len(\1)`).

## 8. Stages and status

- [x] A. explicit levels, overlay, paging/jump adjustments, docs, test
- [ ] B. indentation engine (`--sticky-indent`, `--sticky-skip`), docs, tests
- [ ] C. preset file (`LESSSTICKYPRESETS`), CLI-over-preset slots
- [ ] D. balanced-delimiter engine (`--sticky-open/close/match/ignore/root/lead`)
- [ ] E. capture-group levels
- [ ] F. other makefiles, regenerate `less.man`/`less.hlp`, mouse wheel check

## 9. Testing

`lesstest/sticky-header.py` drives `less` in tmux and checks the top rows after
paging, searching, `G`/`g`, runtime option changes, and a worst-case speed
test.  Each stage adds scenarios (Python-like and tab-indented samples for B,
glob/precedence cases for C, brace samples with comments/strings for D).
