# Evidence for heldout-tier.toml

All files are in `workspaces/sec-deal-core/docs/`. Each entry is `file:line` followed by a verbatim quote from that line.
Every `file:line` and quote pair below was re-checked mechanically after this file was written (`sed -n '<line>p' <file> | /usr/bin/grep -F '<quote>'`): 82 of 82 matched.
Dollar amounts in the 10-K and 10-Q notes and tables are in thousands unless the text says "million".

12 questions, 49 criteria: 4 lookup (17 criteria), 6 cross-document (28 criteria), 2 trap (4 criteria).

---

## ho-google-domains (lookup, 10-K FY2023)

1. Signed June 15, 2023; closed September 7, 2023
   - `10k-fy2023-part4.txt:146` — "On June 15, 2023, the Company entered into an asset purchase agreement"
   - `10k-fy2023-part4.txt:146` — "The Google Domains Asset Acquisition closed on September 7, 2023"
2. Total paid $180.7M
   - `10k-fy2023-part4.txt:147` — "The total amount paid for the Google Domains Asset Acquisition was $180,721, including direct transaction costs of $721"
   - `10k-fy2023-part2.txt:254` — "for a total amount paid of $180.7 million"
3. Funded with cash on hand plus $100M of additional term loans
   - `10k-fy2023-part4.txt:147` — "The Company funded the asset acquisition with cash on hand as well as the proceeds from additional term loan commitments of $100,000"
4. $176.7M to customer relationships, amortized over 4 years
   - `10k-fy2023-part4.txt:147` — "$176,721 was allocated to the asset classified as customer relationships"
   - `10k-fy2023-part4.txt:147` — "which was estimated to be 4 years as of the Closing Date"

Note: `10k-fy2023-part3.txt:219` gives "$176.7 million used for the Google Domains Asset Acquisition" as the investing cash outflow. That is the intangible, not the total paid; the other $4,000 thousand is a transition services agreement (`10k-fy2023-part4.txt:147`).

## ho-centerview-valuation (lookup, DEFM14A)

Context: `defm14a-merger-proxy-part3.txt:127` — "On May 12, 2024, Centerview rendered to the Special Committee its oral opinion" (the opinion on the $44.00 price).

1. Comparable public companies: about $38.10 to $45.10 per share
   - `defm14a-merger-proxy-part3.txt:216` — "to derive a range of implied values per share of approximately $38.10 to $45.10"
2. DCF: about $41.65 to $53.10 per share
   - `defm14a-merger-proxy-part3.txt:222` — "to derive a range of implied values per share of Common Stock of approximately $41.65 to $53.10"
3. DCF discount rates 12.5% to 14.5%
   - `defm14a-merger-proxy-part3.txt:219` — "using discount rates ranging from 12.5% to 14.5%"
4. Analyst price targets $38.00 to $50.00
   - `defm14a-merger-proxy-part3.txt:226` — "these price targets ranged from a low of $38.00 per share of Common Stock to a high of $50.00 per share of Common Stock"

## ho-sale-process (lookup, DEFM14A)

1. Board formed the special committee on February 22, 2024
   - `defm14a-merger-proxy-part2.txt:185` — "On February 22, 2024, the Squarespace Board held a meeting"
   - `defm14a-merger-proxy-part2.txt:185` — "(i) authorized the formation of a special committee"
2. Members Fleisher (chair), Klein, Montgomery
   - `defm14a-merger-proxy-part2.txt:185` — "(iii) designated Michael Fleisher, Jonathan Klein and Neela Montgomery to serve as members of the Special Committee, with Mr. Fleisher to serve as Chair of the Special Committee"
3. Advisers: Richards, Layton & Finger (legal), Centerview (financial)
   - `defm14a-merger-proxy-part2.txt:190` — "Richards, Layton & Finger, P.A." (defined there as RLF)
   - `defm14a-merger-proxy-part2.txt:191` — "the Special Committee determined to engage RLF to serve as legal counsel to the Special Committee"
   - `defm14a-merger-proxy-part2.txt:217` — "On April 1, 2024, the Special Committee executed an engagement letter with respect to Centerview" ... "engagement as financial advisor to the Special Committee"
