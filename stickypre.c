/*
 * Sticky header presets.
 *
 * With --sticky-presets, when a file is opened less looks for a block in
 * the presets files whose patterns match the file, and applies the
 * --sticky-* options of the first such block.  Options on the command line
 * take precedence (see sticky.c).
 *
 * A presets file consists of blocks.  A block starts with an unindented line
 * of one or more space-separated patterns; the indented lines after it are
 * its options, one per line, written like on the command line.  The value of
 * an option is everything after the first "=" up to the end of the line, so
 * regular expressions need no quoting.  Lines starting with "#" are comments.
 *
 *     *.py $python*
 *         --sticky-indent=^[[:space:]]*(def|class)
 *         --sticky-skip=^[[:space:]]*(#|$)
 *
 * A pattern is a glob matched against the name of the file (its last
 * component, unless the pattern contains a "/", then the whole name).
 * A pattern starting with "$" (which is not part of the pattern) is matched
 * against the interpreter named by the "#!" first line of the file: the
 * last component of the interpreter's path, or, if that is "env", of the
 * first argument of env which is not an option or a NAME=value.
 * A pattern of the form ":LINE:REGEXP" matches a file with a line which
 * matches REGEXP: line number LINE (counted from 1), or any line if LINE is
 * empty, or any line of the header if LINE is "H" (the lines before the first
 * blank one).  It contains no blanks; write [[:blank:]] or [ ].
 *
 * The file is read only if its name has not matched: the patterns of a block
 * are tried name globs first, then "$" patterns, then ":" patterns, and the
 * reading stops at the line which decides.  It never goes beyond SCAN_MAX
 * bytes.  A pipe is not waited for: only the data of its first read is
 * examined, and a line which is not complete in it is ignored.
 *
 * Presets files: the colon-separated list in $LESSSTICKYPRESETS, or if that
 * is not set, ~/.lesssticky and then the system-wide file.
 * The first block which matches wins, over all files in that order.
 */

#include "less.h"
#include "option.h"

extern IFILE curr_ifile;
extern int sigs;
extern lbool sticky_loading_preset;

/* The --sticky-presets option. */
public int sticky_presets = 0;

#define PRESET_LINE_MAX 8192
#define SHEBANG_MAX 512
#define SCAN_MAX (1 << 20)

/* The LINE of a ":LINE:REGEXP" pattern, if it is not a line number. */
#define LINE_ANY 0
#define LINE_HEADER (-1)
#define LINE_BAD (-2)

/* The file which the presets are looked up for, as far as it is known yet. */
struct pfile
{
	constant char *name;  /* NULL for input from a pipe */
	char interp[SHEBANG_MAX];
	lbool interp_read;    /* the first line was looked at */
	lbool has_interp;
};

/*
 * Match a glob: * ? [set] [!set] and \ to quote.
 */
static lbool glob_match(constant char *pat, constant char *str)
{
	for (;;)
	{
		switch (*pat)
		{
		case '\0':
			return (*str == '\0');
		case '*':
			while (*pat == '*')
				pat++;
			if (*pat == '\0')
				return (TRUE);
			for (;  *str != '\0';  str++)
				if (glob_match(pat, str))
					return (TRUE);
			return (glob_match(pat, str));
		case '?':
			if (*str == '\0')
				return (FALSE);
			pat++;
			str++;
			break;
		case '[':
		{
			constant char *p = pat + 1;
			lbool neg = FALSE;
			lbool found = FALSE;
			lbool first = TRUE;

			if (*str == '\0')
				return (FALSE);
			if (*p == '!' || *p == '^')
			{
				neg = TRUE;
				p++;
			}
			while (*p != '\0' && (first || *p != ']'))
			{
				unsigned char lo = (unsigned char) *p;
				unsigned char hi = lo;
				first = FALSE;
				if (p[1] == '-' && p[2] != '\0' && p[2] != ']')
				{
					hi = (unsigned char) p[2];
					p += 3;
				} else
					p++;
				if ((unsigned char) *str >= lo && (unsigned char) *str <= hi)
					found = TRUE;
			}
			if (*p != ']')
			{
				/* No closing bracket: the [ is an ordinary character. */
				if (*str != '[')
					return (FALSE);
				pat++;
				str++;
				break;
			}
			if (found == neg)
				return (FALSE);
			pat = p + 1;
			str++;
			break;
		}
		case '\\':
			if (pat[1] != '\0')
				pat++;
			/* fall through */
		default:
			if (*pat != *str)
				return (FALSE);
			pat++;
			str++;
			break;
		}
	}
}

static constant char * base_name(constant char *path)
{
	constant char *slash = strrchr(path, '/');
	return (slash != NULL) ? slash + 1 : path;
}

/*
 * Get the next blank-separated token of *pp into buf.
 */
static lbool next_token(constant char **pp, char *buf, size_t buflen)
{
	constant char *p = *pp;
	size_t n = 0;

	while (*p == ' ' || *p == '\t')
		p++;
	if (*p == '\0')
		return (FALSE);
	while (*p != '\0' && *p != ' ' && *p != '\t')
	{
		if (n + 1 < buflen)
			buf[n++] = *p;
		p++;
	}
	buf[n] = '\0';
	*pp = p;
	return (TRUE);
}

