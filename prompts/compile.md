You write the "Current picture" of entity pages. The program has compiled each page from its fact lines, grouped
by unit. Your task names the pages you own, under entities/. You may change only the text between each page's
`<!-- current-picture:start -->` and `<!-- current-picture:end -->` markers: everything else on the page, the
markers included, must stay byte for byte as it is. Use the Edit tool on the text between the markers only.

For each page:
1. Read the page (for a long one, its fact lines in parts).
2. Between its markers, write at most 30 lines: the facts a reader most likely wants, current values first, each
   a list item ending with the source tag of the fact line it comes from, copied exactly
   (`[source: <unit> | as-of: <date>]`, with any valid_from/valid_to it carries). A superseded value gets one
   line saying what replaced it. Where two fact lines disagree and neither is the later correction of the other,
   write one line `- CONFLICT: <what> <value A> [source: <unit-a> | as-of: <date>] vs <value B> [source: <unit-b> | as-of: <date>]`;
   when one is plainly the later correction, keep the current value and record what it replaced.
3. End with one plain line (not a list item) naming the page's sections.
Do not invent facts and do not cite a unit that the page's fact lines do not cite. Read each page back after
editing it.

{common}

<steering>
{steering}
</steering>

When you are done, reply with: `pages:` (the pages you wrote).
