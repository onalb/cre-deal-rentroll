# Gold eval

Two cases. Each start uploads La Cordoba Rent Roll 04-20-21 (2) 164.xlsx, then runs Q1 and Multi-1. The figures in the tables are one example session of 3 runs, so the layout can be read before a live pass.

G, C, and R are the live judge. Accuracy is this page: the answer hit every gold fact. Tool calls is this page: each best-case tool ran once, and no other work tool ran. `score_gcr` is ignored.

## Summary

| Case | Scored | G mean/stdev | C mean/stdev | R mean/stdev | Accuracy | Tool calls |
| --- | --- | --- | --- | --- | --- | --- |
| Q1 | 3/3 | 4.67 ±0.58 | 5.00 ±0.00 | 5.00 ±0.00 | 3/3 | 3/3 |
| Multi-1 | 3/3 | 4.67 ±0.58 | 4.67 ±0.58 | 5.00 ±0.00 | 2/3 | 2/3 |

Accuracy `2/3` means two runs stated every gold fact. Tool calls `2/3` means two runs matched the best-case tool budget.

## Run log

| Case | Run | G | C | R | Accuracy | Tool calls | Time | Tokens |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Q1 | 1 of 3 | 5 | 5 | 5 | pass | 2/2 | 11.2s | in 6100 · out 480 |
| Q1 | 2 of 3 | 5 | 5 | 5 | pass | 2/2 | 12.0s | in 6040 · out 510 |
| Q1 | 3 of 3 | 4 | 5 | 5 | pass | 2/2 | 13.4s | in 6288 · out 540 |
| Multi-1 | 1 of 3 | 5 | 5 | 5 | pass | 2/2 | 16.8s | in 7420 · out 610 |
| Multi-1 | 2 of 3 | 5 | 4 | 5 | miss | 3/2 | 21.5s | in 8104 · out 730 |
| Multi-1 | 3 of 3 | 4 | 5 | 5 | pass | 2/2 | 17.1s | in 7502 · out 640 |

Every log row opens the same record. Run 2 is not a special slot. It is one of the six rows, and it is the one that missed.

## Q1 · run 1

| Field | Record |
| --- | --- |
| Prompt | Abstract this rent roll: unit count, occupancy, in-place monthly income, vacant count, MTM if present. Cite computed totals. Do not geocode or search. |
| Answer | States unit count, occupancy, in-place monthly income, vacant count, and month-to-month count from the roll. |
| Gold answer | unit count 10 · occupancy 90.00% · in-place monthly 1000 · vacant count 1 · month-to-month 2 |
| Plan | Read the roll, then cite the income total. |
| Tools | `query_units` ×1 · `compute_income` ×1 |
| Tool calls | Best case is 2. This run used 2. |
| Gold facts | unit count hit · occupancy hit · in-place monthly hit · vacant count hit · month-to-month hit |
| Accuracy | pass |
| Judge | G 5 · C 5 · R 5 |
| Tokens | in 6100 · out 480 |
| Time | 11.2s |

## Multi-1 · run 2

| Field | Record |
| --- | --- |
| Prompt | Where is this property? Can you put it on a map, and how many units are vacant, and what is occupancy? |
| Answer | States the roll's vacant count and occupancy. Quotes two geocodes. Does not name the street on the roll. |
| Gold answer | vacant count 1 · occupancy 90.00% · place 100 Congress Ave, Austin, TX 78701 |
| Plan | Locate the property, then read vacancy and occupancy from the roll. |
| Tools | `query_units` ×1 · `osm_geocode` ×2 |
| Tool calls | Best case is 2. This run used 3. Extra: `osm_geocode`. |
| Gold facts | vacant count hit · occupancy hit · place miss |
| Accuracy | miss |
| Judge | G 5 · C 4 · R 5 |
| Tokens | in 8104 · out 730 |
| Time | 21.5s |

## Gold responses

The runner fills roll fields from the upload. They are not typed into the case file. Place for Multi-1 is the street or city already on that roll (`suggested_address`).

### Q1

Abstract this rent roll: unit count, occupancy, in-place monthly income, vacant count, MTM if present. Cite computed totals. Do not geocode or search.

| Check | Gold |
| --- | --- |
| Facts from the roll | unit count, occupancy, in-place monthly income, vacant count, month-to-month count |
| Best-case tool calls | `query_units` ×1, `compute_income` ×1 |
| Off the path | `osm_geocode`, `web_search` |

### Multi-1

Where is this property? Can you put it on a map, and how many units are vacant, and what is occupancy?

| Check | Gold |
| --- | --- |
| Facts from the roll | vacant count, occupancy |
| Place | street or city on the roll |
| Best-case tool calls | `query_units` ×1, `osm_geocode` ×1 |
| Off the path | `web_search`, `compute_income`, `compute_noi`, `compute_cap_rate`, `ask_prediction_agent` |
