/*
 * Sticky headers.
 *
 * The user gives one regular expression per nesting level
 * (--sticky-header=PATTERN, repeated; the first is level 1).
 * A line belongs to the deepest level whose pattern matches it.
 * The headers enclosing the first visible line are kept pinned at the
 * top of the screen: level 1 on the first screen line, level 2 on the
 * second, and so on.  A level with no enclosing header takes no row.
 *
 * Enclosing headers of a line L are found by scanning backward from L:
 * a header is accepted only if its level is strictly shallower than the
 * last accepted one; the scan ends when a level-1 header is accepted.
 *
 * With --sticky-indent the nesting is not numbered by the user: a header
 * encloses a line if it is indented strictly less than that line, and
 * only lines matching the given pattern are header candidates.
 *
 * Classified header lines are kept in a sorted index covering one
 * contiguous region of the file, grown on demand in both directions,
 * so scrolling by a few lines costs only the lines scrolled over.
 */

#include "less.h"
#include "option.h"
#include "position.h"

extern int sc_width;
extern int sc_height;
extern int header_lines;
extern int sigs;
extern int ctldisp;
extern int tabstops[];
extern int ntabstops;
extern int tabdefault;

#define MAX_STICKY_LEVELS 16
/* Most headers shown at once (nesting depth in the indentation engine). */
#define STICKY_STACK_MAX 64
/* How far to look ahead for a line which defines the indent of a blank one. */
#define STICKY_INDENT_LOOKAHEAD 256
#define STICKY_INDENT_MAX 100000
/* Don't look for enclosing headers further back than this many bytes. */
#define STICKY_SCAN_LIMIT (16*1024*1024)
/* A query this far from the indexed region starts a new region. */
#define STICKY_REGION_GAP (256*1024)

struct sticky_level
{
	char *text;
	void *pat;
};

struct sticky_hdr
{
	POSITION pos;
	int level;
};

static struct sticky_level levels[MAX_STICKY_LEVELS];
static int n_levels = 0;
static lbool compile_failed = FALSE;

/* Indentation engine (--sticky-indent, --sticky-skip). */
static char *indent_text = NULL;
static void *indent_pat = NULL;
static char *skip_text = NULL;
static void *skip_pat = NULL;

/*
 * Index of header lines: a deque, sorted by position.
 * Valid entries are hbuf[hstart] ... hbuf[hend-1].
 */
static struct sticky_hdr *hbuf = NULL;
static size_t hstart = 0;
static size_t hend = 0;
static size_t hcap = 0;
/* Lines whose start position is in [reg_lo, reg_hi) are classified. */
static POSITION reg_lo = NULL_POSITION;
static POSITION reg_hi = NULL_POSITION;
static lbool reg_bof = FALSE;
static POSITION scan_floor = 0;

/* Last answer of line_start(): line_start(cache_in) == cache_out. */
static POSITION cache_in = NULL_POSITION;
static POSITION cache_out = NULL_POSITION;

/* Number of screen rows painted by the last overlay. */
static int last_rows = 0;

public int sticky_jump_adjust = 1;

#define HCOUNT()   (hend - hstart)
#define HDR(i)     (hbuf[hstart + (i)])

/*
 * Is a sticky header configured (and usable)?
 */
public lbool sticky_active(void)
{
	return ((n_levels > 0 || indent_text != NULL) && !compile_failed && header_lines == 0);
}

/*
 * Forget everything we know about the file.
 */
public void sticky_reset(void)
{
	hstart = hend = hcap / 2;
	cache_in = cache_out = NULL_POSITION;
	reg_lo = reg_hi = NULL_POSITION;
	reg_bof = FALSE;
}

/*
 * Settings come from two places: the command line (including LESS, and
 * options given while less is running), and the preset chosen for the
 * current file.  A preset never overrides the command line:
 *  - if --sticky-header or --sticky-indent was given on the command line,
 *    the preset's structure (levels or indent pattern) is not used at all;
 *  - --sticky-skip is taken from the command line if given there.
 * "-" on the command line is a setting too: it means "none".
 */
struct sticky_settings
{
	char *level_text[MAX_STICKY_LEVELS];
	int n_levels;
	lbool have_levels;
	char *indent_text;
	lbool have_indent;
	char *skip_text;
	lbool have_skip;
};
static struct sticky_settings cli_settings;
static struct sticky_settings preset_settings;

/* While a preset is being read, option handlers fill preset_settings. */
public lbool sticky_loading_preset = FALSE;

static void clear_levels(struct sticky_settings *st)
{
	int i;
	for (i = 0;  i < st->n_levels;  i++)
	{
		free(st->level_text[i]);
		st->level_text[i] = NULL;
	}
	st->n_levels = 0;
}

