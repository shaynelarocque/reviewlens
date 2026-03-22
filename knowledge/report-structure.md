# Report Structure

When the user asks for a report or summary, assemble findings into this structure. Not every section is required — include what the conversation has actually covered. An honest short report beats a padded long one.

## Report Sections

### Executive Summary
2-3 paragraphs maximum. Lead with the single most important finding. Include: overall sentiment posture (positive/negative/mixed with numbers), the top 2-3 themes, and one clear recommendation. A busy executive should be able to read only this section and walk away informed.

### Dataset Overview
Brief factual summary of what was analysed:
- Product/entity name and platform
- Total review count and date range
- Average rating and distribution shape (e.g., "skewed positive with a J-curve distribution")
- Any notable data quality issues (missing dates, rating gaps, etc.)

### Key Findings
The core insights, ordered by importance (not by when they were discovered). Each finding should follow this structure:
- **Finding statement** — one clear sentence (e.g., "Shipping complaints increased 40% in Q4")
- **Evidence** — specific numbers and representative quotes from reviews
- **Implication** — what this means for the business

Aim for 3-7 key findings. More than 7 suggests you haven't prioritised.

### Sentiment Breakdown
Aspect-level sentiment analysis. For each major aspect identified:
- Sentiment ratio (positive/negative/neutral %)
- Trend direction (improving, stable, declining)
- Key quotes

A chart here is almost always valuable — stacked bar or horizontal bar showing aspect sentiment distribution.

### Risk Signals
Issues that warrant attention or monitoring. Be specific about severity:
- **High risk:** Active and worsening, significant volume, potential churn driver
- **Medium risk:** Present but stable, or emerging trend with insufficient data to confirm
- **Low risk:** Isolated mentions, but worth monitoring

### Recommendations
Concrete, actionable suggestions tied directly to findings. Each recommendation should reference the finding it addresses. Format:
- **Action** — what to do
- **Rationale** — which finding drives this
- **Priority** — high/medium/low based on impact and urgency

## Citation Standards

Every factual claim in the report must be traceable to actual review data:
- Quote or paraphrase specific reviews when supporting a finding
- Include counts and percentages from statistical analysis
- Reference the search queries used to surface the data
- If a finding is based on sparse data (< 5 reviews), flag it as preliminary

Never state something as a finding if it came from general knowledge rather than the actual review data. If the data is insufficient to support a section, say so and skip it rather than padding with generic advice.

## Formatting

- Use markdown headers and bullet points for scannability
- Bold key numbers and finding statements
- Keep paragraphs short (2-3 sentences max)
- Charts should have clear titles and labels — they may be viewed without surrounding context
- Include a "Data Limitations" note if relevant (e.g., small sample size, date gaps, single-platform data)
