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

## Data Quality & Anomaly Detection

**When to use:** Proactively during initial briefings, or when the user asks about data quality, fake reviews, or suspicious patterns. Use the `find_anomalies` tool and interpret the results through these lenses.

### Rating-Text Mismatches
A 5-star review saying "terrible product, broke on day one" is either a data entry error or deliberate manipulation. In ORM context:
- **Positive rating + negative text:** Often accidental wrong-star selection, but at scale can indicate incentivised reviewers who copy-paste generic text
- **Negative rating + positive text:** Less common. Sometimes a confused user, sometimes a competitor trying to suppress ratings while appearing legitimate

A few mismatches in a large dataset is normal human error. More than 5% warrants a flag.

### Duplicate & Near-Duplicate Reviews
Identical or near-identical review text across multiple entries is one of the strongest astroturfing indicators:
- **Exact duplicates:** Clear data quality issue or review manipulation
- **Same opening / template text:** Suggests coordinated campaign — reviewers given a script
- **Same author, multiple reviews:** Could be legitimate (repeat customer) or sock puppet

Cross-reference with timing — duplicates posted within the same week are much more suspicious than ones months apart.

### Review Volume Spikes
Days with 3x+ the average daily volume may indicate:
- **Organic spike:** Product launch, viral social media mention, seasonal event
- **Review bombing:** Coordinated negative campaign, often visible as a cluster of 1-star reviews in a short window
- **Incentivised campaign:** Burst of positive reviews, often with similar language patterns
- **Product issue:** A defective batch or service outage can trigger a genuine complaint spike

Always check what the spike reviews actually say. The content distinguishes organic from manufactured.

### Suspiciously Short/Long Reviews
- **Very short (< 20 chars):** Low-effort reviews, possibly incentivised ("Great product!"), or placeholder ratings. Low analytical value but not necessarily fake.
- **Very long (3x+ average):** Often genuine power users or deeply frustrated customers. These tend to be the most informative reviews in the dataset — don't dismiss them as outliers.

### How to Report Anomalies
Frame findings carefully. Don't accuse — flag patterns and let the analyst decide:
- "X reviews show rating-text mismatches worth investigating"
- "A cluster of Y similar reviews posted within Z days suggests coordinated activity"
- "Data quality note: N reviews are under 20 characters and may not contribute meaningfully to analysis"
