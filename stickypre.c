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
 * Read the first line of the current file.
 */
static lbool read_first_line(char *buf, size_t buflen)
{
	size_t n = 0;

	if (ch_seek(ch_zero()) != 0)
		return (FALSE);
	while (n + 1 < buflen)
	{
		int c = ch_forw_get();
		if (c == EOI || c == '\n' || c == '\0' || ABORT_SIGS())
			break;
		if (c == '\r')
			continue;
		buf[n++] = (char) c;
	}
	buf[n] = '\0';
	return (n > 0);
}

/*
 * Does a block header line (a list of patterns) match the file?
 */
static lbool block_matches(constant char *line, constant char *filename, constant char *interp)
{
	char tok[PRESET_LINE_MAX];
	constant char *p = line;

	while (next_token(&p, tok, sizeof(tok)))
	{
		if (tok[0] == '$')
		{
			if (interp != NULL && glob_match(tok + 1, interp))
				return (TRUE);
		} else if (filename != NULL)
		{
			constant char *name = (strchr(tok, '/') != NULL) ? filename : base_name(filename);
			if (glob_match(tok, name))
				return (TRUE);
		}
	}
	return (FALSE);
}

static void preset_error(constant char *path, int lineno, constant char *msg)
{
	PARG parg;
	char buf[PRESET_LINE_MAX];

	snprintf(buf, sizeof(buf), "%s: line %d: %s", path, lineno, msg);
	parg.p_string = buf;
	error("Sticky presets: %s", &parg);
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
static lbool load_file(constant char *path, constant char *filename, constant char *interp)
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
			selected = block_matches(line, filename, interp);
		}
	}
	fclose(f);
	return (selected);
}

/*
 * Go through the presets files in order.
 */
static void load_presets(constant char *filename, constant char *interp)
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
				done = load_file(p, filename, interp);
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
			done = load_file(home, filename, interp);
			free(home);
		}
#ifdef STICKYPRESETS
		if (!done)
			(void) load_file(STICKYPRESETS, filename, interp);
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
		char first[SHEBANG_MAX];
		char interp[SHEBANG_MAX];
		lbool have_interp = FALSE;

		if (read_first_line(first, sizeof(first)))
			have_interp = shebang_interpreter(first, interp, sizeof(interp));
		sticky_loading_preset = TRUE;
		load_presets(strcmp(filename, "-") != 0 ? filename : NULL,
			have_interp ? interp : NULL);
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
