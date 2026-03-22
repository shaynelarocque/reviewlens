# Analysis Patterns

Templates for common analysis types. Use these as frameworks — adapt to what the data actually shows rather than forcing every analysis into a template.

## Sentiment Trend Analysis

**When to use:** User asks about how sentiment has changed, whether things are improving/declining, or what's different about recent reviews.

**Approach:**
1. Pull rating distribution over time (monthly buckets)
2. Search for recent reviews (last 1-2 months) and older reviews separately
3. Compare aspect mentions and sentiment between periods
4. Look for inflection points — when did the trend change?
5. Quantify: "Average rating dropped from X to Y between [month] and [month], driven primarily by [aspect]"

**Watch out for:** Volume changes masquerading as sentiment changes. If review volume doubled and rating dipped, the new reviewers may have different expectations — that's a different finding than "quality declined."

## Aspect Deep-Dive

**When to use:** User asks about a specific topic (e.g., "What do people say about customer service?")

**Approach:**
1. Search with multiple phrasings (e.g., "customer service", "support", "help", "response time", "staff")
2. Categorise sentiment: positive / negative / mixed / neutral
3. Extract specific sub-aspects (e.g., under "customer service": response time, knowledge, friendliness, resolution)
4. Quantify: counts, percentages of positive vs negative mentions
5. Pull representative quotes — one strong positive, one strong negative, one nuanced/mixed

**Deliverable structure:**
- Overall sentiment ratio for this aspect
- Key sub-themes with sentiment breakdown
- Notable quotes with attribution
- Comparison to overall review sentiment (is this aspect better or worse than the product's average?)

## Churn Signal Detection

**When to use:** User asks about customer retention risks, reasons for dissatisfaction, or "why are people leaving."

**Signals to search for:**
- Explicit churn language: "switching to", "cancelled", "won't buy again", "looking for alternatives", "last time"
- Disappointment trajectory: reviews that start positive then turn ("used to love this but...")
- Unresolved complaints: mentions of contacting support without resolution
- Competitive mentions: naming specific alternatives
- Deal-breakers: strong negative language about a single aspect that outweighs everything else

**Quantify:** What percentage of negative reviews contain churn signals? Which aspects are most associated with churn language?

## Feature Request Extraction

**When to use:** User asks what customers want, what's missing, or what to build/improve next.

**Search terms:** "wish", "would be nice", "should have", "missing", "need", "hope they add", "if only", "compared to [competitor]"

**Categorise by:**
- Frequency (how many reviewers independently request this?)
- Feasibility signal (are they asking for something that exists in the market?)
- Sentiment context (is the missing feature a deal-breaker or a nice-to-have?)

## Rating Distribution Interpretation

**Common patterns and what they mean:**

- **J-curve (skewed high):** Most reviews are 4-5 stars, few in the middle. Normal for products with self-selected reviewers. The 1-star reviews are disproportionately informative.
- **Bimodal (peaks at 1 and 5):** Polarising product. Two distinct customer segments with very different experiences. Investigate what differentiates the groups.
- **Normal distribution (bell curve around 3):** Unusual for reviews. May indicate a commodity product or forced/incentivised reviews.
- **Declining tail (high ratings trending down over time):** Quality or expectation problem developing. Urgent signal.
- **Volume spike with rating dip:** External event (viral post, sale bringing new audience, product change). Investigate timing.

## Competitive Gap Analysis

**When to use:** Reviews mention competitors, or user asks about competitive positioning.

**Approach:**
1. Search for competitor brand names and comparative language ("better than", "worse than", "compared to", "switched from")
2. Map which aspects reviewers compare on
3. Identify where the product wins and loses vs specific competitors
4. Note: reviewers who mention competitors are usually making a deliberate comparison — their insights are high-signal

## Pain Point Mapping

**When to use:** User asks about problems, complaints, or areas for improvement.

**Approach:**
1. Focus on 1-3 star reviews
2. Extract and cluster complaint themes
3. Rank by: frequency, severity (how angry?), recency (getting worse?)
4. For each pain point: specific quotes, count, trend direction, and which customer segment is most affected
5. Distinguish between product issues (fixable) and expectation mismatches (messaging problem)
