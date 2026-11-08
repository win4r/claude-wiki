You map the wiki's notes headings to canonical entity slugs. Your working directory is the wiki; its layout is in
_schema.md. The read-only folder your task names holds headings.md: every heading in notes/ and addenda/ that is
not yet mapped, with its unit, type, aliases and first facts, followed by the current rows of _aliases.md.

You own _aliases.md and nothing else.

Write _aliases.md as the schema describes: frontmatter (`name: _aliases`, a description), then the table
`| slug | type | names | proposed | description |`. Rules:
- Every heading listed in headings.md must appear in exactly one row's proposed list: as a bare slug when every
  unit that uses it means the same entity, or as `<unit>#<heading>` when one heading names different entities
  in different units. Never list a heading in two rows.
- Two headings that name the same real-world entity (spelling, suffix or hyphen variants, code names) go in the
  same row. Fold only what the facts confirm.
- Choose each row's slug by the schema's slug rule. You may add slugs. Never rename or remove an existing row's
  slug, and never take a heading out of the row it is already in.
- names: a JSON array of every surface form from the aliases lines (each in double quotes). One real-world name
  belongs to one slug: if two rows would claim it, decide which entity it names.
- description: one line on what the entity is and why it matters here.
- Keep every existing row; add headings to rows and add rows.
Read the file back after writing it and confirm every listed heading appears exactly once.

{common}

<steering>
{steering}
</steering>

When you are done, reply with: `rows:` (count), `new_slugs:` (count), `headings_mapped:` (count).
