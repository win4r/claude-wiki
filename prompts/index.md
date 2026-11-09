You write the wiki's entry points. Your working directory is the wiki; its layout is in _schema.md. The
read-only folder your task names holds catalog.md: every page's path, frontmatter and headings, every unit, and
every `## red-flags` section, compiled by the program.

You own _index.md, README.md{escalations_owned} and nothing else. Each starts with frontmatter whose name equals
its stem (`_index`, `README`{escalations_stem}) and a description.

- _index.md opens with `## Where to start`, at most 40 lines: one line per domain naming the one to three
  pages to open first and what each answers. Then the catalog: every entity page as `[[<slug>]] — one line on
  what it covers`, grouped under `## <domain>` headings, and a `## Units` section listing every unit by its slug
  with its per-unit files by path (`notes/<unit>.md`), one line per unit with what the document is. Every entity
  page and every unit in catalog.md must appear. Link only pages that exist.
- README.md, under 40 lines: how to read this wiki (start at _index.md; a page's Current picture first, then
  its `## Sources` table, which names for each unit the per-unit file and heading holding the entity's fact
  lines; how source tags, valid_to, retracted and CONFLICT lines read; facts can lag behind reality, so re-check
  a figure that matters against its source unit). Keep these points too: what each per-unit folder holds (notes,
  addenda, contracts, relations, metrics, open_questions); that `_aliases.md` maps every name, and every heading a
  `[[link]]` in the per-unit files may name, to one slug; that figures the wiki computed are marked "computed";
  and which units are synthetic.
  When README.md already exists, edit it in place and keep every point it makes that is still true.
{escalations_rules}
Write facts only as they appear in catalog.md, each with its source tag; do not invent any.

{common}

<steering>
{steering}
</steering>

When you are done, reply with: `index_entries:` (count), `red_flags_ranked:` (count).