static void clear_settings(struct sticky_settings *st)
{
	clear_levels(st);
	free(st->indent_text);
	free(st->skip_text);
	st->indent_text = st->skip_text = NULL;
	st->have_levels = st->have_indent = st->have_skip = FALSE;
}

public void sticky_preset_clear(void)
{
	clear_settings(&preset_settings);
}

/*
 * Drop the effective settings and the compiled patterns.
 */
static void free_effective(void)
{
	int i;
	for (i = 0;  i < n_levels;  i++)
	{
		if (levels[i].pat != NULL)
			sticky_pattern_free(levels[i].pat);
		free(levels[i].text);
		levels[i].pat = NULL;
		levels[i].text = NULL;
	}
	n_levels = 0;
	if (indent_pat != NULL)
		sticky_pattern_free(indent_pat);
	if (skip_pat != NULL)
		sticky_pattern_free(skip_pat);
	indent_pat = skip_pat = NULL;
	free(indent_text);
	free(skip_text);
	indent_text = skip_text = NULL;
}

/*
 * Work out the effective settings from the command line and the preset.
 */
public void sticky_settings_changed(void)
{
	struct sticky_settings *st;
	struct sticky_settings *sk;
	int i;

	free_effective();
	st = (cli_settings.have_levels || cli_settings.have_indent) ? &cli_settings : &preset_settings;
	sk = cli_settings.have_skip ? &cli_settings : &preset_settings;
	for (i = 0;  i < st->n_levels;  i++)
		levels[i].text = save(st->level_text[i]);
	n_levels = st->n_levels;
	if (st->indent_text != NULL)
		indent_text = save(st->indent_text);
	if (sk->skip_text != NULL)
		skip_text = save(sk->skip_text);
	compile_failed = FALSE;
	sticky_reset();
	last_rows = 0;
}

/*
 * Compile patterns, which are set before the options like -i are final.
 */
static void compile_levels(void)
{
	int i;
	if (indent_text != NULL)
	{
		/* The indentation engine takes precedence over explicit levels. */
		if (indent_pat == NULL)
			indent_pat = sticky_pattern_new(indent_text);
		if (indent_pat == NULL)
		{
			compile_failed = TRUE;
			return;
		}
		if (skip_text != NULL && skip_pat == NULL)
		{
			skip_pat = sticky_pattern_new(skip_text);
			if (skip_pat == NULL)
				compile_failed = TRUE;
		}
		return;
	}
	for (i = 0;  i < n_levels;  i++)
	{
		if (levels[i].pat != NULL)
			continue;
		levels[i].pat = sticky_pattern_new(levels[i].text);
		if (levels[i].pat == NULL)
		{
			/* The error was already shown.  Disable sticky headers
			 * rather than silently shifting the levels. */
			compile_failed = TRUE;
			return;
		}
	}
}

/*
 * Deque primitives.
 */
static void hgrow(lbool at_front)
{
	size_t n = HCOUNT();
	size_t ncap = (hcap == 0) ? 64 : hcap * 2;
	size_t nstart = (ncap - n) / 2;
	struct sticky_hdr *nbuf = (struct sticky_hdr *) ecalloc(ncap, sizeof(struct sticky_hdr));
	if (n > 0)
		memcpy(&nbuf[nstart], &hbuf[hstart], n * sizeof(struct sticky_hdr));
	if (hbuf != NULL)
		free(hbuf);
	hbuf = nbuf;
	hcap = ncap;
	hstart = nstart;
	hend = nstart + n;
	(void) at_front;
}

static void hpush_back(POSITION pos, int level)
{
	if (hend >= hcap)
		hgrow(FALSE);
	hbuf[hend].pos = pos;
	hbuf[hend].level = level;
	hend++;
}

static void hpush_front(POSITION pos, int level)
{
	if (hstart == 0)
		hgrow(TRUE);
	hstart--;
	hbuf[hstart].pos = pos;
	hbuf[hstart].level = level;
}

/*
 * Width of a tab at column col, following the -x tab stops.
 */
static int tab_width(int col)
{
	int to_tab = col;

	if (ntabstops < 2 || to_tab >= tabstops[ntabstops-1])
		to_tab = tabdefault - ((to_tab - tabstops[ntabstops-1]) % tabdefault);
	else
	{
		int i;
		for (i = ntabstops - 2;  i >= 0;  i--)
			if (to_tab >= tabstops[i])
				break;
		to_tab = tabstops[i+1] - to_tab;
	}
	return (to_tab > 0 ? to_tab : 1);
}