/*
 * From the first line of a file, find the name of the interpreter.
 * Put it in out and return TRUE, or return FALSE if the line is not a
 * "#!" line or names no interpreter.
 */
static lbool shebang_interpreter(constant char *line, char *out, size_t outlen)
{
	char tok[SHEBANG_MAX];
	constant char *p;

	if (line[0] != '#' || line[1] != '!')
		return (FALSE);
	p = line + 2;
	if (!next_token(&p, tok, sizeof(tok)))
		return (FALSE);
	if (strcmp(base_name(tok), "env") != 0)
	{
		snprintf(out, outlen, "%s", base_name(tok));
		return (TRUE);
	}
	/* env: the command is its first argument which is not an option. */
	while (next_token(&p, tok, sizeof(tok)))
	{
		if (strcmp(tok, "-u") == 0 || strcmp(tok, "-C") == 0 ||
		    strcmp(tok, "--unset") == 0 || strcmp(tok, "--chdir") == 0)
		{
			/* These take an argument. */
			char skip[SHEBANG_MAX];
			(void) next_token(&p, skip, sizeof(skip));
			continue;
		}
		if (tok[0] == '-' || strchr(tok, '=') != NULL)
			continue;
		snprintf(out, outlen, "%s", base_name(tok));
		return (TRUE);
	}
	return (FALSE);
}

/*
 * Read the line at the read pointer of the current file into buf, without
 * its line ending and cut to buflen-1 characters; *blank tells whether it
 * has only white space.  Return FALSE if there is no line to read: at the
 * end of the file, at a NUL (binary data), after SCAN_MAX bytes, or on a pipe
 * when the rest of the line has not arrived yet.  Only the very first read of
 * a pipe waits for data.
 */
static lbool read_line(char *buf, size_t buflen, lbool *blank)
{
	size_t n = 0;
	size_t got = 0;

	*blank = TRUE;
	for (;;)
	{
		int c;

		if (ch_tell() >= SCAN_MAX || ABORT_SIGS() ||
		    (ch_tell() > ch_zero() && ch_would_wait()))
			return (FALSE);
		c = ch_forw_get();
		if (c == '\0' || (c == EOI && got == 0))
			return (FALSE);
		if (c == EOI || c == '\n')
			break;
		got++;
		if (c != ' ' && (c < '\t' || c > '\r'))
			*blank = FALSE;
		if (n + 1 < buflen)
			buf[n++] = (char) c;
	}
	while (n > 0 && buf[n-1] == '\r')
		n--;
	buf[n] = '\0';
	return (TRUE);
}

/*
 * Get the interpreter named by the "#!" line of the file, or NULL.
 */
static constant char * file_interp(struct pfile *pf)
{
	if (!pf->interp_read)
	{
		char first[SHEBANG_MAX];
		lbool blank;

		pf->interp_read = TRUE;
		if (ch_seek(ch_zero()) == 0 && read_line(first, sizeof(first), &blank))
			pf->has_interp = shebang_interpreter(first, pf->interp, sizeof(pf->interp));
	}
	return (pf->has_interp ? pf->interp : NULL);
}

/*
 * Does a line of the current file match the compiled pattern re?
 * LINE is the number of the line, LINE_ANY for any line, or LINE_HEADER for
 * any line before the first blank one.
 */
static lbool content_matches(void *re, int line)
{
	char buf[PRESET_LINE_MAX];
	lbool blank;
	int n;

	if (re == NULL || ch_seek(ch_zero()) != 0)
		return (FALSE);
	for (n = 1;  line <= 0 || n <= line;  n++)
	{
		if (!read_line(buf, sizeof(buf), &blank))
			break;
		if (line == LINE_HEADER && blank)
			break;
		if ((line <= 0 || n == line) && sticky_pattern_match(re, buf, strlen(buf)))
			return (TRUE);
	}
	return (FALSE);
}

/*
 * Is tok a ":LINE:REGEXP" pattern?  If so, set *line to LINE (a number from
 * 1, LINE_ANY, LINE_HEADER or LINE_BAD) and *re to REGEXP.
 */
static lbool parse_content(constant char *tok, int *line, constant char **re)
{
	constant char *colon = (tok[0] == ':') ? strchr(tok + 1, ':') : NULL;
	char *end;
	long n;

	if (colon == NULL)
		return (FALSE);
	*re = colon + 1;
	if (colon == tok + 1)
		*line = LINE_ANY;
	else if (colon == tok + 2 && tok[1] == 'H')
		*line = LINE_HEADER;
	else
	{
		n = strtol(tok + 1, &end, 10);
		*line = (tok[1] >= '0' && tok[1] <= '9' && end == colon && n >= 1)
			? (int) ((n > SCAN_MAX) ? SCAN_MAX : n) : LINE_BAD;
	}
	return (TRUE);
}

static void preset_error(constant char *path, int lineno, constant char *msg)
{
	PARG parg;
	char buf[PRESET_LINE_MAX];

	snprintf(buf, sizeof(buf), "%s: line %d: %s", path, lineno, msg);
	parg.p_string = buf;
	error("Sticky presets: %s", &parg);
}