4. Eight parties contacted, seven signed NDAs
   - `defm14a-merger-proxy-part2.txt:313` — "eight parties (including Permira) were contacted in such process to solicit interest in a potential transaction with Squarespace, seven of which entered into non-disclosure agreements"
5. Management diligence sessions with five parties
   - `defm14a-merger-proxy-part2.txt:313` — "management diligence sessions were conducted with five parties (including Permira)"

Note: J.P. Morgan and Skadden advised the company, not the committee (`defm14a-merger-proxy-part2.txt:183`, `:184`); an answer naming them as the committee's advisers does not meet criterion 3.

## ho-first-quarter-margin (lookup, 10-Q Q1 2024)

1. 71.3% against 81.9%
   - `10q-q1-2024-part1.txt:956` — "Gross profit as a percentage of revenue was 71.3% for the three months ended March 31, 2024 compared to 81.9% for the same period in 2023"
2. Cost of revenue up $37.8M (88.1%)
   - `10q-q1-2024-part1.txt:950` — "Cost of revenue increased $37.8 million, or 88.1%, for the three months ended March 31, 2024"
   - `10q-q1-2024-part1.txt:944` — "Cost of revenue | $ | 80,774 | $ | 42,950 | $ | 37,824 | 88.1 | %"
3. Higher domain registration fees, tied to the Google domains
   - `10q-q1-2024-part1.txt:950` — "increases in domain name registration fees of $31.3 million"
   - `10q-q1-2024-part1.txt:953` — "primarily attributed to domain subscriptions associated with the Google Domains Asset Acquisition"
4. Timing mismatch between revenue and cost recognition
   - `10q-q1-2024-part1.txt:956` — "we recognize revenue for our domain subscription ratably over a period of twelve months and we recognize the cost of registering domains upon purchase or renewal"

## ho-termination-fees (cross-document: May 2024 8-K and September 2024 8-K)

1. Original: Squarespace would owe $198,700,000
   - `8k-merger-announcement.txt:67` — "the Company will be required to pay Parent a termination fee of $198,700,000"
   - also `defm14a-merger-proxy-part1.txt:816` — "Squarespace will be required to pay Parent the Company Termination Fee of $198,700,000"
2. Original: the buyer would owe $231,816,666.67
   - `8k-merger-announcement.txt:69` — "Parent will be required to pay the Company a termination fee of $231,816,666.67"
   - also `defm14a-merger-proxy-part1.txt:818` — "Parent will be required to pay Squarespace the Parent Termination Fee of $231,816,666.67"
3. Amended: Squarespace would owe $210,493,094.39
   - `8k-amended-agreement.txt:129` — "the Company will be required to pay Parent a termination fee of $210,493,094.39"
4. Amended: the buyer would owe $245,575,276.79
   - `8k-amended-agreement.txt:133` — "Parent will be required to pay the Company a termination fee of $245,575,276.79"
5. Outside date May 12, 2025 in both
   - `8k-merger-announcement.txt:70` — "may terminate the Merger Agreement if the Merger is not consummated by May 12, 2025"
   - `8k-amended-agreement.txt:135` — "the Merger is not consummated by May 12, 2025"

Left out on purpose: the cap on the buyer's damages. The May 8-K puts it at $198,700,000 (`8k-merger-announcement.txt:72`) and the September 8-K at $245,575,276.79 (`8k-amended-agreement.txt:140`); the May figure equals the company's fee, not the buyer's, so I did not build a criterion on it.

## ho-credit-agreement (cross-document: 10-K FY2023, 10-Q Q1 2024, closing 8-K)

1. Term loan increased to $650.0M; $25.0M revolver
   - `10k-fy2023-part3.txt:230` — "to increase the total size of the 2020 Term Loan to $650.0 million"
   - `10k-fy2023-part3.txt:230` — "a $25.0 million revolving credit facility"
