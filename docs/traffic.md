# Repository traffic history

## Lifetime accumulation

The source API exposes a rolling 14-day window. Each collection merges those
daily observations into permanent `state.json` records in the public Gist.
Neither the 14-day API snapshot nor a 31-day display window replaces saved
history. Daily history is no longer trimmed.

- Dates are UTC dates, matching GitHub Traffic.
- Each date is counted once. Repeated collection of the same snapshot does
  not increase the total; late increases update the matching daily record.
- Missing API rows and endpoint failures preserve existing observations.
  They do not establish that the number of clones was zero.
- An explicit API row with `count=0` is a reported zero. An unreported date
  has `clones=null`, `clonesStatus="pending"`, and a gap/shaded band on the
  curve. Positive legacy daily counts remain usable.
- A lower snapshot does not erase a higher previously recorded daily value.
  This protects the saved history from stale responses; counts follow a
  maximum-observed-value policy rather than applying downward revisions.
- If an older installation already removed daily records while preserving
  a larger lifetime total, `historyOffsets` retains that earlier accumulated
  amount. The chart identifies this offset; it cannot reconstruct missing
  daily detail. Existing archives are still useful for recovering that detail.
- If the configured Gist cannot be read, its state is missing/invalid, or
  the API reports truncated content, collection fails before updating it.
  It does not replace unavailable history with an empty state.

The collector stores separate retrieval times for clones and views and the
latest API-reported date for each. Metadata collection on today's date is
not evidence that today's clone data has been reported.

## Metric definitions

The curve uses **raw clone events**, including automation:

1. Bars: daily clone counts reported by GitHub.
2. Line: cumulative clone counts from all saved dates, plus any preserved
   earlier-history offset. Unknown dates are not plotted as zero.

It does not label a sum of daily unique cloners as a lifetime count of people.
The same person can appear on different days, and the traffic API does not
provide identities for deduplicating those daily aggregates across all history.
Legacy daily unique fields are retained for compatibility.

The existing clone/install badges use estimated organic counts after subtracting
detected CI checkouts. Those estimates are separate from the raw counts on the
curve and are not exact numbers of users or installations. Daily estimated
organic values are recomputed when delayed traffic arrives.

## Refresh and rendering

The workflow runs at minute 17 every three hours (UTC), also supports manual
dispatch, and refreshes after pushes to main subject to its one-hour freshness
guard. Scheduled execution can be delayed by GitHub Actions.

```sh
gh workflow run traffic-badges.yml --repo Lavaxzyh/ads-mcp --ref main
```

Runs are serialized without cancelling an in-progress update. Rendering reads
the exact state snapshot written by that collection, avoiding a second fetch
of a potentially cached raw Gist URL. PNGs are published through the Gist's
Git remote. GitHub's image proxy and badge service may still cache published
assets, so the collection time on the curve helps identify stale display.

For local rendering of a saved snapshot:

```sh
python tools/plot_clones_history.py state.json clones-history-preview.png
```

The optional monthly archive contains the selected month's daily records.
The main state retains all daily records regardless of whether monthly
archiving is configured.