/*
 * Does a block header line (a list of patterns) match the file?
 * The patterns are tried in three passes, cheapest first, so that the file
 * is read only when its name has not matched: name globs, "$" patterns,
 * ":LINE:REGEXP" patterns.
 */
static lbool block_matches(constant char *header, struct pfile *pf, constant char *path, int lineno)
{
	char tok[PRESET_LINE_MAX];
	int pass;

	for (pass = 0;  pass < 3;  pass++)
	{
		constant char *p = header;

		while (next_token(&p, tok, sizeof(tok)))
		{
			constant char *re = "";
			int line = LINE_ANY;

			if (pass != ((tok[0] == '$') ? 1 : parse_content(tok, &line, &re) ? 2 : 0))
				continue;
			if (pass == 0)
			{
				if (pf->name != NULL &&
				    glob_match(tok, (strchr(tok, '/') != NULL) ? pf->name : base_name(pf->name)))
					return (TRUE);
			} else if (pass == 1)
			{
				constant char *interp = file_interp(pf);
				if (interp != NULL && glob_match(tok + 1, interp))
					return (TRUE);
			} else if (line == LINE_BAD || *re == '\0')
			{
				preset_error(path, lineno, "expected :LINE:REGEXP, LINE being empty, H or a number from 1");
			} else
			{
				void *pat = sticky_pattern_new(re);
				lbool matched = content_matches(pat, line);
				sticky_pattern_free(pat);
				if (matched)
					return (TRUE);
			}
		}
	}
	return (FALSE);
}

static void apply_option(constant char *opt, constant char *path, int lineno)
{
	if (strncmp(opt, "--sticky-", 9) != 0 || strncmp(opt, "--sticky-presets", 16) == 0)
	{
		preset_error(path, lineno, "only --sticky-* options are allowed");
		return;
	}
	if (strchr(opt, '=') == NULL)
	{
		preset_error(path, lineno, "option needs =VALUE");
		return;
	}
	scan_option(opt);
}

/*
 * Read one presets file and apply the first block which matches.
 * Return TRUE if a block was found (even if it has no options).
 */
static lbool load_file(constant char *path, struct pfile *pf)
{
	FILE *f = fopen(path, "r");
	char line[PRESET_LINE_MAX];
	lbool selected = FALSE;
	int lineno = 0;

	if (f == NULL)
		return (FALSE);
	while (fgets(line, sizeof(line), f) != NULL)
	{
		size_t len = strlen(line);
		constant char *p;

		lineno++;
		while (len > 0 && (line[len-1] == '\n' || line[len-1] == '\r'))
			line[--len] = '\0';
		if (line[0] == ' ' || line[0] == '\t')
		{
			/* An option of the current block, or a comment. */
			p = line;
			while (*p == ' ' || *p == '\t')
				p++;
			if (*p == '\0' || *p == '#' || !selected)
				continue;
			apply_option(p, path, lineno);
		} else
		{
			if (line[0] == '\0' || line[0] == '#')
				continue;
			if (selected)
				break;
			selected = block_matches(line, pf, path, lineno);
		}
	}
	fclose(f);
	return (selected);
}

/*
 * Go through the presets files in order.
 */
static void load_presets(struct pfile *pf)
{
	constant char *env = lgetenv("LESSSTICKYPRESETS");

	if (env != NULL)
	{
		char *list = save(env);
		char *p = list;
		lbool done = FALSE;

		while (!done && p != NULL && *p != '\0')
		{
			char *colon = strchr(p, ':');
			if (colon != NULL)
				*colon = '\0';
			if (*p != '\0')
				done = load_file(p, pf);
			p = (colon != NULL) ? colon + 1 : NULL;
		}
		free(list);
		return;
	}
	{
		char *home = homefile(".lesssticky");
		lbool done = FALSE;
		if (home != NULL)
		{
			done = load_file(home, pf);
			free(home);
		}
#ifdef STICKYPRESETS
		if (!done)
			(void) load_file(STICKYPRESETS, pf);
#endif
	}
}

/*
 * Called when a file has been opened (and when --sticky-presets is toggled):
 * choose the preset for it and work out the effective settings.
 */
public void sticky_file_opened(constant char *filename)
{
	sticky_preset_clear();
	if (sticky_presets && filename != NULL &&
	    strcmp(filename, FAKE_HELPFILE) != 0 && strcmp(filename, FAKE_EMPTYFILE) != 0)
	{
		struct pfile pf;

		pf.name = (strcmp(filename, "-") != 0) ? filename : NULL;
		pf.interp_read = FALSE;
		pf.has_interp = FALSE;
		sticky_loading_preset = TRUE;
		load_presets(&pf);
		sticky_loading_preset = FALSE;
	}
	sticky_settings_changed();
}

/*
 * Handler for the --sticky-presets option.
 */
public void opt_sticky_presets(int type, constant char *s)
{
	(void) s;
	if (type == TOGGLE && curr_ifile != NULL_IFILE)
		sticky_file_opened(get_filename(curr_ifile));
}