2. $571.4M outstanding at December 31, 2023
   - `10k-fy2023-part3.txt:233` — "As of December 31, 2023, $571.4 million was outstanding under the Term Loan"
3. $559.2M outstanding at March 31, 2024
   - `10q-q1-2024-part2.txt:17` — "As of March 31, 2024, $559.2 million was outstanding under the Term Loan"
4. Maturity December 11, 2025
   - `10k-fy2023-part3.txt:230` — "extend the maturity date for the 2019 Term Loan and the Revolving Credit Facility to December 11, 2025"
5. Repaid in full and terminated at closing, October 17, 2024
   - `8k-closing.txt:76` — "On October 17, 2024, in connection with the Merger, all outstanding indebtedness under that certain Amended and Restated Credit"
   - `8k-closing.txt:78` — "was repaid in full, all commitments thereunder were terminated"

## ho-tock (cross-document: 10-K FY2022 and DEFM14A)

1. Acquired March 31, 2021 for $425.7M
   - `10k-fy2022-part3.txt:471` — "On March 31, 2021, the Company acquired all of the equity interests in Tock, Inc."
   - `10k-fy2022-part3.txt:731` — "The total consideration for the transaction was $425,710"
2. $226.8M cash and $188.2M Class C stock
   - `10k-fy2022-part3.txt:731` — "consisting of $226,821 of cash, $188,179 of the Company" (continues: "’s Class C common stock, and $10,710 of net working capital adjustments")
3. $225.2M goodwill impairment in fiscal 2022, tied to Tock
   - `10k-fy2022-part4.txt:188` — "the Company recognized a non-cash goodwill impairment charge of $225,163"
   - `10k-fy2022-part4.txt:188` — "The charge was primarily due to market values deteriorating subsequent to the Tock acquisition in March 2021"
4. Sale to American Express agreed June 21, 2024
   - `defm14a-merger-proxy-part6.txt:723` — "On June 21, 2024, Squarespace and American Express Travel Related Services Company, Inc."
5. $400M in cash
   - `defm14a-merger-proxy-part6.txt:723` — "is $400,000,000 in cash"

Note: the FY2023 10-K repeats the purchase terms (`10k-fy2023-part4.txt:150`) and adds a second cause for the impairment, a change in product strategy (`10k-fy2023-part2.txt:292`). Criterion 3 only requires the link to Tock. The proxy describes the sale as agreed, not completed.

## ho-subscription-metrics (cross-document: 10-K FY2023 and 10-Q Q1 2024; FY2022 10-K agrees)

1. Unique subscriptions 4,204 thousand (2022) and 4,631 thousand (2023)
   - `10k-fy2023-part3.txt:245` — "Unique subscriptions (in thousands) | 4,631 | 4,204 | 4,086"
   - `10k-fy2022-part3.txt:93` — "Unique subscriptions (in thousands) | 4,204 | 4,086"
2. 4,912 thousand at March 31, 2024
   - `10q-q1-2024-part2.txt:30` — "Unique subscriptions (in thousands) (1)"
   - `10q-q1-2024-part2.txt:31` — "4,912 | 4,264"
3. ARRR $931.7M (2022) and $1,105.7M (2023)
   - `10k-fy2023-part3.txt:247` — "ARRR (in thousands) | $ | 1,105,743 | $ | 931,708 | $ | 835,194"
   - `10k-fy2022-part3.txt:95` — "ARRR (in thousands) | $ | 931,708 | $ | 835,194"
4. ARRR $1,117.7M at March 31, 2024
   - `10q-q1-2024-part2.txt:33` — "ARRR (in thousands) (2)"
   - `10q-q1-2024-part2.txt:34` — "$ | 1,117,659 | $ | 941,860"
5. The 10-Q changed the ARRR calculation
   - `10k-fy2023-part3.txt:258` — "in the last month of the period multiplied by 12"
   - `10q-q1-2024-part2.txt:43` — "We have since revised our calculation to use quarterly revenue from subscription fees and revenue generated in conjunction with associated fees in the last quarter of the period multiplied by 4"