/*
 * Measure the indentation of a raw input line.
 * Returns -1 if the line is blank, else the width of its leading
 * white space in columns.
 * With -R, leading ANSI color sequences do not count.
 */
static int line_indent(constant char *line, size_t line_len)
{
	size_t i = 0;
	int col = 0;

	while (i < line_len)
	{
		char c = line[i];
		if (c == ' ')
			col++;
		else if (c == '\t')
			col += tab_width(col);
		else if (c == '\n' || c == '\r')
			return (-1);
		else if (ctldisp && c == '\033' && i+1 < line_len && line[i+1] == '[')
		{
			i += 2;
			while (i < line_len && !(line[i] >= '@' && line[i] <= '~'))
				i++;
		} else
			break;
		if (col > STICKY_INDENT_MAX)
			col = STICKY_INDENT_MAX;
		i++;
	}
	return (i >= line_len ? -1 : col);
}

/*
 * Is a line invisible to the indentation engine?
 * Sets *indent to its indentation if not.
 */
static lbool indent_of(constant char *line, size_t line_len, int *indent)
{
	int ind = line_indent(line, line_len);
	if (ind < 0)
		return (FALSE);
	if (skip_pat != NULL && sticky_pattern_match(skip_pat, line, line_len))
		return (FALSE);
	*indent = ind;
	return (TRUE);
}

/*
 * Which level does an input line belong to?  0 = not a header.
 * In indentation mode the level is the indentation plus one.
 */
static int classify(constant char *line, size_t line_len)
{
	int i;
	if (indent_text != NULL)
	{
		int ind;
		if (!indent_of(line, line_len, &ind))
			return (0);
		if (!sticky_pattern_match(indent_pat, line, line_len))
			return (0);
		return (ind + 1);
	}
	for (i = n_levels-1;  i >= 0;  i--)
	{
		if (sticky_pattern_match(levels[i].pat, line, line_len))
			return (i+1);
	}
	return (0);
}

/*
 * Classify the line at reg_hi and extend the region over it.
 */
static lbool extend_fwd(void)
{
	constant char *line;
	size_t line_len;
	POSITION npos;
	int lvl;

	if (ABORT_SIGS())
		return (FALSE);
	npos = forw_raw_line(reg_hi, &line, &line_len);
	if (npos == NULL_POSITION)
		return (FALSE);
	lvl = classify(line, line_len);
	if (lvl > 0)
		hpush_back(reg_hi, lvl);
	reg_hi = npos;
	return (TRUE);
}

/*
 * Classify the line before reg_lo and extend the region over it.
 */
static lbool extend_bwd(int *plevel)
{
	constant char *line;
	size_t line_len;
	POSITION npos;
	int lvl;

	*plevel = 0;
	if (reg_bof || reg_lo <= scan_floor)
		return (FALSE);
	if (ABORT_SIGS())
		return (FALSE);
	npos = back_raw_line(reg_lo, &line, &line_len);
	if (npos == NULL_POSITION)
	{
		reg_bof = TRUE;
		return (FALSE);
	}
	lvl = classify(line, line_len);
	if (lvl > 0)
		hpush_front(npos, lvl);
	reg_lo = npos;
	if (npos <= ch_zero())
		reg_bof = TRUE;
	*plevel = lvl;
	return (TRUE);
}

/*
 * Make sure the line starting at pos is inside the classified region.
 */
static lbool cover(POSITION pos)
{
	int lvl;

	if (reg_lo == NULL_POSITION ||
	    pos + STICKY_REGION_GAP < reg_lo || pos > reg_hi + STICKY_REGION_GAP)
	{
		sticky_reset();
		reg_lo = reg_hi = pos;
		reg_bof = (pos <= ch_zero());
	}
	while (pos >= reg_hi)
	{
		if (!extend_fwd())
			return (pos < reg_hi);
	}
	while (pos < reg_lo)
	{
		if (!extend_bwd(&lvl))
			return (pos >= reg_lo);
	}
	return (TRUE);
}

/*
 * Prepend lines to the region until a header of at most maxlevel is found.
 * Return the number of header lines added.
 */
static size_t extend_for(int maxlevel)
{
	size_t before = HCOUNT();
	int lvl;

	while (extend_bwd(&lvl))
	{
		if (lvl > 0 && lvl <= maxlevel)
			break;
	}
	return (HCOUNT() - before);
}

/*
 * The position table has one entry per screen row, so with wrapped lines
 * a position can be in the middle of an input line.  Find the start of
 * its line.  Positions are usually asked for in increasing order, so
 * remember the last answer to avoid rescanning long lines.
 */
