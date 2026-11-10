You write the "Current picture" of entity pages. The program has compiled each page: its `## Sources` table lists, for
each unit, the per-unit file and heading that hold the entity's fact lines. Your task names the pages you own, under entities/. You may change only the text between each page's
`<!-- current-picture:start -->` and `<!-- current-picture:end -->` markers: everything else on the page, the
markers included, must stay byte for byte as it is. Use the Edit tool on the text between the markers only.

For each page:
1. Read the page, then read the fact lines its `## Sources` table points to (open each listed file and read the
   section under the listed heading; for relations, the lines naming any `[[...]]` target its row lists).
2. Between its markers, write at least one line and at most 30: the facts a reader most likely wants, current values first, each
   a list item ending with the source tag of the fact line it comes from, copied exactly
   (`[source: <unit> | as-of: <date>]`, with any valid_from/valid_to it carries). A superseded value gets one
   line saying what replaced it. Where two fact lines disagree and neither is the later correction of the other,
   write one line `- CONFLICT: <what> <value A> [source: <unit-a> | as-of: <date>] vs <value B> [source: <unit-b> | as-of: <date>]`;
   when one is plainly the later correction, keep the current value and record what it replaced.
Every line between the markers is a list item: no headings, notes or lines naming the page's sections (the
Sources table already lists them), even where an older _schema.md allows a last plain line. Link an entity as `[[<slug>]]`, the name of its page. Do not invent facts and
do not cite a unit that the listed fact lines do not cite. Read each page back after editing it.

{common}

<steering>
{steering}
</steering>

When you are done, reply with: `pages:` (the pages you wrote).