Note: the 10-Q recast only the March 31, 2023 comparative ($941,860 thousand). The December 31, 2023 figure of $1,105,743 thousand is on the old monthly basis.

## ho-headcount (cross-document: 10-K FY2022, 10-K FY2023, vendor extract)

1. 1,800 at December 31, 2022
   - `10k-fy2022-part1.txt:225` — "As of December 31, 2022, we had 1,800 full- and part-time employees"
2. 1,749 at December 31, 2023
   - `10k-fy2023-part1.txt:227` — "As of December 31, 2023, we had 1,749 full- and part-time employees"
3. Year-end 2023 locations
   - `10k-fy2023-part1.txt:227` — "1,530 are located in the United States, 185 are located in the European Union, 30 are located in Australia, and 4 are located in the United Kingdom"
4. Vendor extract shows 1,750
   - `data-provider-extract.txt:4` — "AlphaData,AD-778812,SQUARESPACE INC,SQSP,US,New York,1012.3,1750"

Note: the extract is marked synthetic. Its other Squarespace row (BetaFeed, line 5) leaves employees blank; lines 7 and 8 are other companies (Block, Inc., 13000; Squarespace Technologies Ltd, 60).

## ho-exec-payouts (cross-document: DEF 14A and DEFM14A)

2023 totals come from the "2023 Summary Compensation Table" (`def14a-2024-part1.txt:677`); the table puts each total on its own line under the officer's name. Merger payouts come from "Golden Parachute Compensation" (`defm14a-merger-proxy-part4.txt:648`), "Total" column, one cell per line.

1. Nathan Gooden: $6,804,301 and $25,938,560
   - `def14a-2024-part1.txt:697` — "Nathan Gooden"; `def14a-2024-part1.txt:701` — "6,804,301"
   - `defm14a-merger-proxy-part4.txt:687` — "Nathan Gooden"; `defm14a-merger-proxy-part4.txt:695` — "25,938,560"
2. Paul Gubbay: $2,893,433 and $8,958,816
   - `def14a-2024-part1.txt:703` — "Paul Gubbay"; `def14a-2024-part1.txt:706` — "2,893,433"
   - `defm14a-merger-proxy-part4.txt:696` — "Paul Gubbay"; `defm14a-merger-proxy-part4.txt:704` — "8,958,816"
3. Courtenay O'Connor: $2,845,046 and $8,120,988
   - `def14a-2024-part1.txt:709` — "Courtenay O'Connor"; `def14a-2024-part1.txt:713` — "2,845,046"
   - `defm14a-merger-proxy-part4.txt:705` (name row); `defm14a-merger-proxy-part4.txt:713` — "8,120,988"
4. Anthony Casalena: $552,146 and no payout
   - `def14a-2024-part1.txt:691` — "Anthony Casalena"; `def14a-2024-part1.txt:692` — "Chief Executive Officer | 2023 | 1 | — | — | 552,145"; `def14a-2024-part1.txt:694` — "552,146"
   - `defm14a-merger-proxy-part4.txt:678` — "Anthony Casalena" (lines 680 to 686 are dashes in every column)
   - `defm14a-merger-proxy-part4.txt:649` — "Mr. Casalena is not entitled to any severance payments or benefits upon his termination of employment"

Arithmetic check on the rows: Gooden 750,000 + 200,000 + 5,692,480 + 161,821 = 6,804,301 and 750,000 + 25,156,560 + 32,000 = 25,938,560; Gubbay 325,000 + 8,620,216 + 13,600 = 8,958,816; O'Connor 275,000 + 7,833,188 + 12,800 = 8,120,988.

---

## ho-trap-midyear-subscriptions (trap)

Answer is absent: no file gives unique subscriptions at June 30, 2024 or total bookings for Q2 2024. Greps run with `/usr/bin/grep` over all 29 files:

| Pattern | Result |
|---|---|
| `-c -i "unique subscription" *.txt` | Hits only in the eight 10-K and 10-Q files; 0 in the DEFM14A, 8-Ks, DEF 14A, analyst note and vendor extract |
| `-l -i "subscriptions"` on the DEFM14A, 8-K, DEF 14A, analyst note and vendor extract | No file |
| `-i "subscri"` on the same files | Only "accessed without subscription or cost" (the Delaware code website), 9 matches in the DEFM14A |
| `-c -i "bookings"`, `-c "ARRR"`, `-c -i "run rate"`, `-c "ARPUS"` on `*.txt` | Hits only in the 10-K and 10-Q files |
| `-c -F "June 30, 2024" 10k-*.txt 10q-*.txt` | 0 in every file |
| `"June 30, 2024"` lines filtered by `-i -E "subscri\|bookings\|ARRR\|run rate"` | No line |
| `-c -i "second quarter" *.txt` | Only `defm14a-merger-proxy-part6.txt` (4 hits, all "Second Quarter" row labels in the stock-price tables, lines 739, 762, 785, 808) |
| `-E "Q2"` on the DEFM14A, 8-Ks and 10-Q | No line |
| `-i -E "Unique Subscriptions\|Bookings\|ARRR" defm14a-merger-proxy-part4.txt` (the projections) | No line: the management projections carry no subscription or bookings forecast |

Figures that could be mistaken for the answer:
- The latest reported figures are for March 31, 2024: `10q-q1-2024-part2.txt:31` — "4,912 | 4,264" and `10q-q1-2024-part2.txt:32` — "Total bookings (in thousands) | $ | 325,946 | $ | 265,789".
- The DEFM14A does carry June 30, 2024 data, but only financial and share data (balance sheet lines at `defm14a-merger-proxy-part6.txt` from line 120, "net income of $6,277,000 for the six months ended June 30, 2024" at `defm14a-merger-proxy-part4.txt:180`, share counts). None is an operating metric.
- No projection or estimate of these two metrics exists in the folder.

Document needed, named in the folder: `defm14a-merger-proxy-part7.txt:16` — "Quarterly Reports on Form 10-Q for the quarterly period ended June 30, 2024, filed on August 2, 2024". That filing is not among the 29 files.

## ho-trap-revised-valuation (trap)

Answer is absent: no file contains a Centerview analysis, opinion or value range at the $46.50 price. Greps run with `/usr/bin/grep`:

| Pattern | Result |
|---|---|
| `-c -F "46.5" *.txt` | Only `8k-amended-agreement.txt` (2 lines: 57, 77) and `8k-closing.txt` (2 lines: 60, 98) |
| `-c "Centerview" *.txt` | Only DEFM14A parts 1, 2, 3, 4, 5, 7, 8 and 10; 0 in all three 8-Ks |
| `-c -i -E "fairness\|opinion\|discounted cash flow\|implied" 8k-*.txt analyst-note.txt data-provider-extract.txt` | 0 in every file |
| `-c -E "September (8\|9), 2024" defm14a-merger-proxy-part*.txt` | 0: the proxy (filed August 22, 2024) predates the price increase |
| `-c -E "A&R Merger\|Amended and Restated Agreement and Plan" defm14a-merger-proxy-part*.txt` | 0 |
| `-n "14D-9" *.txt` | Only `8k-amended-agreement.txt` lines 149, 183, 232, 233, 243 |

Figures that could be mistaken for the answer: the ranges from Centerview's May 12, 2024 analysis of the $44.00 price, `defm14a-merger-proxy-part3.txt:216` ("$38.10 to $45.10") and `:222` ("$41.65 to $53.10"). They are the answer to `ho-centerview-valuation`, not to this question. There is no estimate of an updated range anywhere.

Document needed, named in the folder: `8k-amended-agreement.txt:243` — "Company will file with the SEC a Solicitation/Recommendation Statement on Schedule 14D-9". That filing is not among the 29 files. The folder does not say whether Centerview gave a new opinion; the question's premise that it updated its analysis is not confirmed by any document.