static POSITION line_start(POSITION pos)
{
	POSITION limit = (cache_in != NULL_POSITION && cache_in <= pos) ? cache_in : NULL_POSITION;
	int c;

	if (pos == NULL_POSITION || pos <= ch_zero())
		return (pos);
	if (ch_seek(pos) != 0)
		return (pos);
	if (limit == pos)
		return (cache_out);
	for (;;)
	{
		c = ch_back_get();
		if (c == '\n' || c == EOI)
			break;
		if (limit != NULL_POSITION && ch_tell() <= limit)
		{
			/* No newline between the last position and this one. */
			cache_in = pos;
			return (cache_out);
		}
	}
	if (c == '\n')
		(void) ch_forw_get();
	cache_in = pos;
	cache_out = ch_tell();
	return (cache_out);
}

/*
 * The indentation which decides what encloses the line at pos:
 * its own, or if it is blank or skipped, that of the next real line.
 */
static int reference_indent(POSITION pos)
{
	int n;

	for (n = 0;  n < STICKY_INDENT_LOOKAHEAD;  n++)
	{
		constant char *line;
		size_t line_len;
		int ind;
		POSITION npos = forw_raw_line(pos, &line, &line_len);
		if (npos == NULL_POSITION)
			break;
		if (indent_of(line, line_len, &ind))
			return (ind);
		pos = npos;
	}
	return (0);
}

/*
 * Find the headers enclosing the line starting at pos.
 * Fill out[] outermost first; return how many there are.
 */
static int stack_for(POSITION pos, struct sticky_hdr *out)
{
	struct sticky_hdr tmp[STICKY_STACK_MAX];
	size_t lo, hi, idx;
	long i;
	int cur_max;
	int nt = 0;
	int k;

	if (!sticky_active() || pos == NULL_POSITION)
		return (0);
	pos = line_start(pos);
	compile_levels();
	if (compile_failed)
		return (0);
	scan_floor = (pos > STICKY_SCAN_LIMIT) ? pos - STICKY_SCAN_LIMIT : 0;
	if (!cover(pos))
		return (0);

	/* idx = first indexed header at or after pos. */
	lo = 0;
	hi = HCOUNT();
	while (lo < hi)
	{
		size_t mid = lo + (hi - lo) / 2;
		if (HDR(mid).pos < pos)
			lo = mid + 1;
		else
			hi = mid;
	}
	idx = lo;

	if (indent_text != NULL)
		cur_max = reference_indent(pos);
	else
	{
		cur_max = n_levels;
		if (idx < HCOUNT() && HDR(idx).pos == pos)
			cur_max = HDR(idx).level - 1;
	}

	i = (long) idx - 1;
	while (cur_max > 0 && nt < STICKY_STACK_MAX)
	{
		struct sticky_hdr h;
		if (i < 0)
		{
			size_t added = extend_for(cur_max);
			if (added == 0)
				break;
			idx += added;
			i += (long) added;
			continue;
		}
		h = HDR(i);
		i--;
		if (h.level <= cur_max)
		{
			tmp[nt++] = h;
			cur_max = h.level - 1;
		}
	}
	for (k = 0;  k < nt;  k++)
		out[k] = tmp[nt-1-k];
	return (nt);
}

/*
 * The most rows we let the sticky headers take.
 */
static int max_rows(void)
{
	int m = (sc_height - 1) / 2;
	return (m < 0 ? 0 : m);
}

/*
 * How many sticky rows does the line at pos need?
 */
public int sticky_rows_for(POSITION pos)
{
	struct sticky_hdr st[STICKY_STACK_MAX];
	int n;

	if (!sticky_active())
		return (0);
	n = stack_for(pos, st);
	return (n > max_rows() ? max_rows() : n);
}

/*
 * How many rows did the last overlay take?
 */
public int sticky_rows_current(void)
{
	return (sticky_active() ? last_rows : 0);
}

/*
 * Choose the headers to show for the current screen contents.
 * The overlay hides the top rows, so we need the smallest k such that the
 * headers enclosing the line at row k fit in k rows.
 */
static int screen_stack(struct sticky_hdr *out)
{
	struct sticky_hdr prev[STICKY_STACK_MAX];
	int prev_n = 0;
	int k;
	int n = 0;

	for (k = 0;  k < sc_height-1;  k++)
	{
		POSITION pos = position(k);
		if (pos == NULL_POSITION)
			break;
		n = stack_for(pos, out);
		if (n > max_rows())
			n = max_rows();
		if (n == k || (n < k && k == 0))
			return (n);
		if (n < k)
		{
			/*
			 * The line at row k is a header which closes some of the
			 * scopes of the line above it, so no stack fits exactly.
			 * Keep the previous line's headers pinned (they cover rows
			 * 0..k-1) until the new header scrolls up into their place.
			 */
			if (prev_n > k)
				prev_n = k;
			memcpy(out, prev, prev_n * sizeof(prev[0]));
			return (prev_n);
		}
		memcpy(prev, out, n * sizeof(prev[0]));
		prev_n = n;
	}
	return (n < k ? n : k);
}

