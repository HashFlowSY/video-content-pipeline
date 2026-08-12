# 06 -- Aggregate Part-local chapters and summaries

**What to build:** Verified semantic segments aggregate into Part-local chapters
and a transparent collection summary without crossing evidence boundaries.

**Blocked by:** 05 -- Validate evidence-bound segment content.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Aggregate only consecutive verified segments within one Part; chapter
  titles and summaries cite their member segments.
- [ ] Produce collection aggregation from verified segment IDs while retaining
  each Part identity and never creating a continuous cross-Part time range.
- [ ] Report subtitle-unavailable Parts, audio-completeness limitations, source
  language, and Chinese-default prose without inventing text content.