/*
 * Draw the sticky headers over the top rows of the screen.
 * Return TRUE if anything was drawn.
 */
public int overlay_sticky(void)
{
	struct sticky_hdr st[STICKY_STACK_MAX];
	int n;
	int i;
	int drew;

	if (!sticky_active())
	{
		last_rows = 0;
		return (FALSE);
	}
	n = screen_stack(st);
	for (i = 0;  i < n;  i++)
	{
		forw_line_pfx(st[i].pos, sc_width - line_pfx_width(), FALSE);
		/* Every pinned header gets the same highlighting. */
		set_attr_line(AT_COLOR_HEADER | AT_UNDERLINE);
		goto_line(i);
		clear_eol();
		put_line();
	}
	/*
	 * Rows which carried headers before but not now must show their
	 * real contents again.
	 */
	for (i = n;  i < last_rows;  i++)
	{
		POSITION pos = position(i);
		goto_line(i);
		clear_eol();
		if (pos != NULL_POSITION)
		{
			(void) forw_line(pos);
			put_line();
		}
	}
	drew = (n > 0 || last_rows > 0);
	last_rows = n;
	return (drew);
}

/*
 * The settings an option handler changes.
 */
static struct sticky_settings * target_settings(void)
{
	return (sticky_loading_preset ? &preset_settings : &cli_settings);
}

static void shown(constant char *what, constant char *text)
{
	PARG parg;
	char buf[256];
	snprintf(buf, sizeof(buf), "%s: %s", what, (text != NULL) ? text : "none");
	parg.p_string = buf;
	error("%s", &parg);
}

/*
 * Handler for the --sticky-indent option.
 * The argument is the pattern of lines which may be headers;
 * "-" turns the indentation engine off.
 */
public void opt_sticky_indent(int type, constant char *s)
{
	struct sticky_settings *st = target_settings();
	switch (type)
	{
	case INIT:
	case TOGGLE:
		if (s == NULL)
			break;
		free(st->indent_text);
		st->indent_text = (strcmp(s, "-") != 0) ? save(s) : NULL;
		st->have_indent = TRUE;
		if (!sticky_loading_preset)
			sticky_settings_changed();
		break;
	case QUERY:
		shown("Sticky indent header pattern", indent_text);
		break;
	}
}

/*
 * Handler for the --sticky-skip option: lines which the indentation engine
 * ignores (comments etc.).  Blank lines are always ignored.
 */
public void opt_sticky_skip(int type, constant char *s)
{
	struct sticky_settings *st = target_settings();
	switch (type)
	{
	case INIT:
	case TOGGLE:
		if (s == NULL)
			break;
		free(st->skip_text);
		st->skip_text = (strcmp(s, "-") != 0) ? save(s) : NULL;
		st->have_skip = TRUE;
		if (!sticky_loading_preset)
			sticky_settings_changed();
		break;
	case QUERY:
		shown("Sticky skip pattern", skip_text);
		break;
	}
}

/*
 * Handler for the --sticky-header option.
 * Each use adds one level.  "-" removes all levels.
 */
public void opt_sticky_header(int type, constant char *s)
{
	struct sticky_settings *st = target_settings();
	switch (type)
	{
	case INIT:
	case TOGGLE:
		if (s == NULL)
			break;
		if (strcmp(s, "-") == 0)
		{
			clear_levels(st);
		} else if (st->n_levels >= MAX_STICKY_LEVELS)
		{
			error("Too many sticky header levels", NULL_PARG);
			break;
		} else
		{
			st->level_text[st->n_levels++] = save(s);
		}
		st->have_levels = TRUE;
		if (!sticky_loading_preset)
			sticky_settings_changed();
		break;
	case QUERY:
	{
		char buf[256];
		int i;
		size_t len = 0;

		buf[0] = '\0';
		for (i = 0;  i < n_levels;  i++)
		{
			int w = snprintf(buf + len, sizeof(buf) - len, "%s%s",
				(i > 0) ? " > " : "", levels[i].text);
			if (w < 0 || (size_t) w >= sizeof(buf) - len)
				break;
			len += (size_t) w;
		}
		shown("Sticky header levels", (n_levels > 0) ? buf : NULL);
		break;
	}
	}
}
